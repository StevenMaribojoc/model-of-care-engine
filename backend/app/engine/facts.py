"""Reduce a patient's raw history to the handful of facts the rules ask about.

Built once per patient per run, then handed to every program. Two reasons:

  performance -- predicates become dictionary lookups instead of list scans, so
  adding the tenth program costs almost nothing beyond evaluating its criteria;

  consistency -- every program in a run sees exactly the same view of the patient.
  If eligibility and stratification each recomputed "most recent A1C" they could,
  in principle, disagree. Here that is not expressible.

Everything is relative to as_of. Clinical data dated after as_of is invisible, so
evaluating at a past date cannot leak future facts backwards -- without that, time
travel would quietly produce answers that were never true.
"""

from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from app.domain.models import LabResult, PatientFacts, PatientRecord


def build_facts(record: PatientRecord, as_of: date) -> PatientFacts:
    return PatientFacts(
        patient_id=record.patient_id,
        as_of=as_of,
        age=_age_at(record.date_of_birth, as_of),
        diagnosis_prefixes=_diagnosis_prefixes(record, as_of),
        diagnosis_codes=_active_codes(record, as_of),
        latest_labs=_latest_labs(record, as_of),
        last_visit=_last_visits(record, as_of),
        next_visit=_next_visits(record, as_of),
    )


def _age_at(dob: date | None, as_of: date) -> int | None:
    if dob is None or dob > as_of:
        # An unknown or future birthday yields no age rather than a negative one.
        # Predicates treat None as failing any age comparison.
        return None
    return relativedelta(as_of, dob).years


def _active_codes(record: PatientRecord, as_of: date) -> tuple[str, ...]:
    """Diagnoses known as of the evaluation date.

    An undated diagnosis is kept. Chronic conditions do not resolve, and dropping
    one for want of a date would understate a patient's risk -- the wrong direction
    to err in when the output is a care gap.
    """
    return tuple(
        dx.icd_code
        for dx in record.diagnoses
        if dx.diagnosed_date is None or dx.diagnosed_date <= as_of
    )


def _diagnosis_prefixes(record: PatientRecord, as_of: date) -> frozenset[str]:
    """Every prefix of every active code, so membership is a set lookup.

    "E11.65" contributes E, E1, E11, E11., E11.6, E11.65. Code sets are declared as
    prefixes, so asking "does this patient have anything in CHRONIC_CONDITIONS?"
    becomes ten hash lookups regardless of how long their problem list is.
    """
    prefixes: set[str] = set()
    for code in _active_codes(record, as_of):
        for length in range(1, len(code) + 1):
            prefixes.add(code[:length])
    return frozenset(prefixes)


def _latest_labs(record: PatientRecord, as_of: date) -> dict[str, LabResult]:
    """Most recent result per test on or before as_of.

    Ties on the same date are broken toward the higher value: when a patient has
    two results for one day, treating the worse one as current keeps the engine
    from under-stratifying. Also makes the run deterministic regardless of row
    order, which matters more than it sounds -- a worklist that reshuffles between
    identical runs destroys staff trust in it.
    """
    latest: dict[str, LabResult] = {}
    for lab in record.labs:
        if lab.result_date > as_of:
            continue
        current = latest.get(lab.test_name)
        if current is None or (lab.result_date, lab.result_value) > (
            current.result_date,
            current.result_value,
        ):
            latest[lab.test_name] = lab
    return latest


def _last_visits(record: PatientRecord, as_of: date) -> dict[str, date]:
    """Most recent completed visit per specialty (encounter_date <= as_of)."""
    last: dict[str, date] = {}
    for encounter in record.encounters:
        if encounter.encounter_date > as_of:
            continue
        current = last.get(encounter.specialty)
        if current is None or encounter.encounter_date > current:
            last[encounter.specialty] = encounter.encounter_date
    return last


def _next_visits(record: PatientRecord, as_of: date) -> dict[str, date]:
    """Earliest future appointment per specialty (encounter_date > as_of).

    The source data has no appointment status, so a scheduled encounter cannot be
    distinguished from one later cancelled or no-showed. Every future row is taken
    at face value; consuming a real EHR feed, status would gate this.
    """
    upcoming: dict[str, date] = {}
    for encounter in record.encounters:
        if encounter.encounter_date <= as_of:
            continue
        current = upcoming.get(encounter.specialty)
        if current is None or encounter.encounter_date < current:
            upcoming[encounter.specialty] = encounter.encounter_date
    return upcoming
