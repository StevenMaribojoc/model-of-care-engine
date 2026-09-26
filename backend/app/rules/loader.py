"""Load, validate, compile, and version the rule configuration.

Everything that can go wrong with a config is caught here, at startup:

  structural errors      -> Pydantic (unknown keys, cadence <= 0, missing fields)
  cross-file references  -> checked below (a need targeting an undeclared specialty)
  criteria errors        -> the predicate registry (unknown predicate or code set)

The version is a content hash of the raw config bytes. It is half of an engine
run's identity, so results produced under one ruleset can never be confused with
results produced under another -- which is what makes "we changed the A1C
threshold last Tuesday" an answerable question.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.rules.predicates import ConfigError, RuleContext, compile_criteria
from app.rules.schema import (
    CompiledNeed,
    CompiledProgram,
    CompiledTier,
    ProgramsConfig,
    ReferenceConfig,
    RulesBundle,
)
from app.settings import settings

REFERENCE_FILE = "reference.yaml"
PROGRAMS_FILE = "programs.yaml"


def load_rules(config_dir: Path | None = None) -> RulesBundle:
    directory = Path(config_dir or settings.config_dir)
    reference_bytes = _read(directory / REFERENCE_FILE)
    programs_bytes = _read(directory / PROGRAMS_FILE)

    reference = _parse(ReferenceConfig, reference_bytes, REFERENCE_FILE)
    programs_config = _parse(ProgramsConfig, programs_bytes, PROGRAMS_FILE)

    _validate_reference(reference)
    ctx = RuleContext(
        code_set_prefixes={cs.code: tuple(cs.prefixes) for cs in reference.code_sets},
        known_lab_tests=frozenset(reference.lab_tests),
    )
    target_domains = {
        "specialty": frozenset(s.code for s in reference.specialties),
        "lab_test": frozenset(reference.lab_tests),
    }
    need_type_domains = {nt.type: nt.target_domain for nt in reference.need_types}

    compiled: list[CompiledProgram] = []
    seen_programs: set[str] = set()
    for program in programs_config.programs:
        if program.code in seen_programs:
            raise ConfigError(f"duplicate program code {program.code!r}")
        seen_programs.add(program.code)
        compiled.append(
            _compile_program(program, ctx, need_type_domains, target_domains)
        )

    version = _version(reference_bytes, programs_bytes)
    return RulesBundle(version=version, programs=tuple(compiled), reference=reference)


@lru_cache(maxsize=1)
def get_rules() -> RulesBundle:
    """Process-wide cached bundle. Rules are immutable for the life of the process."""
    return load_rules()


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _compile_program(program, ctx, need_type_domains, target_domains) -> CompiledProgram:
    label = f"program {program.code}"
    eligibility = compile_criteria(
        program.eligibility, ctx, path=f"{label}.eligibility"
    )

    tiers: list[CompiledTier] = []
    seen_tiers: set[str] = set()
    for order, tier in enumerate(program.tiers):
        if tier.code in seen_tiers:
            raise ConfigError(f"{label}: duplicate tier code {tier.code!r}")
        seen_tiers.add(tier.code)

        needs: list[CompiledNeed] = []
        seen_needs: set[tuple[str, str]] = set()
        for need in tier.needs:
            domain = need_type_domains.get(need.type)
            if domain is None:
                raise ConfigError(
                    f"{label}/{tier.code}: need type {need.type!r} is not declared "
                    f"in reference.yaml (known: {sorted(need_type_domains)})"
                )
            allowed = target_domains.get(domain)
            if allowed is None:
                raise ConfigError(
                    f"need type {need.type!r} declares unknown target_domain {domain!r}"
                )
            if need.target not in allowed:
                raise ConfigError(
                    f"{label}/{tier.code}: need target {need.target!r} is not a known "
                    f"{domain} (known: {sorted(allowed)})"
                )
            if (need.type, need.target) in seen_needs:
                raise ConfigError(
                    f"{label}/{tier.code}: duplicate need {need.type}/{need.target}"
                )
            seen_needs.add((need.type, need.target))
            needs.append(
                CompiledNeed(
                    need_type=need.type,
                    target=need.target,
                    cadence_days=need.every_days,
                    note=need.note,
                )
            )

        tiers.append(
            CompiledTier(
                code=tier.code,
                name=tier.name,
                priority=tier.priority,
                eval_order=order,
                criteria=compile_criteria(
                    tier.criteria, ctx, path=f"{label}/{tier.code}.criteria"
                ),
                criteria_raw=tier.criteria,
                needs=tuple(needs),
            )
        )

    return CompiledProgram(
        code=program.code,
        name=program.name,
        description=program.description,
        eligibility=eligibility,
        eligibility_raw=program.eligibility,
        tiers=tuple(tiers),
        is_active=program.is_active,
    )


def _validate_reference(reference: ReferenceConfig) -> None:
    _require_unique([s.code for s in reference.specialties], "specialty code")
    _require_unique([c.code for c in reference.code_sets], "code set code")
    _require_unique(reference.lab_tests, "lab test")
    _require_unique([n.type for n in reference.need_types], "need type")
    _require_unique([r.role.value for r in reference.role_visibility], "role")

    declared_task_types = {n.task_type for n in reference.need_types}
    visible_task_types = {
        task_type
        for entry in reference.role_visibility
        for task_type in entry.task_types
    }
    # Every task type a need can produce must be visible to at least one role,
    # otherwise the engine would generate work nobody can ever see.
    orphaned = declared_task_types - visible_task_types
    if orphaned:
        raise ConfigError(
            f"task types {sorted(t.value for t in orphaned)} are produced by a need "
            "type but visible to no role in role_visibility"
        )


def _require_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"duplicate {label} {value!r}")
        seen.add(value)


def _read(path: Path) -> bytes:
    if not path.exists():
        raise ConfigError(f"missing rule configuration file: {path}")
    return path.read_bytes()


def _parse(model, raw: bytes, filename: str):
    data = yaml.safe_load(raw)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{filename} is invalid:\n{exc}") from exc


def _version(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
        digest.update(b"\x00")
    return digest.hexdigest()[:16]
