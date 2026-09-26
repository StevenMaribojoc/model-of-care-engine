"""Materialise the validated rule configuration into the reference tables.

Why write rules to the database at all when the engine already holds them in
memory? Because derived rows need something to point at. An enrollment that stores
the string "HIGH_RISK" is a note; an enrollment with a foreign key to a tier row is
a fact you can join, count, and report on -- "how many patients moved out of High
Risk this quarter" becomes a query rather than a script.

The direction of authority is worth being explicit about: for this exercise YAML is
the source of truth and these tables are a projection of it, rebuilt on every
startup. In production the arrow reverses -- the tables become the system of
record, edited through an admin UI with approval and effective dating, and the YAML
survives only as the bootstrap seed. Nothing downstream changes when it flips,
because everything downstream already reads the tables.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import (
    CodeSet,
    CodeSetMember,
    NeedRequirement,
    Program,
    RiskTier,
    RoleTaskVisibility,
    Specialty,
)
from app.rules.schema import RulesBundle

logger = logging.getLogger(__name__)


def seed_reference(session: Session, rules: RulesBundle) -> None:
    reference = rules.reference

    for specialty in reference.specialties:
        session.add(
            Specialty(
                code=specialty.code,
                name=specialty.name,
                requires_referral=specialty.requires_referral,
            )
        )

    for code_set in reference.code_sets:
        session.add(CodeSet(code=code_set.code, name=code_set.name))
        for prefix in code_set.prefixes:
            session.add(
                CodeSetMember(code_set_code=code_set.code, icd_prefix=prefix)
            )

    for entry in reference.role_visibility:
        for task_type in entry.task_types:
            session.add(
                RoleTaskVisibility(
                    role=entry.role.value, name=entry.name, task_type=task_type.value
                )
            )

    for program in rules.programs:
        program_row = Program(
            code=program.code,
            name=program.name,
            description=program.description,
            is_active=program.is_active,
            eligibility=program.eligibility_raw,
            rules_version=rules.version,
        )
        session.add(program_row)
        session.flush()  # need program_id for the tier rows

        for tier in program.tiers:
            tier_row = RiskTier(
                program_id=program_row.program_id,
                code=tier.code,
                name=tier.name,
                eval_order=tier.eval_order,
                priority=tier.priority,
                criteria=tier.criteria_raw,
            )
            session.add(tier_row)
            session.flush()

            for need in tier.needs:
                session.add(
                    NeedRequirement(
                        tier_id=tier_row.tier_id,
                        need_type=need.need_type,
                        target=need.target,
                        cadence_days=need.cadence_days,
                        note=need.note,
                    )
                )

    session.flush()
    logger.info(
        "seeded %d programs (rules_version=%s)", len(rules.programs), rules.version
    )
