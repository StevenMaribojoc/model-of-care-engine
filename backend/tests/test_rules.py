"""Targeted tests for the rules that are easy to get subtly wrong.

Not chasing coverage. Each test pins down a boundary, an ordering decision, or a
missing-data case where a plausible alternative implementation would silently
produce different clinical output. Several exist because they caught a real bug or
would have.

These run against the *real* config in backend/config, so a careless edit to
programs.yaml fails the suite rather than quietly changing who gets care.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.models import (
    Diagnosis,
    Encounter,
    LabResult,
    Need,
    NeedStatus,
    PatientRecord,
    Role,
    TaskType,
)
from app.engine.run import evaluate
from app.engine.tasks import build_tasks
from app.rules.loader import get_rules
from app.rules.predicates import _code_matches

AS_OF = date(2026, 4, 8)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def rules():
    return get_rules()


def patient(
    patient_id: str = "PTEST",
    *,
    dob: date | None = date(1980, 1, 1),
    diagnoses: tuple[tuple[str, date | None], ...] = (),
    labs: tuple[tuple[str, float, date], ...] = (),
    encounters: tuple[tuple[str, date], ...] = (),
) -> PatientRecord:
    return PatientRecord(
        patient_id=patient_id,
        first_name="Test",
        last_name="Patient",
        date_of_birth=dob,
        gender="F",
        phone=None,
        language="English",
        pcp_provider_name=None,
        diagnoses=tuple(Diagnosis(code, "", dt) for code, dt in diagnoses),
        labs=tuple(LabResult(name, value, dt) for name, value, dt in labs),
        encounters=tuple(Encounter(spec, dt, None) for spec, dt in encounters),
    )


def born_age(years: int, as_of: date = AS_OF) -> date:
    """A date of birth that makes the patient exactly `years` old on as_of."""
    return date(as_of.year - years, as_of.month, as_of.day)


def run(record: PatientRecord, rules, as_of: date = AS_OF):
    return evaluate([record], rules, as_of)


def tier_of(result, program_code: str) -> str | None:
    for enrollment in result.enrollments:
        if enrollment.program_code == program_code:
            return enrollment.tier_code
    return None


def programs_of(result) -> set[str]:
    return {e.program_code for e in result.enrollments}


def need_for(result, target: str) -> Need | None:
    for need in result.needs:
        if need.target == target:
            return need
    return None


# --------------------------------------------------------------------------- #
# Eligibility boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "age,eligible",
    [(17, False), (18, True), (19, True)],
    ids=["17-excluded", "18-included", "19-included"],
)
def test_wellness_age_floor_is_inclusive_at_18(rules, age, eligible):
    """'Aged 18 and older' includes the patient on their 18th birthday."""
    result = run(patient(dob=born_age(age)), rules)
    assert ("PCP_WELLNESS" in programs_of(result)) is eligible


def test_unknown_birthday_fails_the_age_floor_but_not_diabetes(rules):
    """Missing data must not promote a patient into a program by accident.

    An unknown age cannot satisfy 'aged 18 and older', so wellness is skipped.
    Diabetes has no age criterion, so the patient is still managed there -- the
    gap in demographics does not cost them specialist care.
    """
    record = patient(dob=None, diagnoses=(("E11.9", date(2024, 1, 1)),))
    result = run(record, rules)
    assert programs_of(result) == {"DIABETES_MGMT"}


def test_diabetic_minor_is_managed_but_not_enrolled_in_wellness(rules):
    """Patients can be in one program and not another; the spec sets no age floor
    on diabetes, so a 12-year-old with Type 1 is still stratified."""
    record = patient(dob=born_age(12), diagnoses=(("E10.9", date(2024, 1, 1)),))
    result = run(record, rules)
    assert programs_of(result) == {"DIABETES_MGMT"}
    assert tier_of(result, "DIABETES_MGMT") == "UNMONITORED"


# --------------------------------------------------------------------------- #
# Stratification boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "age,expected",
    [(64, "STANDARD"), (65, "HIGH_PRIORITY"), (66, "HIGH_PRIORITY")],
)
def test_wellness_high_priority_age_threshold(rules, age, expected):
    result = run(patient(dob=born_age(age)), rules)
    assert tier_of(result, "PCP_WELLNESS") == expected


def test_sleep_apnea_subcode_counts_as_chronic(rules):
    """Regression: G47.3 must match G47.33.

    The spec lists 'G47.3x -- Sleep Apnea' in the chronic conditions group. An
    ICD prefix matcher that insists on stopping at a code boundary rejects
    G47.33 and silently under-stratifies the patient to Standard, halving how
    often they are seen. Caught by cross-checking the engine against an
    independent implementation; P0139 in the real dataset is exactly this case.
    """
    record = patient(dob=born_age(40), diagnoses=(("G47.33", date(2025, 10, 8)),))
    assert tier_of(run(record, rules), "PCP_WELLNESS") == "HIGH_PRIORITY"


def test_bare_category_prefix_does_not_match_a_longer_category():
    """The other half of the same rule: I10 must not match I100.

    Tested at the matcher rather than through a patient because no such code
    appears in the dataset -- but a naive startswith would pass the test above
    and fail this one.
    """
    assert _code_matches("I10.9", "I10")
    assert _code_matches("I10", "I10")
    assert not _code_matches("I100", "I10")


@pytest.mark.parametrize(
    "a1c,expected",
    [
        (9.0, "HIGH_RISK"),  # inclusive lower bound
        (8.9, "MODERATE_RISK"),
        (7.0, "MODERATE_RISK"),  # inclusive lower bound
        (6.9, "LOW_RISK"),
    ],
)
def test_a1c_tier_thresholds_are_inclusive_below(rules, a1c, expected):
    """No A1C in the dataset sits exactly on 7.0 or 9.0, so the boundary is
    unobservable from the data and has to be pinned by test."""
    record = patient(
        diagnoses=(("E11.9", date(2024, 1, 1)),),
        labs=(("HbA1c", a1c, date(2026, 3, 1)),),
    )
    assert tier_of(run(record, rules), "DIABETES_MGMT") == expected


def test_a1c_exactly_six_months_old_is_outside_the_window(rules):
    """'Within the last 6 months' is read as a half-open window.

    A result dated exactly six months back is not within the last six months, so
    the patient is Unmonitored. One day later, it counts. Arbitrary either way --
    what matters is that it is decided once, documented, and cannot drift.
    """
    from dateutil.relativedelta import relativedelta

    window_start = AS_OF - relativedelta(months=6)
    diabetes = (("E11.9", date(2024, 1, 1)),)

    on_edge = patient(diagnoses=diabetes, labs=(("HbA1c", 6.0, window_start),))
    assert tier_of(run(on_edge, rules), "DIABETES_MGMT") == "UNMONITORED"

    inside = patient(
        diagnoses=diabetes, labs=(("HbA1c", 6.0, window_start + timedelta(days=1)),)
    )
    assert tier_of(run(inside, rules), "DIABETES_MGMT") == "LOW_RISK"


def test_labs_dated_after_as_of_are_invisible(rules):
    """Time travel must not leak future facts backwards.

    Evaluating at a past date has to reproduce what was knowable then, otherwise
    re-running history produces answers that were never true.
    """
    record = patient(
        diagnoses=(("E11.9", date(2024, 1, 1)),),
        labs=(("HbA1c", 11.0, AS_OF + timedelta(days=30)),),
    )
    assert tier_of(run(record, rules), "DIABETES_MGMT") == "UNMONITORED"


def test_most_recent_a1c_wins_over_the_worst_one(rules):
    """Stratification follows the latest result, not the highest."""
    record = patient(
        diagnoses=(("E11.9", date(2024, 1, 1)),),
        labs=(
            ("HbA1c", 12.0, date(2025, 11, 1)),
            ("HbA1c", 6.2, date(2026, 3, 1)),
        ),
    )
    assert tier_of(run(record, rules), "DIABETES_MGMT") == "LOW_RISK"


# --------------------------------------------------------------------------- #
# Need status and cadence
# --------------------------------------------------------------------------- #


def test_cadence_boundary_is_strictly_greater(rules):
    """Due on the due date, overdue the day after.

    Standard wellness is a 365-day cadence: a visit exactly 365 days ago is still
    satisfied; 366 days ago is not. Off-by-one here moves thousands of patients
    on or off a worklist.
    """
    exactly_due = patient(encounters=(("PCP", AS_OF - timedelta(days=365)),))
    assert need_for(run(exactly_due, rules), "PCP").status is NeedStatus.SATISFIED

    one_day_over = patient(encounters=(("PCP", AS_OF - timedelta(days=366)),))
    assert need_for(run(one_day_over, rules), "PCP").status is NeedStatus.DUE


def test_upcoming_appointment_suppresses_an_overdue_task(rules):
    """The spec is explicit: no task when an upcoming encounter exists.

    Taken literally, so a patient years overdue with an appointment booked far in
    the future is still SCHEDULED and generates nothing. Deliberate, and flagged
    in the README -- production would compare the appointment date against the
    due date and keep the task if the appointment lands too late.
    """
    record = patient(
        encounters=(
            ("PCP", AS_OF - timedelta(days=900)),
            ("PCP", AS_OF + timedelta(days=120)),
        )
    )
    result = run(record, rules)
    need = need_for(result, "PCP")
    assert need.status is NeedStatus.SCHEDULED
    assert result.tasks == ()


def test_no_pcp_history_records_the_gap_but_generates_no_task(rules):
    """The patient must stay visible even though nobody is assigned work.

    Referral tasks apply only to specialists, so a patient never seen by primary
    care produces no task. Dropping the need entirely would be the easy
    implementation and the wrong one: 72 real patients are in this state, and
    they are exactly the population that falls through the cracks today.
    """
    record = patient(encounters=(("Endocrinology", AS_OF - timedelta(days=30)),))
    result = run(record, rules)
    need = need_for(result, "PCP")
    assert need.status is NeedStatus.NEVER_SEEN
    assert need.task_type is None
    assert need.is_actionable and not need.generates_task
    assert result.tasks == ()


def test_specialist_never_seen_produces_a_referral(rules):
    record = patient(
        diagnoses=(("E11.9", date(2024, 1, 1)),),
        labs=(("HbA1c", 9.5, date(2026, 3, 1)),),
    )
    result = run(record, rules)
    endo = [t for t in result.tasks if t.target == "Endocrinology"]
    assert len(endo) == 1
    assert endo[0].task_type is TaskType.REFERRAL
    assert endo[0].due_date == AS_OF  # no prior visit to measure a cadence from


def test_seen_before_produces_scheduling_not_referral(rules):
    record = patient(
        diagnoses=(("E11.9", date(2024, 1, 1)),),
        labs=(("HbA1c", 9.5, date(2026, 3, 1)),),
        encounters=(("Endocrinology", AS_OF - timedelta(days=400)),),
    )
    result = run(record, rules)
    endo = [t for t in result.tasks if t.target == "Endocrinology"]
    assert endo[0].task_type is TaskType.SCHEDULING


# --------------------------------------------------------------------------- #
# Task merging across programs
# --------------------------------------------------------------------------- #


def _need(program: str, target: str, priority: int, due: date, **kwargs) -> Need:
    return Need(
        patient_id="P1",
        program_code=program,
        tier_code="T",
        need_type="visit",
        target=target,
        cadence_days=90,
        status=NeedStatus.DUE,
        task_type=TaskType.SCHEDULING,
        due_date=due,
        priority=priority,
        **kwargs,
    )


def test_two_programs_wanting_the_same_visit_produce_one_task():
    """Merging is the difference between one phone call and two.

    The current two programs target disjoint specialties, so the real dataset
    never exercises this. It becomes live the moment a third program overlaps,
    which is the whole point of building it now.
    """
    needs = [
        _need("DIABETES_MGMT", "Cardiology", priority=1, due=date(2026, 1, 10)),
        _need("CARDIAC_RISK", "Cardiology", priority=3, due=date(2025, 11, 20)),
    ]
    tasks = build_tasks(needs)

    assert len(tasks) == 1
    task = tasks[0]
    # Strictest wins on both axes independently: the most urgent program sets
    # priority, the earliest deadline sets the due date.
    assert task.priority == 1
    assert task.due_date == date(2025, 11, 20)
    assert task.program_codes == ("CARDIAC_RISK", "DIABETES_MGMT")
    # Both needs stay linked, so closing the task can report both gaps closed.
    assert len(task.need_keys) == 2


def test_different_targets_are_not_merged():
    needs = [
        _need("DIABETES_MGMT", "Cardiology", priority=1, due=date(2026, 1, 10)),
        _need("DIABETES_MGMT", "Podiatry", priority=1, due=date(2026, 1, 10)),
    ]
    assert len(build_tasks(needs)) == 2


def test_referral_outranks_scheduling_when_a_group_disagrees():
    """Defensive: booking without clinical approval is the worse error."""
    needs = [
        _need("A", "Cardiology", priority=2, due=date(2026, 1, 1)),
        _need("B", "Cardiology", priority=2, due=date(2026, 1, 1)),
    ]
    needs[1] = Need(**{**needs[1].__dict__, "task_type": TaskType.REFERRAL})
    assert build_tasks(needs)[0].task_type is TaskType.REFERRAL


def test_worklist_order_is_deterministic_and_urgency_first():
    needs = [
        _need("A", "Podiatry", priority=3, due=date(2026, 1, 1)),
        _need("A", "Cardiology", priority=1, due=date(2026, 2, 1)),
        _need("A", "Nephrology", priority=1, due=date(2025, 1, 1)),
    ]
    order = [(t.priority, t.target) for t in build_tasks(needs)]
    assert order == [(1, "Nephrology"), (1, "Cardiology"), (3, "Podiatry")]


# --------------------------------------------------------------------------- #
# Whole-run properties
# --------------------------------------------------------------------------- #


def test_role_visibility_comes_from_configuration(rules):
    assert rules.visible_task_types(Role.SCHEDULER) == (TaskType.SCHEDULING,)
    assert set(rules.visible_task_types(Role.CLINICAL)) == {
        TaskType.SCHEDULING,
        TaskType.REFERRAL,
    }


def test_pcp_never_generates_a_referral_anywhere(rules):
    """Property check over a small synthetic population, not a single patient."""
    population = [
        patient(f"P{i}", dob=born_age(30 + i), encounters=()) for i in range(10)
    ]
    result = evaluate(population, rules, AS_OF)
    assert not [
        t for t in result.tasks if t.target == "PCP" and t.task_type is TaskType.REFERRAL
    ]


def test_evaluation_is_deterministic(rules):
    """Same inputs, same output -- including ordering.

    A worklist that reshuffles between identical runs is one staff stop trusting,
    and non-determinism here would also make caching results by run unsound.
    """
    record = patient(
        diagnoses=(("E11.65", date(2024, 1, 1)), ("I10", date(2023, 5, 1))),
        labs=(("HbA1c", 9.4, date(2026, 2, 1)),),
        encounters=(("PCP", AS_OF - timedelta(days=500)),),
    )
    first = evaluate([record], rules, AS_OF)
    second = evaluate([record], rules, AS_OF)
    assert first.tasks == second.tasks
    assert first.needs == second.needs
