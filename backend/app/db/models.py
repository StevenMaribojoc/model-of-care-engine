"""Relational schema, in three clearly separated layers.

    SOURCE     facts as received from the EHR (here: the provided CSVs).
               The engine reads these and never writes them.

    REFERENCE  the clinical vocabulary and the rules themselves, seeded from YAML.
               Derived rows point at these by foreign key, so a result can always
               name the exact program and tier that produced it.

    DERIVED    engine output, every row owned by an engine_run. Runs are additive;
               nothing is updated in place.

The layering is the answer to the "three patient tables that grew organically"
problem: there is exactly one patient table, source data is immutable, and
everything computed is quarantined behind a run id where it can be recomputed,
compared, or thrown away without touching the facts.

SQLite here for zero-setup local running. The models are deliberately plain
SQLAlchemy with no dialect-specific types, so the same definitions run on MSSQL
(ecares' database) by changing the connection URL.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# SOURCE
# --------------------------------------------------------------------------- #


class Patient(Base):
    __tablename__ = "patient"

    patient_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128))
    # Nullable because a missing birthday must be representable rather than guessed;
    # the engine treats an unknown age as "cannot satisfy an age floor".
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The *assigned* PCP. Explicitly not evidence of a PCP visit -- 69 of the 74
    # adults with no PCP encounter do have an assigned PCP name.
    pcp_provider_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    diagnoses: Mapped[list["Diagnosis"]] = relationship(back_populates="patient")
    labs: Mapped[list["LabResult"]] = relationship(back_populates="patient")
    encounters: Mapped[list["Encounter"]] = relationship(back_populates="patient")


class Diagnosis(Base):
    __tablename__ = "diagnosis"
    __table_args__ = (Index("ix_diagnosis_patient", "patient_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    icd_code: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    diagnosed_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    patient: Mapped[Patient] = relationship(back_populates="diagnoses")


class LabResult(Base):
    __tablename__ = "lab_result"
    __table_args__ = (
        # Supports "most recent HbA1c for this patient on or before a date".
        Index("ix_lab_patient_test_date", "patient_id", "test_name", "result_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    test_name: Mapped[str] = mapped_column(String(64))
    result_value: Mapped[float] = mapped_column(Float)
    result_date: Mapped[date] = mapped_column(Date)

    patient: Mapped[Patient] = relationship(back_populates="labs")


class Encounter(Base):
    __tablename__ = "encounter"
    __table_args__ = (
        # Supports both "last completed visit" and "next scheduled visit" per
        # specialty, which is the hottest access path in the whole engine.
        Index("ix_encounter_patient_spec_date", "patient_id", "specialty", "encounter_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    specialty: Mapped[str] = mapped_column(String(64))
    encounter_date: Mapped[date] = mapped_column(Date)
    provider_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Whether an encounter is history or an appointment is a function of the
    # evaluation date, not a stored flag -- so it is not a column here.

    patient: Mapped[Patient] = relationship(back_populates="encounters")


# --------------------------------------------------------------------------- #
# REFERENCE / RULES  (seeded from YAML)
# --------------------------------------------------------------------------- #


class Specialty(Base):
    __tablename__ = "specialty"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # Drives "referral tasks apply only to specialists". Data, not an if-statement.
    requires_referral: Mapped[bool] = mapped_column(Boolean)


class CodeSet(Base):
    __tablename__ = "code_set"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))

    members: Mapped[list["CodeSetMember"]] = relationship(back_populates="code_set")


class CodeSetMember(Base):
    __tablename__ = "code_set_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_set_code: Mapped[str] = mapped_column(ForeignKey("code_set.code"))
    icd_prefix: Mapped[str] = mapped_column(String(16))

    code_set: Mapped[CodeSet] = relationship(back_populates="members")


class Program(Base):
    __tablename__ = "program"

    program_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The criteria expression, stored as authored. Keeping it alongside the result
    # is what lets the UI answer "what does this program actually require?".
    eligibility: Mapped[dict] = mapped_column(JSON, default=dict)
    rules_version: Mapped[str] = mapped_column(String(32))

    tiers: Mapped[list["RiskTier"]] = relationship(back_populates="program")


class RiskTier(Base):
    __tablename__ = "risk_tier"
    __table_args__ = (UniqueConstraint("program_id", "code", name="uq_tier_program_code"),)

    tier_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("program.program_id"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    eval_order: Mapped[int] = mapped_column(Integer)  # first match wins
    priority: Mapped[int] = mapped_column(Integer)  # 1 = most urgent
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)

    program: Mapped[Program] = relationship(back_populates="tiers")
    requirements: Mapped[list["NeedRequirement"]] = relationship(back_populates="tier")


class NeedRequirement(Base):
    """What a tier requires. The template a ClinicalNeed is instantiated from."""

    __tablename__ = "need_requirement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tier_id: Mapped[int] = mapped_column(ForeignKey("risk_tier.tier_id"))
    need_type: Mapped[str] = mapped_column(String(32))  # "visit", later "lab_order"
    target: Mapped[str] = mapped_column(String(64))  # specialty code, or test name
    cadence_days: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)

    tier: Mapped[RiskTier] = relationship(back_populates="requirements")


class RoleTaskVisibility(Base):
    """Which task types a role may see. Authorization data, enforced server-side."""

    __tablename__ = "role_task_visibility"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(32))


# --------------------------------------------------------------------------- #
# DERIVED  (one set per engine run)
# --------------------------------------------------------------------------- #


class EngineRun(Base):
    """One evaluation of the whole population.

    Identified by (as_of_date, rules_version): the same data evaluated on the same
    date under the same rules is the same run, so results are cached rather than
    recomputed, and a rule change automatically produces a distinct run instead of
    silently overwriting the previous answer.
    """

    __tablename__ = "engine_run"
    __table_args__ = (
        UniqueConstraint("as_of_date", "rules_version", name="uq_run_asof_rules"),
    )

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    rules_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    patient_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class Enrollment(Base):
    __tablename__ = "enrollment"
    __table_args__ = (
        Index("ix_enrollment_run_patient", "run_id", "patient_id"),
        UniqueConstraint("run_id", "patient_id", "program_id", name="uq_enrollment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("engine_run.run_id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    program_id: Mapped[int] = mapped_column(ForeignKey("program.program_id"))
    # Null when the patient is eligible but matched no tier -- a rules gap worth
    # surfacing rather than hiding behind a default tier.
    tier_id: Mapped[int | None] = mapped_column(ForeignKey("risk_tier.tier_id"), nullable=True)
    # Why this tier: e.g. {"test": "HbA1c", "value": 9.4, "result_date": "2025-11-10"}.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class ClinicalNeed(Base):
    """One care requirement for one patient under one program, with its status.

    Stored per program even when two programs ask for the same visit, because
    program performance is measured per program. Merging happens at the task layer.
    """

    __tablename__ = "clinical_need"
    __table_args__ = (
        Index("ix_need_run_patient", "run_id", "patient_id"),
        Index("ix_need_run_status_target", "run_id", "status", "target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("engine_run.run_id"), index=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollment.id"))
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    need_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))
    cadence_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))  # SATISFIED|SCHEDULED|DUE|NEVER_SEEN
    last_completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=99)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)


class Task(Base):
    """Actionable work routed to a role.

    One table with a task_type discriminator rather than separate scheduling and
    referral tables: both share a lifecycle, both appear in the same worklists, and
    the role filter is a single predicate. Type-specific fields, if they ever
    appear, belong in a 1:1 extension table.
    """

    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_run_type_target", "run_id", "task_type", "target"),
        Index("ix_task_run_patient", "run_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("engine_run.run_id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.patient_id"))
    task_type: Mapped[str] = mapped_column(String(32))  # SCHEDULING | REFERRAL
    need_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")


class TaskNeed(Base):
    """Links a task to every need it satisfies.

    Many-to-many because one appointment can close needs from several programs;
    without this link, closing a task could not report which quality gaps it shut.
    """

    __tablename__ = "task_need"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("clinical_need.id"), index=True)


# --------------------------------------------------------------------------- #
# HUMAN STATE  (prototype -- see README)
# --------------------------------------------------------------------------- #


class TaskState(Base):
    """What a person did about a task, as opposed to what the engine derived.

    This is the one table in the system that is **not** recomputable. Everything
    else can be thrown away and rebuilt from the CSVs and the YAML; a note
    saying "called, left a voicemail" cannot. So it is deliberately built
    differently from every other table here:

    * **No run_id.** Derived rows belong to a run and are replaced wholesale on
      the next one. Human state has to survive that, or a nightly re-evaluation
      would erase everyone's work.

    * **Keyed by what the work *is*, not by a row id.** A task row is recreated
      with a fresh surrogate id on every run, so a foreign key to it would dangle
      immediately. `(patient, task_type, need_type, target)` is the natural key --
      "book this patient a PCP visit" identifies the same job today and tomorrow.

    * **No foreign key to patient either**, so a full source reload cannot
      cascade into deleting somebody's notes.

    * **Excluded from the startup rebuild** (see db/session.reset_schema).

    Note what a person deliberately *cannot* set: "completed". Completion is a
    clinical fact -- the visit shows up in the encounter feed, the need becomes
    satisfied, and the task stops being generated. Letting staff tick a box
    instead would let the worklist drift away from what actually happened to the
    patient, which is the failure this whole design exists to prevent.
    """

    __tablename__ = "task_state"
    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "task_type",
            "need_type",
            "target",
            name="uq_task_state_natural_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The natural key: this is the work, described in domain terms.
    patient_id: Mapped[str] = mapped_column(String(32))
    task_type: Mapped[str] = mapped_column(String(32))
    need_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(64))

    # OPEN | IN_PROGRESS | SNOOZED. Not "completed" -- see the note above.
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    updated_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    @property
    def natural_key(self) -> tuple[str, str, str, str]:
        return (self.patient_id, self.task_type, self.need_type, self.target)
