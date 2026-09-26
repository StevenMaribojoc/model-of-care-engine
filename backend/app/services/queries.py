"""Filtered reads over a run, with role visibility enforced here and nowhere else.

The single most important rule in this module: **the caller never decides what it
is allowed to see.** Every function that returns tasks takes a role and intersects
the requested filters with what that role may view. A scheduler who asks for
referral tasks gets an empty list, not an error and not the tasks. The frontend
filters for convenience; this filters for authorisation, and only this one counts.

The ``role`` parameter is a stand-in for a claim that would arrive on a verified
token (Entra ID, in ecares' stack). Swapping it is a change to how the role is
*obtained*, not to how it is *enforced* -- which is the property worth having.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.models import NeedStatus, Role
from app.rules.schema import RulesBundle
from app.services.views import (
    ClinicalHistoryView,
    EnrollmentView,
    NeedView,
    Page,
    PatientDetailView,
    PatientSummary,
    PatientView,
    RunInfo,
    SummaryView,
    TaskView,
)

# Statuses that represent an open care gap.
ACTIONABLE_STATUSES = (NeedStatus.DUE.value, NeedStatus.NEVER_SEEN.value)


# --------------------------------------------------------------------------- #
# Role visibility
# --------------------------------------------------------------------------- #


def visible_task_types(
    rules: RulesBundle, role: Role, requested: str | None = None
) -> list[str]:
    """Task types this role may see, narrowed by an optional requested filter.

    Returning an empty list is a meaningful answer: it means the role asked for
    something it cannot see, and the caller should return no rows.
    """
    allowed = [t.value for t in rules.visible_task_types(role)]
    if requested is None:
        return allowed
    return [t for t in allowed if t == requested]


# --------------------------------------------------------------------------- #
# Run metadata
# --------------------------------------------------------------------------- #


def get_run_info(session: Session, run_id: int) -> RunInfo:
    row = session.get(db.EngineRun, run_id)
    return RunInfo(
        run_id=row.run_id,
        as_of=row.as_of_date,
        rules_version=row.rules_version,
        patient_count=row.patient_count,
        duration_ms=row.duration_ms,
        warnings=list(row.warnings or []),
    )


def get_meta(session: Session, rules: RulesBundle, default_as_of: date) -> dict:
    """Everything the frontend needs to build its filter controls.

    Deliberately derived from configuration rather than hardcoded, so a new
    program, tier, or specialty appears in the UI with no frontend change. That
    is the difference between "adding a program is a YAML edit" being true and
    being nearly true.
    """
    programs = []
    for program in rules.programs:
        programs.append(
            {
                "code": program.code,
                "name": program.name,
                "description": program.description,
                "is_active": program.is_active,
                "tiers": [
                    {
                        "code": tier.code,
                        "name": tier.name,
                        "priority": tier.priority,
                        "program_code": program.code,
                        "needs": [
                            {
                                "need_type": need.need_type,
                                "target": need.target,
                                "cadence_days": need.cadence_days,
                                "note": need.note,
                            }
                            for need in tier.needs
                        ],
                    }
                    for tier in program.tiers
                ],
            }
        )

    return {
        "default_as_of": default_as_of,
        "rules_version": rules.version,
        "programs": programs,
        "specialties": [
            {
                "code": s.code,
                "name": s.name,
                "requires_referral": s.requires_referral,
            }
            for s in rules.reference.specialties
        ],
        "need_statuses": [status.value for status in NeedStatus],
        "roles": [
            {
                "code": entry.role.value,
                "name": entry.name,
                "task_types": [t.value for t in entry.task_types],
            }
            for entry in rules.reference.role_visibility
        ],
    }


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


def list_tasks(
    session: Session,
    *,
    run_id: int,
    as_of: date,
    rules: RulesBundle,
    role: Role,
    specialty: str | None = None,
    task_type: str | None = None,
    program: str | None = None,
    tier: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Page:
    allowed = visible_task_types(rules, role, task_type)
    if not allowed:
        # The role asked for a task type it may not see. An empty page is the
        # correct answer: not an error, and certainly not the rows.
        return Page(total=0, limit=limit, offset=offset, items=[])

    query = (
        select(db.Task, db.Patient)
        .join(db.Patient, db.Patient.patient_id == db.Task.patient_id)
        .where(db.Task.run_id == run_id, db.Task.task_type.in_(allowed))
    )
    query = _apply_task_filters(
        query, run_id=run_id, specialty=specialty, program=program, tier=tier,
        search=search,
    )

    total = session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )

    rows = session.execute(
        query.order_by(db.Task.priority, db.Task.due_date, db.Task.patient_id)
        .limit(limit)
        .offset(offset)
    ).all()

    task_ids = [task.id for task, _ in rows]
    programs_by_task = _programs_for_tasks(session, task_ids)

    items = [
        _to_task_view(task, patient, as_of, programs_by_task.get(task.id, []))
        for task, patient in rows
    ]
    return Page(total=total or 0, limit=limit, offset=offset, items=items)


def _apply_task_filters(
    query: Select,
    *,
    run_id: int,
    specialty: str | None,
    program: str | None,
    tier: str | None,
    search: str | None,
) -> Select:
    if specialty:
        query = query.where(db.Task.target == specialty)
    if search:
        query = query.where(_name_matches(search))
    if program or tier:
        # Program and tier live on the enrollment behind the task's needs, so this
        # is an EXISTS through the link table rather than a join -- a task merged
        # from two programs must appear once under either filter, not twice.
        link = (
            select(db.TaskNeed.task_id)
            .join(db.ClinicalNeed, db.ClinicalNeed.id == db.TaskNeed.need_id)
            .join(db.Enrollment, db.Enrollment.id == db.ClinicalNeed.enrollment_id)
            .where(db.TaskNeed.task_id == db.Task.id, db.Enrollment.run_id == run_id)
        )
        if program:
            link = link.join(
                db.Program, db.Program.program_id == db.Enrollment.program_id
            ).where(db.Program.code == program)
        if tier:
            link = link.join(
                db.RiskTier, db.RiskTier.tier_id == db.Enrollment.tier_id
            ).where(db.RiskTier.code == tier)
        query = query.where(link.exists())
    return query


def _programs_for_tasks(session: Session, task_ids: list[int]) -> dict[int, list[str]]:
    """One query for the whole page rather than one per task."""
    if not task_ids:
        return {}
    rows = session.execute(
        select(db.TaskNeed.task_id, db.Program.code)
        .join(db.ClinicalNeed, db.ClinicalNeed.id == db.TaskNeed.need_id)
        .join(db.Enrollment, db.Enrollment.id == db.ClinicalNeed.enrollment_id)
        .join(db.Program, db.Program.program_id == db.Enrollment.program_id)
        .where(db.TaskNeed.task_id.in_(task_ids))
        .distinct()
    ).all()
    grouped: dict[int, list[str]] = defaultdict(list)
    for task_id, code in rows:
        grouped[task_id].append(code)
    return {task_id: sorted(codes) for task_id, codes in grouped.items()}


# --------------------------------------------------------------------------- #
# Patients
# --------------------------------------------------------------------------- #


def list_patients(
    session: Session,
    *,
    run_id: int,
    as_of: date,
    rules: RulesBundle,
    role: Role,
    specialty: str | None = None,
    task_type: str | None = None,
    program: str | None = None,
    tier: str | None = None,
    need_status: str | None = None,
    search: str | None = None,
    with_tasks_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Page:
    """Patient-centric list.

    ``specialty`` means "has an open gap for this specialty", not "has any need
    for it" -- a patient whose Endocrinology visit is up to date is not who you
    mean when you ask to see everyone who needs endocrinology.
    """
    allowed = visible_task_types(rules, role, task_type)

    query = select(db.Patient)

    if search:
        query = query.where(_name_matches(search))

    if specialty or need_status:
        gap = select(db.ClinicalNeed.id).where(
            db.ClinicalNeed.patient_id == db.Patient.patient_id,
            db.ClinicalNeed.run_id == run_id,
        )
        if specialty:
            gap = gap.where(db.ClinicalNeed.target == specialty)
        # Without an explicit status filter, "needs X" means an open gap for X.
        gap = gap.where(
            db.ClinicalNeed.status == need_status
            if need_status
            else db.ClinicalNeed.status.in_(ACTIONABLE_STATUSES)
        )
        query = query.where(gap.exists())

    if program or tier:
        enrolled = select(db.Enrollment.id).where(
            db.Enrollment.patient_id == db.Patient.patient_id,
            db.Enrollment.run_id == run_id,
        )
        if program:
            enrolled = enrolled.join(
                db.Program, db.Program.program_id == db.Enrollment.program_id
            ).where(db.Program.code == program)
        if tier:
            enrolled = enrolled.join(
                db.RiskTier, db.RiskTier.tier_id == db.Enrollment.tier_id
            ).where(db.RiskTier.code == tier)
        query = query.where(enrolled.exists())

    if task_type or with_tasks_only:
        if not allowed:
            return Page(total=0, limit=limit, offset=offset, items=[])
        has_task = select(db.Task.id).where(
            db.Task.patient_id == db.Patient.patient_id,
            db.Task.run_id == run_id,
            db.Task.task_type.in_(allowed),
        )
        if specialty:
            has_task = has_task.where(db.Task.target == specialty)
        query = query.where(has_task.exists())

    total = session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    )

    patients = list(
        session.scalars(
            query.order_by(db.Patient.patient_id).limit(limit).offset(offset)
        )
    )
    patient_ids = [p.patient_id for p in patients]

    enrollments = _enrollments_for(session, run_id, patient_ids)
    needs = _needs_for(session, run_id, patient_ids, as_of)
    tasks = _tasks_for(session, run_id, patient_ids, allowed, as_of)

    items = [
        PatientView(
            patient=_to_patient_summary(patient, as_of),
            enrollments=enrollments.get(patient.patient_id, []),
            needs=needs.get(patient.patient_id, []),
            tasks=tasks.get(patient.patient_id, []),
        )
        for patient in patients
    ]
    return Page(total=total or 0, limit=limit, offset=offset, items=items)


def get_patient(
    session: Session,
    *,
    run_id: int,
    as_of: date,
    rules: RulesBundle,
    role: Role,
    patient_id: str,
) -> PatientDetailView | None:
    patient = session.get(db.Patient, patient_id)
    if patient is None:
        return None

    allowed = visible_task_types(rules, role)
    ids = [patient_id]

    diagnoses = session.scalars(
        select(db.Diagnosis)
        .where(db.Diagnosis.patient_id == patient_id)
        .order_by(db.Diagnosis.diagnosed_date.desc())
    ).all()
    labs = session.scalars(
        select(db.LabResult)
        .where(db.LabResult.patient_id == patient_id)
        .order_by(db.LabResult.result_date.desc())
    ).all()
    encounters = session.scalars(
        select(db.Encounter)
        .where(db.Encounter.patient_id == patient_id)
        .order_by(db.Encounter.encounter_date.desc())
    ).all()

    return PatientDetailView(
        patient=_to_patient_summary(patient, as_of),
        enrollments=_enrollments_for(session, run_id, ids).get(patient_id, []),
        needs=_needs_for(session, run_id, ids, as_of).get(patient_id, []),
        tasks=_tasks_for(session, run_id, ids, allowed, as_of).get(patient_id, []),
        history=ClinicalHistoryView(
            diagnoses=[
                {
                    "icd_code": d.icd_code,
                    "description": d.description,
                    "diagnosed_date": d.diagnosed_date,
                }
                for d in diagnoses
            ],
            labs=[
                {
                    "test_name": lab.test_name,
                    "result_value": lab.result_value,
                    "result_date": lab.result_date,
                    # Flagged rather than filtered: seeing that a result exists but
                    # was not counted is exactly what makes a tier explainable.
                    "after_as_of": lab.result_date > as_of,
                }
                for lab in labs
            ],
            encounters=[
                {
                    "specialty": e.specialty,
                    "encounter_date": e.encounter_date,
                    "provider_name": e.provider_name,
                    "upcoming": e.encounter_date > as_of,
                }
                for e in encounters
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


def get_summary(
    session: Session, *, run_id: int, rules: RulesBundle, role: Role
) -> SummaryView:
    allowed = visible_task_types(rules, role)

    tasks_by_type = dict(
        session.execute(
            select(db.Task.task_type, func.count())
            .where(db.Task.run_id == run_id, db.Task.task_type.in_(allowed))
            .group_by(db.Task.task_type)
        ).all()
    )
    tasks_by_specialty = dict(
        session.execute(
            select(db.Task.target, func.count())
            .where(db.Task.run_id == run_id, db.Task.task_type.in_(allowed))
            .group_by(db.Task.target)
        ).all()
    )
    enrollments_by_tier = dict(
        session.execute(
            select(db.RiskTier.code, func.count())
            .join(db.Enrollment, db.Enrollment.tier_id == db.RiskTier.tier_id)
            .where(db.Enrollment.run_id == run_id)
            .group_by(db.RiskTier.code)
        ).all()
    )
    needs_by_status = dict(
        session.execute(
            select(db.ClinicalNeed.status, func.count())
            .where(db.ClinicalNeed.run_id == run_id)
            .group_by(db.ClinicalNeed.status)
        ).all()
    )
    patients_with_tasks = session.scalar(
        select(func.count(func.distinct(db.Task.patient_id))).where(
            db.Task.run_id == run_id, db.Task.task_type.in_(allowed)
        )
    )
    # Open gaps that are linked to no task at all: the no-PCP-history population.
    # Worth a number of its own, because it is invisible in any task count and is
    # precisely who falls through the cracks today.
    unactionable = session.scalar(
        select(func.count())
        .select_from(db.ClinicalNeed)
        .where(
            db.ClinicalNeed.run_id == run_id,
            db.ClinicalNeed.status.in_(ACTIONABLE_STATUSES),
            ~select(db.TaskNeed.id)
            .where(db.TaskNeed.need_id == db.ClinicalNeed.id)
            .exists(),
        )
    )

    return SummaryView(
        run=get_run_info(session, run_id),
        role=role.value,
        total_patients=session.scalar(select(func.count()).select_from(db.Patient)) or 0,
        patients_with_tasks=patients_with_tasks or 0,
        tasks_by_type=tasks_by_type,
        tasks_by_specialty=tasks_by_specialty,
        enrollments_by_tier=enrollments_by_tier,
        needs_by_status=needs_by_status,
        unactionable_gaps=unactionable or 0,
    )


# --------------------------------------------------------------------------- #
# Row -> view helpers
# --------------------------------------------------------------------------- #


def _name_matches(search: str):
    pattern = f"%{search.strip()}%"
    return (
        db.Patient.patient_id.ilike(pattern)
        | db.Patient.first_name.ilike(pattern)
        | db.Patient.last_name.ilike(pattern)
    )


def _days_overdue(due_date: date | None, as_of: date) -> int | None:
    if due_date is None:
        return None
    delta = (as_of - due_date).days
    return delta if delta > 0 else None


def _age(dob: date | None, as_of: date) -> int | None:
    if dob is None or dob > as_of:
        return None
    return (
        as_of.year
        - dob.year
        - ((as_of.month, as_of.day) < (dob.month, dob.day))
    )


def _to_patient_summary(patient: db.Patient, as_of: date) -> PatientSummary:
    return PatientSummary(
        patient_id=patient.patient_id,
        name=f"{patient.first_name} {patient.last_name}".strip(),
        age=_age(patient.date_of_birth, as_of),
        gender=patient.gender,
        language=patient.language,
        phone=patient.phone,
        pcp_provider_name=patient.pcp_provider_name,
    )


def _to_task_view(
    task: db.Task, patient: db.Patient, as_of: date, program_codes: list[str]
) -> TaskView:
    return TaskView(
        task_id=task.id,
        patient_id=task.patient_id,
        patient_name=f"{patient.first_name} {patient.last_name}".strip(),
        task_type=task.task_type,
        need_type=task.need_type,
        target=task.target,
        priority=task.priority,
        due_date=task.due_date,
        days_overdue=_days_overdue(task.due_date, as_of),
        status=task.status,
        program_codes=program_codes,
        patient=_to_patient_summary(patient, as_of),
    )


def _enrollments_for(
    session: Session, run_id: int, patient_ids: list[str]
) -> dict[str, list[EnrollmentView]]:
    if not patient_ids:
        return {}
    rows = session.execute(
        select(db.Enrollment, db.Program, db.RiskTier)
        .join(db.Program, db.Program.program_id == db.Enrollment.program_id)
        .outerjoin(db.RiskTier, db.RiskTier.tier_id == db.Enrollment.tier_id)
        .where(
            db.Enrollment.run_id == run_id,
            db.Enrollment.patient_id.in_(patient_ids),
        )
        .order_by(db.Program.code)
    ).all()
    grouped: dict[str, list[EnrollmentView]] = defaultdict(list)
    for enrollment, program, tier in rows:
        grouped[enrollment.patient_id].append(
            EnrollmentView(
                program_code=program.code,
                program_name=program.name,
                tier_code=tier.code if tier else None,
                tier_name=tier.name if tier else None,
                tier_priority=tier.priority if tier else None,
                evidence=enrollment.evidence or {},
            )
        )
    return grouped


def _needs_for(
    session: Session, run_id: int, patient_ids: list[str], as_of: date
) -> dict[str, list[NeedView]]:
    if not patient_ids:
        return {}
    rows = session.execute(
        select(db.ClinicalNeed, db.Program.code)
        .join(db.Enrollment, db.Enrollment.id == db.ClinicalNeed.enrollment_id)
        .join(db.Program, db.Program.program_id == db.Enrollment.program_id)
        .where(
            db.ClinicalNeed.run_id == run_id,
            db.ClinicalNeed.patient_id.in_(patient_ids),
        )
        .order_by(db.ClinicalNeed.priority, db.ClinicalNeed.target)
    ).all()
    grouped: dict[str, list[NeedView]] = defaultdict(list)
    for need, program_code in rows:
        grouped[need.patient_id].append(
            NeedView(
                program_code=program_code,
                need_type=need.need_type,
                target=need.target,
                cadence_days=need.cadence_days,
                status=need.status,
                last_completed_date=need.last_completed_date,
                next_scheduled_date=need.next_scheduled_date,
                due_date=need.due_date,
                days_overdue=_days_overdue(need.due_date, as_of)
                if need.status in ACTIONABLE_STATUSES
                else None,
                note=need.note,
            )
        )
    return grouped


def _tasks_for(
    session: Session,
    run_id: int,
    patient_ids: list[str],
    allowed: list[str],
    as_of: date,
) -> dict[str, list[TaskView]]:
    if not patient_ids or not allowed:
        return {}
    rows = session.execute(
        select(db.Task, db.Patient)
        .join(db.Patient, db.Patient.patient_id == db.Task.patient_id)
        .where(
            db.Task.run_id == run_id,
            db.Task.patient_id.in_(patient_ids),
            db.Task.task_type.in_(allowed),
        )
        .order_by(db.Task.priority, db.Task.due_date)
    ).all()
    programs_by_task = _programs_for_tasks(session, [task.id for task, _ in rows])
    grouped: dict[str, list[TaskView]] = defaultdict(list)
    for task, patient in rows:
        grouped[task.patient_id].append(
            _to_task_view(task, patient, as_of, programs_by_task.get(task.id, []))
        )
    return grouped
