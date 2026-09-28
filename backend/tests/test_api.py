"""API tests, weighted toward role visibility.

Visibility is the one rule here that is a safety property rather than a feature:
every other bug shows a wrong number, this one shows the wrong person the wrong
work. So it is tested from the outside, the way a client would actually try it --
including the case of a scheduler explicitly asking for referral tasks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client():
    # The context manager runs lifespan, which rebuilds the database from the
    # CSVs, so these tests exercise the real dataset end to end.
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_reports_the_active_rules_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["rules_version"]


def test_meta_is_derived_from_configuration(client):
    """The frontend builds its filters from this, so a new program must appear
    here without any frontend change."""
    body = client.get("/api/meta").json()
    assert {p["code"] for p in body["programs"]} == {"PCP_WELLNESS", "DIABETES_MGMT"}
    assert {s["code"] for s in body["specialties"]} >= {"PCP", "Endocrinology"}
    assert body["specialties"][0]["code"] == "PCP"
    assert {r["code"] for r in body["roles"]} == {"SCHEDULER", "CLINICAL"}


# --------------------------------------------------------------------------- #
# Role visibility
# --------------------------------------------------------------------------- #


def test_scheduler_sees_only_scheduling_tasks(client):
    body = client.get("/api/tasks", params={"role": "SCHEDULER", "limit": 500}).json()
    assert body["total"] > 0
    assert {item["task_type"] for item in body["items"]} == {"SCHEDULING"}


def test_clinical_sees_both_task_types(client):
    body = client.get("/api/tasks", params={"role": "CLINICAL", "limit": 500}).json()
    assert {item["task_type"] for item in body["items"]} == {"SCHEDULING", "REFERRAL"}


def test_scheduler_asking_for_referrals_gets_nothing(client):
    """The filter is intersected with the role, not trusted.

    This is the attack: the client requests a task type it may not see. The
    correct response is an empty page -- not an error that reveals the rows
    exist, and certainly not the rows.
    """
    body = client.get(
        "/api/tasks", params={"role": "SCHEDULER", "task_type": "REFERRAL"}
    ).json()
    assert body["total"] == 0
    assert body["items"] == []


def test_clinical_sees_strictly_more_than_scheduler(client):
    scheduler = client.get("/api/tasks", params={"role": "SCHEDULER"}).json()["total"]
    clinical = client.get("/api/tasks", params={"role": "CLINICAL"}).json()["total"]
    assert clinical > scheduler


def test_role_visibility_applies_to_patient_and_summary_endpoints_too(client):
    """Visibility cannot be a property of one endpoint.

    A leak through the patient list would be just as bad as one through the
    worklist, so the same intersection has to hold everywhere tasks are returned.
    """
    patients = client.get(
        "/api/patients", params={"role": "SCHEDULER", "limit": 500}
    ).json()
    leaked = [
        task
        for patient in patients["items"]
        for task in patient["tasks"]
        if task["task_type"] != "SCHEDULING"
    ]
    assert leaked == []

    summary = client.get("/api/summary", params={"role": "SCHEDULER"}).json()
    assert set(summary["tasks_by_type"]) == {"SCHEDULING"}


def test_an_unknown_role_is_rejected(client):
    assert client.get("/api/tasks", params={"role": "ADMIN"}).status_code == 422


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def test_specialty_filter_narrows_to_that_specialty(client):
    body = client.get(
        "/api/tasks",
        params={"role": "CLINICAL", "specialty": "Endocrinology", "limit": 500},
    ).json()
    assert body["total"] > 0
    assert {item["target"] for item in body["items"]} == {"Endocrinology"}


def test_pcp_tasks_are_never_referrals(client):
    """The spec's hard constraint, checked through the API."""
    body = client.get(
        "/api/tasks", params={"role": "CLINICAL", "specialty": "PCP", "limit": 500}
    ).json()
    assert body["total"] > 0
    assert {item["task_type"] for item in body["items"]} == {"SCHEDULING"}


def test_patient_specialty_filter_means_open_gap_not_any_need(client):
    """"Show me everyone who needs endocrinology" must not include the patients
    whose endocrinology visit is already up to date."""
    body = client.get(
        "/api/patients",
        params={"specialty": "Endocrinology", "role": "CLINICAL", "limit": 500},
    ).json()
    assert body["total"] > 0
    for patient in body["items"]:
        endo = [n for n in patient["needs"] if n["target"] == "Endocrinology"]
        assert any(n["status"] in ("DUE", "NEVER_SEEN") for n in endo)


def test_program_and_tier_filters(client):
    diabetes = client.get(
        "/api/patients", params={"program": "DIABETES_MGMT", "limit": 1}
    ).json()
    assert diabetes["total"] == 115  # verified independently against the CSVs

    high_risk = client.get("/api/patients", params={"tier": "HIGH_RISK", "limit": 1}).json()
    assert high_risk["total"] == 29


def test_pagination_reports_total_independently_of_page_size(client):
    first = client.get("/api/tasks", params={"role": "CLINICAL", "limit": 5}).json()
    assert len(first["items"]) == 5
    assert first["total"] > 5

    second = client.get(
        "/api/tasks", params={"role": "CLINICAL", "limit": 5, "offset": 5}
    ).json()
    assert second["total"] == first["total"]
    assert {i["task_id"] for i in first["items"]} & {
        i["task_id"] for i in second["items"]
    } == set()


# --------------------------------------------------------------------------- #
# Patient detail and as_of
# --------------------------------------------------------------------------- #


def test_unknown_patient_returns_404(client):
    assert client.get("/api/patients/NOPE").status_code == 404


def test_patient_detail_carries_the_evidence_for_each_tier(client):
    """The "why is this patient here?" contract.

    P0002 is in both programs, which is also the multi-program edge case: the
    same diagnosis qualifies them for diabetes management and marks them chronic
    for wellness, and both enrollments must say so.
    """
    body = client.get("/api/patients/P0002", params={"role": "CLINICAL"}).json()
    by_program = {e["program_code"]: e for e in body["enrollments"]}
    assert set(by_program) == {"PCP_WELLNESS", "DIABETES_MGMT"}

    diabetes = by_program["DIABETES_MGMT"]
    assert diabetes["tier_code"] == "HIGH_RISK"
    assert diabetes["evidence"]["value"] >= 9.0
    assert diabetes["evidence"]["result_date"]

    wellness = by_program["PCP_WELLNESS"]
    assert wellness["tier_code"] == "HIGH_PRIORITY"
    assert wellness["evidence"]["matched_codes"]


def test_upcoming_appointment_shows_as_scheduled_with_no_task(client):
    """P0002 has never seen Cardiology but has an appointment booked, so the
    need is SCHEDULED and no Cardiology task exists for them."""
    body = client.get("/api/patients/P0002", params={"role": "CLINICAL"}).json()
    cardiology = [n for n in body["needs"] if n["target"] == "Cardiology"][0]
    assert cardiology["status"] == "SCHEDULED"
    assert cardiology["last_completed_date"] is None
    assert cardiology["next_scheduled_date"] is not None
    assert not [t for t in body["tasks"] if t["target"] == "Cardiology"]


def test_as_of_cannot_see_facts_that_had_not_happened_yet(client):
    """Evaluating in the past must reproduce what was knowable then.

    P0002 is diagnosed with Type 2 diabetes after 2025-06-01, so at that date
    they are not in the diabetes program at all and their later labs are flagged
    as being beyond the evaluation date.
    """
    body = client.get(
        "/api/patients/P0002", params={"role": "CLINICAL", "as_of": "2025-06-01"}
    ).json()
    assert {e["program_code"] for e in body["enrollments"]} == {"PCP_WELLNESS"}
    assert all(lab["after_as_of"] for lab in body["history"]["labs"])


def test_changing_as_of_produces_a_different_run(client):
    """Runs are identified by (as_of, rules_version), so each date gets its own
    result set rather than overwriting the last."""
    default = client.get("/api/summary", params={"role": "CLINICAL"}).json()
    past = client.get(
        "/api/summary", params={"role": "CLINICAL", "as_of": "2025-04-08"}
    ).json()
    assert default["run"]["run_id"] != past["run"]["run_id"]
    assert default["run"]["as_of"] != past["run"]["as_of"]
    assert default["tasks_by_type"] != past["tasks_by_type"]


def test_repeating_a_request_reuses_the_cached_run(client):
    first = client.get("/api/summary", params={"role": "CLINICAL"}).json()
    second = client.get("/api/summary", params={"role": "SCHEDULER"}).json()
    assert first["run"]["run_id"] == second["run"]["run_id"]


def test_summary_counts_gaps_that_generate_no_work(client):
    """The no-PCP-history population is invisible in any task count, so it gets
    a number of its own."""
    summary = client.get("/api/summary", params={"role": "CLINICAL"}).json()
    assert summary["unactionable_gaps"] == 72


# --------------------------------------------------------------------------- #
# Human state (prototype)
# --------------------------------------------------------------------------- #


def _first_task(client, **params):
    body = client.get("/api/tasks", params={"role": "CLINICAL", "limit": 1, **params}).json()
    return body["items"][0]


def test_a_task_starts_with_no_human_state(client):
    assert _first_task(client)["state"] is None


def test_claiming_a_task_records_who_and_what(client):
    task = _first_task(client, search="P0081", specialty="PCP")
    body = client.patch(
        f"/api/tasks/{task['task_id']}/state",
        params={"role": "CLINICAL"},
        json={"status": "IN_PROGRESS", "assignee": "Maria Alvarez", "note": "left voicemail"},
    ).json()
    assert body["status"] == "IN_PROGRESS"
    assert body["assignee"] == "Maria Alvarez"
    assert body["updated_by_role"] == "CLINICAL"


def test_a_partial_update_does_not_clear_the_other_fields(client):
    """Setting a note must not wipe an assignee. Two people edit these."""
    task = _first_task(client, search="P0081", specialty="PCP")
    url = f"/api/tasks/{task['task_id']}/state"
    client.patch(url, params={"role": "CLINICAL"}, json={"assignee": "Devon Park"})
    body = client.patch(url, params={"role": "CLINICAL"}, json={"note": "second call"}).json()
    assert body["assignee"] == "Devon Park"
    assert body["note"] == "second call"


def test_a_role_cannot_write_to_a_task_it_cannot_see(client):
    """Authorisation on the write path, not just on reads.

    A scheduler cannot see referral tasks, so it must not be able to claim one
    either -- and the response is the same 404 it would get for a task that does
    not exist, so this cannot be used to discover hidden work.
    """
    referral = _first_task(client, task_type="REFERRAL")
    denied = client.patch(
        f"/api/tasks/{referral['task_id']}/state",
        params={"role": "SCHEDULER"},
        json={"status": "IN_PROGRESS"},
    )
    assert denied.status_code == 404

    allowed = client.patch(
        f"/api/tasks/{referral['task_id']}/state",
        params={"role": "CLINICAL"},
        json={"status": "IN_PROGRESS"},
    )
    assert allowed.status_code == 200


def test_completion_cannot_be_set_by_hand(client):
    """Completion is a clinical fact, derived from the encounter feed.

    Allowing staff to tick a box is how a worklist drifts away from what
    actually happened to the patient.
    """
    task = _first_task(client)
    refused = client.patch(
        f"/api/tasks/{task['task_id']}/state",
        params={"role": "CLINICAL"},
        json={"status": "COMPLETED"},
    )
    assert refused.status_code == 422


def test_human_state_survives_the_engine_regenerating_tasks(client):
    """The whole reason this table is keyed the way it is.

    Task rows are destroyed and recreated on every run. State is stored against
    the natural key -- patient, action, specialty -- so it reattaches to the
    regenerated task rather than being orphaned.
    """
    from app.bootstrap import rebuild_database
    from app.services.runs import ensure_run
    from app.db.session import session_scope
    from app.settings import settings

    task = _first_task(client, search="P0149", specialty="PCP")
    client.patch(
        f"/api/tasks/{task['task_id']}/state",
        params={"role": "CLINICAL"},
        json={"assignee": "Priya Nair", "note": "survives a rebuild"},
    )

    # Nuke and rebuild everything, exactly as a restart would.
    rules, _ = rebuild_database()
    with session_scope() as session:
        ensure_run(session, settings.default_as_of, rules)

    after = _first_task(client, search="P0149", specialty="PCP")
    assert after["state"]["assignee"] == "Priya Nair"
    assert after["state"]["note"] == "survives a rebuild"
