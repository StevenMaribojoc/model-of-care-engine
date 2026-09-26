"""Translation between database rows and the engine's plain domain objects.

The engine must not import SQLAlchemy, so something has to sit in between. That is
this module. It is also the seam where a future change of source -- an EHR API, a
warehouse table, a Kafka topic -- lands without the engine noticing.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.models import (
    Diagnosis,
    Encounter,
    LabResult,
    PatientRecord,
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
