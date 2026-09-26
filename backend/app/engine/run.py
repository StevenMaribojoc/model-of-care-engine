"""The engine entry point.

    evaluate(records, rules, as_of) -> RunResult

A pure function: same inputs, same output, no clock, no database, no network. That
is what makes it testable with three hand-written patients, runnable as a batch job
outside the API, and safe to re-run for any historical date.

Patients are evaluated independently, which is the property that makes this scale.
The loop is embarrassingly parallel: partition by patient id and the work fans out
across processes or workers with no coordination.
"""

from __future__ import annotations

import logging
from datetime import date

from app.domain.models import Enrollment, Need, PatientRecord, RunResult
from app.engine.facts import build_facts
from app.engine.resolvers import NeedContext, registered_need_types, resolver_for
from app.engine.stratify import stratify
from app.rules.schema import RulesBundle

logger = logging.getLogger(__name__)


def evaluate(
    records: tuple[PatientRecord, ...] | list[PatientRecord],
    rules: RulesBundle,
    as_of: date,
) -> RunResult:
    _assert_resolvers_exist(rules)

    enrollments: list[Enrollment] = []
    needs: list[Need] = []
    warnings: list[str] = []

    for record in records:
        # Once per patient, not once per program: every program below reads the
        # same digested view.
        facts = build_facts(record, as_of)

        for program in rules.active_programs:
            enrollment, tier, warning = stratify(facts, program)
            if warning:
                warnings.append(warning)
            if enrollment is None:
                continue
            enrollments.append(enrollment)
            if tier is None:
                continue  # eligible but untiered: no needs to derive

            ctx = NeedContext(
                rules=rules,
                program_code=program.code,
                tier_code=tier.code,
                priority=tier.priority,
            )
            for requirement in tier.needs:
                resolve = resolver_for(requirement.need_type)
                needs.append(resolve(requirement, facts, ctx))

    result = RunResult(
        as_of=as_of,
        rules_version=rules.version,
        enrollments=tuple(enrollments),
        needs=tuple(needs),
        tasks=(),
        warnings=tuple(warnings),
    )
    logger.info(
        "evaluated %d patients as of %s: %d enrollments, %d needs, %d warnings",
        len(records),
        as_of,
        len(enrollments),
        len(needs),
        len(warnings),
    )
    return result


def _assert_resolvers_exist(rules: RulesBundle) -> None:
    """Fail before the first patient rather than partway through the population.

    The loader checks that every need type is *declared*; this checks that one is
    actually *implemented*. Splitting the two keeps the loader free of any
    dependency on the engine.
    """
    required = {
        need.need_type
        for program in rules.active_programs
        for tier in program.tiers
        for need in tier.needs
    }
    missing = sorted(required - set(registered_need_types()))
    if missing:
        raise KeyError(
            f"rules require need types with no registered resolver: {missing}"
        )
