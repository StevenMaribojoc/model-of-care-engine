"""Read models returned by the service layer.

Plain dataclasses rather than ORM rows or API schemas, for two reasons. Handing
ORM objects to the router invites lazy loads to fire during serialisation, which
is how an N+1 query appears in production and never in a test. And keeping the
API schemas in app/api means the wire format can change -- a field renamed for the
frontend, a value formatted differently -- without the service layer knowing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PatientSummary:
    patient_id: str
    name: str
    age: int | None
    gender: str | None
    language: str | None
    phone: str | None
    pcp_provider_name: str | None


@dataclass(frozen=True)
class EnrollmentView:
    program_code: str
    program_name: str
    tier_code: str | None
    tier_name: str | None
    tier_priority: int | None
    evidence: dict


@dataclass(frozen=True)
class NeedView:
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


@dataclass(frozen=True)
class TaskSource:
    """The program and tier that asked for this work.

    Carried on the task so a worklist row can explain its own urgency. Without
    it the priority number looks arbitrary: a task can read "P2" while the
    patient is in a tier literally named "High Risk", because the P2 came from
    a *different* program's tier.
    """

    program_code: str
    program_name: str
    tier_code: str | None
    tier_name: str | None


@dataclass(frozen=True)
class TaskStateView:
    """The human-owned half of a task. None everywhere until someone touches it."""

    status: str
    assignee: str | None
    note: str | None
    updated_at: str | None
    updated_by_role: str | None


@dataclass(frozen=True)
class TaskView:
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
    # Which programs are driving this one piece of work. Plural because a merged
    # task can be demanded by several.
    program_codes: list[str]
    sources: list[TaskSource] = field(default_factory=list)
    # Survives re-derivation: matched by natural key, not by task id.
    state: TaskStateView | None = None
    patient: PatientSummary | None = None


@dataclass(frozen=True)
class PatientView:
    patient: PatientSummary
    enrollments: list[EnrollmentView] = field(default_factory=list)
    needs: list[NeedView] = field(default_factory=list)
    tasks: list[TaskView] = field(default_factory=list)


@dataclass(frozen=True)
class ClinicalHistoryView:
    """The raw facts behind a decision, for the "why is this here?" view."""

    diagnoses: list[dict] = field(default_factory=list)
    labs: list[dict] = field(default_factory=list)
    encounters: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class PatientDetailView:
    patient: PatientSummary
    enrollments: list[EnrollmentView]
    needs: list[NeedView]
    tasks: list[TaskView]
    history: ClinicalHistoryView


@dataclass(frozen=True)
class Page:
    total: int
    limit: int
    offset: int
    items: list


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    as_of: date
    rules_version: str
    patient_count: int
    duration_ms: int
    warnings: list[str]


@dataclass(frozen=True)
class SummaryView:
    run: RunInfo
    role: str
    total_patients: int
    patients_with_tasks: int
    tasks_by_type: dict[str, int]
    tasks_by_specialty: dict[str, int]
    enrollments_by_tier: dict[str, int]
    needs_by_status: dict[str, int]
    # Gaps that are real but generate no work -- the no-PCP-history population.
    unactionable_gaps: int
