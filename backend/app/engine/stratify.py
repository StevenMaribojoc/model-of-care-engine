"""Eligibility and risk stratification for one patient against one program."""

from __future__ import annotations

from app.domain.models import Enrollment, PatientFacts
from app.rules.schema import CompiledProgram, CompiledTier


def stratify(
    facts: PatientFacts, program: CompiledProgram
) -> tuple[Enrollment | None, CompiledTier | None, str | None]:
    """Return (enrollment, tier, warning).

    ``enrollment`` is None when the patient is not eligible for the program at all.
    A patient who *is* eligible but matches no tier still gets an enrollment, with
    tier None and a warning: that combination means the rules have a hole in them,
    and dropping the patient silently would hide exactly the gap worth fixing.
    """
    eligible, eligibility_evidence = program.eligibility(facts)
    if not eligible:
        return None, None, None

    for tier in program.tiers:
        # First match wins, in YAML order. Tiers are authored most specific first,
        # and an empty criteria block acts as the catch-all. Ordering the decision
        # rather than requiring mutually exclusive predicates is what keeps a tier
        # like "all other eligible patients" expressible without restating the
        # negation of every tier above it.
        matched, tier_evidence = tier.criteria(facts)
        if matched:
            return (
                Enrollment(
                    patient_id=facts.patient_id,
                    program_code=program.code,
                    tier_code=tier.code,
                    tier_priority=tier.priority,
                    evidence={**eligibility_evidence, **tier_evidence},
                ),
                tier,
                None,
            )

    warning = (
        f"{facts.patient_id} is eligible for {program.code} but matched no tier; "
        "the program's tiers do not cover every eligible patient"
    )
    return (
        Enrollment(
            patient_id=facts.patient_id,
            program_code=program.code,
            tier_code=None,
            tier_priority=None,
            evidence=eligibility_evidence,
        ),
        None,
        warning,
    )
