"""Shapes for the rule configuration, and the compiled form the engine consumes.

Two layers on purpose:

* the ``*Config`` Pydantic models mirror the YAML one-for-one and enforce the
  structural rules (a cadence must be positive, a tier must have a code);
* the ``Compiled*`` dataclasses are what the engine actually runs -- criteria have
  already become callables, and cross-references have already been resolved.

Keeping them apart means the engine never re-parses configuration mid-run, and a
configuration error can only ever surface at load time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import Role, TaskType
from app.rules.predicates import Predicate


class _Strict(BaseModel):
    """Reject unknown keys: a typo in the config should fail, not be ignored."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# reference.yaml
# --------------------------------------------------------------------------- #


class SpecialtyConfig(_Strict):
    code: str
    name: str
    requires_referral: bool


class CodeSetConfig(_Strict):
    code: str
    name: str
    prefixes: list[str] = Field(min_length=1)


class NeedTypeConfig(_Strict):
    type: str
    target_domain: str  # "specialty" | "lab_test"
    task_type: TaskType


class RoleVisibilityConfig(_Strict):
    role: Role
    name: str
    task_types: list[TaskType] = Field(min_length=1)


class ReferenceConfig(_Strict):
    specialties: list[SpecialtyConfig]
    code_sets: list[CodeSetConfig]
    lab_tests: list[str]
    need_types: list[NeedTypeConfig]
    role_visibility: list[RoleVisibilityConfig]


# --------------------------------------------------------------------------- #
# programs.yaml
# --------------------------------------------------------------------------- #


class NeedRequirementConfig(_Strict):
    type: str
    target: str
    every_days: int = Field(gt=0)
    note: str | None = None


class TierConfig(_Strict):
    code: str
    name: str
    priority: int = Field(ge=1)
    criteria: dict[str, Any] = Field(default_factory=dict)
    needs: list[NeedRequirementConfig] = Field(min_length=1)

    @field_validator("criteria", mode="before")
    @classmethod
    def _null_criteria_is_catch_all(cls, value: Any) -> Any:
        return {} if value is None else value


class ProgramConfig(_Strict):
    code: str
    name: str
    description: str | None = None
    eligibility: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    tiers: list[TierConfig] = Field(min_length=1)


class ProgramsConfig(_Strict):
    programs: list[ProgramConfig] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Compiled form
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompiledNeed:
    need_type: str
    target: str
    cadence_days: int
    note: str | None


@dataclass(frozen=True)
class CompiledTier:
    code: str
    name: str
    priority: int
    eval_order: int  # position in the YAML list; first match wins
    criteria: Predicate
    criteria_raw: dict[str, Any]
    needs: tuple[CompiledNeed, ...]


@dataclass(frozen=True)
class CompiledProgram:
    code: str
    name: str
    description: str | None
    eligibility: Predicate
    eligibility_raw: dict[str, Any]
    tiers: tuple[CompiledTier, ...]
    is_active: bool


@dataclass(frozen=True)
class RulesBundle:
    """Everything the engine needs to know about the rules, fully resolved."""

    version: str  # content hash of the config files; part of an engine_run's identity
    programs: tuple[CompiledProgram, ...]
    reference: ReferenceConfig

    @property
    def active_programs(self) -> tuple[CompiledProgram, ...]:
        return tuple(p for p in self.programs if p.is_active)

    def requires_referral(self, specialty_code: str) -> bool:
        for specialty in self.reference.specialties:
            if specialty.code == specialty_code:
                return specialty.requires_referral
        # Unreachable for validated config; fail closed if it ever happens, because
        # inventing a scheduling task for an unknown specialty is the worse error.
        return True

    def default_task_type(self, need_type: str) -> TaskType:
        for entry in self.reference.need_types:
            if entry.type == need_type:
                return entry.task_type
        raise KeyError(f"no need type declared for {need_type!r}")

    def visible_task_types(self, role: Role) -> tuple[TaskType, ...]:
        for entry in self.reference.role_visibility:
            if entry.role == role:
                return tuple(entry.task_types)
        return ()
