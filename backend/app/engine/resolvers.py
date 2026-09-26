"""Need resolvers: turn "this tier requires X every N days" into "and here is where
that patient actually stands".

One resolver per need type, looked up in a registry. A need type is a triple:

    a declaration in reference.yaml   (what its target names, what task it makes)
    a resolver registered here        (how to tell whether it is met)
    zero changes anywhere else        (schema, task generation, API, and frontend
                                       are all written against need_type/target
                                       rather than against "visit")

Adding lab orders is therefore: uncomment six lines of YAML, write a resolver of
about fifteen lines, register it. No migration, no new table, no API change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from app.domain.models import Need, NeedStatus, PatientFacts
from app.rules.schema import CompiledNeed, RulesBundle


@dataclass(frozen=True)
class NeedContext:
    """Everything a resolver needs beyond the patient's own facts."""

    rules: RulesBundle
    program_code: str
    tier_code: str | None
    priority: int


Resolver = Callable[[CompiledNeed, PatientFacts, NeedContext], Need]

_RESOLVERS: dict[str, Resolver] = {}


def register(need_type: str) -> Callable[[Resolver], Resolver]:
    def decorator(resolver: Resolver) -> Resolver:
        _RESOLVERS[need_type] = resolver
        return resolver

    return decorator


def resolver_for(need_type: str) -> Resolver:
    try:
        return _RESOLVERS[need_type]
    except KeyError:
        raise KeyError(
            f"no resolver registered for need type {need_type!r}; "
            f"registered: {sorted(_RESOLVERS)}"
        ) from None


def registered_need_types() -> tuple[str, ...]:
    return tuple(sorted(_RESOLVERS))


@register("visit")
def resolve_visit(
    requirement: CompiledNeed, facts: PatientFacts, ctx: NeedContext
) -> Need:
    """Decide where a patient stands on a required visit to one specialty.

    The order of these four checks is the whole rule, and it is deliberate:

    1. An upcoming appointment wins over everything. The spec says no task is
       generated when one exists, full stop -- so this is checked before we ask
       whether the patient is overdue, and a patient can be both overdue and
       SCHEDULED. Taken literally: the appointment date is not compared against
       the due date, so an appointment eleven months out still suppresses the
       task. In production that comparison belongs here; see the README.

    2. No completed visit ever means the cadence has nothing to measure from.
       Whether that produces work depends on the specialty, not on the program:
       a specialist needs a clinician's referral decision first, while primary
       care produces no task at all. That branch reads requires_referral from
       reference data rather than testing for "PCP".

    3. Overdue is strictly greater: due on the due date, DUE the day after.

    4. Otherwise the need is met.
    """
    target = requirement.target
    last_completed = facts.last_visit.get(target)
    next_scheduled = facts.next_visit.get(target)
    due_date = (
        last_completed + timedelta(days=requirement.cadence_days)
        if last_completed
        else None
    )

    def build(status: NeedStatus, **overrides) -> Need:
        return Need(
            patient_id=facts.patient_id,
            program_code=ctx.program_code,
            tier_code=ctx.tier_code,
            need_type=requirement.need_type,
            target=target,
            cadence_days=requirement.cadence_days,
            status=status,
            last_completed_date=last_completed,
            next_scheduled_date=next_scheduled,
            note=requirement.note,
            priority=ctx.priority,
            **overrides,
        )

    if next_scheduled is not None:
        return build(NeedStatus.SCHEDULED, due_date=due_date)

    if last_completed is None:
        # Due immediately: there is no prior visit to measure a cadence from, so
        # the gap is open now rather than N days from some date that never was.
        return build(NeedStatus.NEVER_SEEN, due_date=facts.as_of)

    if facts.as_of > due_date:
        return build(NeedStatus.DUE, due_date=due_date)

    return build(NeedStatus.SATISFIED, due_date=due_date)
