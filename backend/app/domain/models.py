"""The vocabulary the engine speaks.

Deliberately plain dataclasses and enums: nothing here imports SQLAlchemy, FastAPI,
or the rules loader. The engine is a pure function of (records, rules, as_of), so its
inputs and outputs have to be expressible without a database session or a request.
That is what lets the same code run inside the API, inside a nightly batch job, or
inside a unit test with three hand-written patients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class NeedStatus(str, Enum):
    """Where a single clinical need stands as of the evaluation date."""

    SATISFIED = "SATISFIED"  # seen within cadence
    SCHEDULED = "SCHEDULED"  # an upcoming appointment already covers it
    DUE = "DUE"  # seen before, but longer ago than the cadence allows
    NEVER_SEEN = "NEVER_SEEN"  # no completed encounter for this target, ever


class TaskType(str, Enum):
    """What kind of work an unmet need creates, which decides who it routes to."""

    SCHEDULING = "SCHEDULING"  # book the next appointment directly
    REFERRAL = "REFERRAL"  # a clinician must approve a referral first


class Role(str, Enum):
    SCHEDULER = "SCHEDULER"
    CLINICAL = "CLINICAL"


# --------------------------------------------------------------------------- #
# Source facts, as loaded from the CSVs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Diagnosis:
    icd_code: str
    description: str
    diagnosed_date: date | None


@dataclass(frozen=True)
class LabResult:
    test_name: str
    result_value: float
    result_date: date


@dataclass(frozen=True)
class Encounter:
    specialty: str
    encounter_date: date
    provider_name: str | None


@dataclass(frozen=True)
class PatientRecord:
    """Everything known about one patient, assembled once and passed to the engine."""

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date | None
    gender: str | None
    phone: str | None
    language: str | None
    pcp_provider_name: str | None
    diagnoses: tuple[Diagnosis, ...] = ()
    labs: tuple[LabResult, ...] = ()
    encounters: tuple[Encounter, ...] = ()

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# --------------------------------------------------------------------------- #
# Derived facts: computed once per patient, shared by every program
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PatientFacts:
    """Pre-digested view of a patient at a point in time.

    Built once per patient per run and reused by every program's predicates. With a
    handful of programs this is a convenience; at a thousand programs it is the
    difference between scanning a patient's encounter list once and scanning it a
    thousand times.
    """

    patient_id: str
    as_of: date
    age: int | None
    # Every prefix of every diagnosis code, so membership tests are a set lookup:
    # "E11.65" contributes {"E", "E1", "E11", "E11.", "E11.6", "E11.65"}.
    diagnosis_prefixes: frozenset[str]
    # The raw codes behind those prefixes, kept so tier evidence can name the
    # actual diagnosis that qualified the patient rather than just saying "yes".
    diagnosis_codes: tuple[str, ...]
    # Most recent result per test name, on or before as_of.
    latest_labs: dict[str, LabResult]
    # Most recent completed visit per specialty (encounter_date <= as_of).
    last_visit: dict[str, date]
    # Earliest future appointment per specialty (encounter_date > as_of).
    next_visit: dict[str, date]

    def has_code_prefix(self, prefix: str) -> bool:
        return prefix in self.diagnosis_prefixes


# --------------------------------------------------------------------------- #
# Engine output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Enrollment:
    """A patient qualifying for a program, and the tier they landed in."""

    patient_id: str
    program_code: str
    tier_code: str | None  # None when no tier matched; surfaced as a data-quality warning
    tier_priority: int | None
    evidence: dict = field(default_factory=dict)  # why this tier, for auditability

    @property
    def key(self) -> str:
        return f"{self.patient_id}|{self.program_code}"


@dataclass(frozen=True)
class Need:
    """One unit of care a patient requires, and whether it is currently met.

    Needs are recorded per program even when two programs ask for the same care,
    because program performance is measured per program. Tasks are what get merged.
    """

    patient_id: str
    program_code: str
    tier_code: str | None
    need_type: str  # "visit" today; "lab_order" is the planned second resolver
    target: str  # a specialty for visits, a test name for lab orders
    cadence_days: int
    status: NeedStatus
    # How this gap should be worked, decided by the resolver because routing is a
    # property of the need type. None means "a real gap that generates no task" --
    # the spec's no-PCP-history case. Such needs stay visible and filterable so
    # those patients do not fall through the cracks; they simply are not staff work.
    task_type: TaskType | None = None
    last_completed_date: date | None = None
    next_scheduled_date: date | None = None
    due_date: date | None = None
    note: str | None = None
    priority: int = 99

    @property
    def key(self) -> str:
        return f"{self.patient_id}|{self.program_code}|{self.need_type}|{self.target}"

    @property
    def is_actionable(self) -> bool:
        """There is a genuine care gap here, whether or not it creates work."""
        return self.status in (NeedStatus.DUE, NeedStatus.NEVER_SEEN)

    @property
    def generates_task(self) -> bool:
        return self.is_actionable and self.task_type is not None


@dataclass(frozen=True)
class Task:
    """Work routed to a staff role, derived from one or more unmet needs.

    Merged across programs by (patient, need_type, target): if both Diabetes
    Management and a future Cardiac program want a Cardiology visit, staff should
    book one appointment, not two.
    """

    patient_id: str
    task_type: TaskType
    need_type: str
    target: str
    priority: int
    due_date: date | None
    need_keys: tuple[str, ...]
    program_codes: tuple[str, ...]
    status: str = "OPEN"


@dataclass(frozen=True)
class RunResult:
    as_of: date
    rules_version: str
    enrollments: tuple[Enrollment, ...]
    needs: tuple[Need, ...]
    tasks: tuple[Task, ...]
    warnings: tuple[str, ...] = ()
