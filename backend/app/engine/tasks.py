"""Needs -> tasks.

Needs and tasks are separate on purpose, and the distinction is the heart of the
model:

    a need is clinical  -- "this patient should see Endocrinology every 90 days",
                           recorded per program because quality is measured per
                           program;
    a task is operational -- "somebody book that appointment", recorded per unit
                           of work because a human has to do it once.

Those cardinalities genuinely differ. A high-risk diabetic who is also in a future
cardiac program has two Cardiology needs and must generate exactly one task: two
tasks would mean two phone calls, two appointments, and a patient who stops
answering the phone. Conversely one appointment closes both needs, and the
task_need link is what lets the system report that.

This module is deliberately generic. It never asks what a need *is* -- routing was
already decided by the resolver that understands the need type. Adding lab orders
changes nothing here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.domain.models import Need, Task, TaskType


def build_tasks(needs: tuple[Need, ...] | list[Need]) -> tuple[Task, ...]:
    """Group actionable needs into deduplicated, prioritised tasks."""
    groups: dict[tuple[str, str, str], list[Need]] = defaultdict(list)
    for need in needs:
        if not need.generates_task:
            continue
        # The merge key is what a staff member would actually do: this patient,
        # this kind of action, this target. Program is deliberately absent.
        groups[(need.patient_id, need.need_type, need.target)].append(need)

    tasks = [_merge(group) for group in groups.values()]

    # Stable, meaningful worklist order: most urgent tier first, then the longest
    # overdue, then patient id purely to make the output deterministic. A worklist
    # that reshuffles between identical runs is one staff stop trusting.
    tasks.sort(key=lambda t: (t.priority, t.due_date or date.max, t.patient_id))
    return tuple(tasks)


def _merge(group: list[Need]) -> Task:
    first = group[0]
    return Task(
        patient_id=first.patient_id,
        task_type=_task_type_for(group),
        need_type=first.need_type,
        target=first.target,
        # Strictest wins on both axes: the most urgent program driving this care
        # sets the priority, and the earliest due date sets the deadline. Merging
        # must never make a patient look less urgent than one of their needs says.
        priority=min(need.priority for need in group),
        due_date=min(
            (need.due_date for need in group if need.due_date is not None),
            default=None,
        ),
        need_keys=tuple(sorted(need.key for need in group)),
        program_codes=tuple(sorted({need.program_code for need in group})),
    )


def _task_type_for(group: list[Need]) -> TaskType:
    """Resolve the task type for a merged group.

    In practice a group is homogeneous: whether a patient has ever seen a
    specialty is a fact about the patient, not about the program asking. If that
    ever stops holding, a referral outranks a scheduling task -- booking an
    appointment that a clinician has not yet approved is the worse error.
    """
    types = {need.task_type for need in group}
    if TaskType.REFERRAL in types:
        return TaskType.REFERRAL
    return next(iter(types))
