"""Translation between database rows and the engine's plain domain objects.

The engine must not import SQLAlchemy, so something has to sit in between. That is
this module. It is also the seam where a future change of source -- an EHR API, a
warehouse table, a Kafka topic -- lands without the engine noticing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.models import (
    Diagnosis,
    Encounter,
    LabResult,
    PatientRecord,
    RunResult,
)


def load_patient_records(session: Session) -> tuple[PatientRecord, ...]:
    """Assemble every patient with their full clinical history.

    Four flat queries grouped in memory rather than per-patient lazy loading: at
    300 patients either works, but the N+1 version would be the first thing to
    fall over at 300,000 and it costs nothing to avoid now.

    This does load the whole population into memory at once. That is the right
    trade for a single-tenant batch of this size; the scaling path is to stream
    patients in id-ordered chunks, which works because the engine evaluates each
    patient independently.
    """
    diagnoses: dict[str, list[Diagnosis]] = defaultdict(list)
    for row in session.scalars(select(db.Diagnosis)):
        diagnoses[row.patient_id].append(
            Diagnosis(
                icd_code=row.icd_code,
                description=row.description or "",
                diagnosed_date=row.diagnosed_date,
            )
        )

    labs: dict[str, list[LabResult]] = defaultdict(list)
    for row in session.scalars(select(db.LabResult)):
        labs[row.patient_id].append(
            LabResult(
                test_name=row.test_name,
                result_value=row.result_value,
                result_date=row.result_date,
            )
        )

    encounters: dict[str, list[Encounter]] = defaultdict(list)
    for row in session.scalars(select(db.Encounter)):
        encounters[row.patient_id].append(
            Encounter(
                specialty=row.specialty,
                encounter_date=row.encounter_date,
                provider_name=row.provider_name,
            )
        )

    records: list[PatientRecord] = []
    for row in session.scalars(select(db.Patient).order_by(db.Patient.patient_id)):
        records.append(
            PatientRecord(
                patient_id=row.patient_id,
                first_name=row.first_name,
                last_name=row.last_name,
                date_of_birth=row.date_of_birth,
                gender=row.gender,
                phone=row.phone,
                language=row.language,
                pcp_provider_name=row.pcp_provider_name,
                diagnoses=tuple(diagnoses.get(row.patient_id, ())),
                labs=tuple(labs.get(row.patient_id, ())),
                encounters=tuple(encounters.get(row.patient_id, ())),
            )
        )
    return tuple(records)


# --------------------------------------------------------------------------- #
# Persisting engine output
# --------------------------------------------------------------------------- #


def persist_run(
    session: Session, result: RunResult, duration_ms: int, patient_count: int
) -> int:
    """Write a RunResult to the derived tables and return its run_id.

    Written layer by layer -- enrollments, then needs, then tasks -- because each
    layer needs the surrogate keys the previous one generated. Each layer is one
    bulk insert followed by one flush rather than a flush per row.

    The engine speaks in codes ("DIABETES_MGMT", "HIGH_RISK") because it knows
    nothing about the database. Resolving those to foreign keys is this function's
    job, and it is why the derived tables can be joined and reported on rather
    than merely read back as strings.
    """
    program_ids = dict(
        session.execute(select(db.Program.code, db.Program.program_id)).all()
    )
    tier_ids = {
        (program_id, code): tier_id
        for program_id, code, tier_id in session.execute(
            select(db.RiskTier.program_id, db.RiskTier.code, db.RiskTier.tier_id)
        ).all()
    }

    run = db.EngineRun(
        as_of_date=result.as_of,
        rules_version=result.rules_version,
        patient_count=patient_count,
        duration_ms=duration_ms,
        warnings=list(result.warnings),
    )
    session.add(run)
    session.flush()

    enrollment_rows: dict[str, db.Enrollment] = {}
    for enrollment in result.enrollments:
        program_id = program_ids[enrollment.program_code]
        enrollment_rows[enrollment.key] = db.Enrollment(
            run_id=run.run_id,
            patient_id=enrollment.patient_id,
            program_id=program_id,
            tier_id=tier_ids.get((program_id, enrollment.tier_code)),
            evidence=enrollment.evidence,
        )
    session.add_all(enrollment_rows.values())
    session.flush()

    need_rows: dict[str, db.ClinicalNeed] = {}
    for need in result.needs:
        need_rows[need.key] = db.ClinicalNeed(
            run_id=run.run_id,
            enrollment_id=enrollment_rows[f"{need.patient_id}|{need.program_code}"].id,
            patient_id=need.patient_id,
            need_type=need.need_type,
            target=need.target,
            cadence_days=need.cadence_days,
            status=need.status.value,
            last_completed_date=need.last_completed_date,
            next_scheduled_date=need.next_scheduled_date,
            due_date=need.due_date,
            priority=need.priority,
            note=need.note,
        )
    session.add_all(need_rows.values())
    session.flush()

    task_rows: list[tuple[db.Task, tuple[str, ...]]] = []
    for task in result.tasks:
        row = db.Task(
            run_id=run.run_id,
            patient_id=task.patient_id,
            task_type=task.task_type.value,
            need_type=task.need_type,
            target=task.target,
            priority=task.priority,
            due_date=task.due_date,
            status=task.status,
        )
        task_rows.append((row, task.need_keys))
    session.add_all(row for row, _ in task_rows)
    session.flush()

    session.add_all(
        db.TaskNeed(task_id=row.id, need_id=need_rows[key].id)
        for row, keys in task_rows
        for key in keys
    )
    session.flush()

    return run.run_id


def find_run(session: Session, as_of: date, rules_version: str) -> int | None:
    """Look up an existing run by its natural identity."""
    return session.scalar(
        select(db.EngineRun.run_id).where(
            db.EngineRun.as_of_date == as_of,
            db.EngineRun.rules_version == rules_version,
        )
    )
