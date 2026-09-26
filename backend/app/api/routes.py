"""HTTP surface.

Routes stay thin on purpose: resolve the run, hand the query off, shape the
response. No rule logic and no authorisation decisions live here -- authorisation
is in the query layer, where it cannot be forgotten by a new endpoint.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import (
    PatientDetailOut,
    PatientPageOut,
    SummaryOut,
    TaskPageOut,
)
from app.db.session import get_session
from app.domain.models import Role
from app.rules.loader import get_rules
from app.rules.schema import RulesBundle
from app.services import queries
from app.services.runs import ensure_run
from app.settings import settings

router = APIRouter(prefix="/api")


def rules_dep() -> RulesBundle:
    return get_rules()


AsOf = Annotated[
    date | None,
    Query(
        description=(
            "Evaluation date. Defaults to the dataset's anchor date. Everything "
            "is computed relative to this: encounters after it are upcoming "
            "appointments, and labs after it are invisible."
        )
    ),
]

RoleParam = Annotated[
    Role,
    Query(
        description=(
            "Worklist role. Stands in for a claim on a verified token; visibility "
            "is enforced server-side regardless of what the client requests."
        )
    ),
]


def _resolve(session: Session, rules: RulesBundle, as_of: date | None) -> tuple[int, date]:
    effective = as_of or settings.default_as_of
    return ensure_run(session, effective, rules), effective


@router.get("/meta", summary="Filter options and rule definitions")
def get_meta(
    session: Annotated[Session, Depends(get_session)],
    rules: Annotated[RulesBundle, Depends(rules_dep)],
) -> dict:
    """Everything the frontend needs to render its controls.

    The UI builds its filter dropdowns from this, so adding a program to the YAML
    surfaces it in the interface without a frontend change.
    """
    return queries.get_meta(session, rules, settings.default_as_of)


@router.get("/tasks", response_model=TaskPageOut, summary="Task-centric worklist")
def list_tasks(
    session: Annotated[Session, Depends(get_session)],
    rules: Annotated[RulesBundle, Depends(rules_dep)],
    role: RoleParam = Role.CLINICAL,
    as_of: AsOf = None,
    specialty: str | None = None,
    task_type: str | None = None,
    program: str | None = None,
    tier: str | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskPageOut:
    """The worklist a staff member works from.

    A requested ``task_type`` is intersected with what the role may see, so a
    scheduler asking for REFERRAL receives an empty page rather than the rows.
    """
    run_id, effective = _resolve(session, rules, as_of)
    page = queries.list_tasks(
        session,
        run_id=run_id,
        as_of=effective,
        rules=rules,
        role=role,
        specialty=specialty,
        task_type=task_type,
        program=program,
        tier=tier,
        search=search,
        limit=limit,
        offset=offset,
    )
    return TaskPageOut(
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        items=page.items,
        run=queries.get_run_info(session, run_id),
    )


@router.get("/patients", response_model=PatientPageOut, summary="Patient-centric list")
def list_patients(
    session: Annotated[Session, Depends(get_session)],
    rules: Annotated[RulesBundle, Depends(rules_dep)],
    role: RoleParam = Role.CLINICAL,
    as_of: AsOf = None,
    specialty: Annotated[
        str | None,
        Query(description="Patients with an OPEN gap for this specialty."),
    ] = None,
    task_type: str | None = None,
    program: str | None = None,
    tier: str | None = None,
    need_status: str | None = None,
    search: str | None = None,
    with_tasks_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PatientPageOut:
    run_id, effective = _resolve(session, rules, as_of)
    page = queries.list_patients(
        session,
        run_id=run_id,
        as_of=effective,
        rules=rules,
        role=role,
        specialty=specialty,
        task_type=task_type,
        program=program,
        tier=tier,
        need_status=need_status,
        search=search,
        with_tasks_only=with_tasks_only,
        limit=limit,
        offset=offset,
    )
    return PatientPageOut(
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        items=page.items,
        run=queries.get_run_info(session, run_id),
    )


@router.get(
    "/patients/{patient_id}",
    response_model=PatientDetailOut,
    summary="One patient, with the evidence behind every decision",
)
def get_patient(
    patient_id: str,
    session: Annotated[Session, Depends(get_session)],
    rules: Annotated[RulesBundle, Depends(rules_dep)],
    role: RoleParam = Role.CLINICAL,
    as_of: AsOf = None,
) -> PatientDetailOut:
    """The "why is this patient here?" view: tier evidence plus raw history."""
    run_id, effective = _resolve(session, rules, as_of)
    detail = queries.get_patient(
        session,
        run_id=run_id,
        as_of=effective,
        rules=rules,
        role=role,
        patient_id=patient_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown patient {patient_id}")
    return PatientDetailOut.model_validate(detail)


@router.get("/summary", response_model=SummaryOut, summary="Counts for the header")
def get_summary(
    session: Annotated[Session, Depends(get_session)],
    rules: Annotated[RulesBundle, Depends(rules_dep)],
    role: RoleParam = Role.CLINICAL,
    as_of: AsOf = None,
) -> SummaryOut:
    run_id, _ = _resolve(session, rules, as_of)
    return SummaryOut.model_validate(
        queries.get_summary(session, run_id=run_id, rules=rules, role=role)
    )


@router.get("/health", summary="Liveness and current rule version")
def health(rules: Annotated[RulesBundle, Depends(rules_dep)]) -> dict:
    return {
        "status": "ok",
        "rules_version": rules.version,
        "default_as_of": settings.default_as_of,
    }
