"""Wire format.

Separate from the service read models so the two can move independently: the
frontend can get a renamed field or a computed convenience without the service
layer changing, and a service refactor cannot silently alter a published contract.
``from_attributes`` lets these validate straight from the dataclasses.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PatientSummaryOut(_Out):
    patient_id: str
    name: str
    age: int | None
    gender: str | None
    language: str | None
    phone: str | None
    pcp_provider_name: str | None


class EnrollmentOut(_Out):
    program_code: str
    program_name: str
    tier_code: str | None
    tier_name: str | None
    tier_priority: int | None
    evidence: dict[str, Any]


class NeedOut(_Out):
    program_code: str
    need_type: str
    target: str
    cadence_days: int
    status: str
    last_completed_date: date | None
    next_scheduled_date: date | None
    due_date: date | None
    days_overdue: int | None
    note: str | None


class TaskSourceOut(_Out):
    program_code: str
    program_name: str
    tier_code: str | None
    tier_name: str | None


class TaskOut(_Out):
    task_id: int
    patient_id: str
    patient_name: str
    task_type: str
    need_type: str
    target: str
    priority: int
    due_date: date | None
    days_overdue: int | None
    status: str
    program_codes: list[str]
    # The program AND tier that asked for this work, so the priority number on a
    # worklist row can explain where it came from.
    sources: list[TaskSourceOut] = []
    patient: PatientSummaryOut | None = None


class PatientOut(_Out):
    patient: PatientSummaryOut
    enrollments: list[EnrollmentOut]
    needs: list[NeedOut]
    tasks: list[TaskOut]


class ClinicalHistoryOut(_Out):
    diagnoses: list[dict[str, Any]]
    labs: list[dict[str, Any]]
    encounters: list[dict[str, Any]]


class PatientDetailOut(_Out):
    patient: PatientSummaryOut
    enrollments: list[EnrollmentOut]
    needs: list[NeedOut]
    tasks: list[TaskOut]
    history: ClinicalHistoryOut


class RunInfoOut(_Out):
    run_id: int
    as_of: date
    rules_version: str
    patient_count: int
    duration_ms: int
    warnings: list[str]


class SummaryOut(_Out):
    run: RunInfoOut
    role: str
    total_patients: int
    patients_with_tasks: int
    tasks_by_type: dict[str, int]
    tasks_by_specialty: dict[str, int]
    enrollments_by_tier: dict[str, int]
    needs_by_status: dict[str, int]
    unactionable_gaps: int


class TaskPageOut(_Out):
    total: int
    limit: int
    offset: int
    items: list[TaskOut]
    run: RunInfoOut


class PatientPageOut(_Out):
    total: int
    limit: int
    offset: int
    items: list[PatientOut]
    run: RunInfoOut
