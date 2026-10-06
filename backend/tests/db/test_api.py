"""HTTP API: auth, role checks on every endpoint, upload validation and the review flow.

Includes design-doc scenario 3 (approval blocked until contrast flags are acknowledged) and
scenario 6 (an override without a reason is rejected by the API, not just the UI).
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from intake import db
from intake.api.app import app
from intake.auth import CSRF_HEADER
from intake.llm.oracle_client import OracleClient
from intake.pipeline.orchestrator import PipelineDeps, process
from intake.settings import get_settings

pytestmark = pytest.mark.db
PASSWORD = get_settings().seed_staff_password


def client_for(user: str | None) -> TestClient:
    client = TestClient(app, headers={CSRF_HEADER: "1"})
    if user is not None:
        response = client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
        assert response.status_code == 200, response.text
    return client


@pytest.fixture
def intake(db_ready: None) -> Iterator[TestClient]:
    yield client_for("intake")


@pytest.fixture
def rad(db_ready: None) -> Iterator[TestClient]:
    yield client_for("radiologist")


@pytest.fixture
def rad2(db_ready: None) -> Iterator[TestClient]:
    yield client_for("radiologist2")


@pytest.fixture
def admin(db_ready: None) -> Iterator[TestClient]:
    yield client_for("admin")


def gold_pdf(case_key: str) -> bytes:
    return (get_settings().data_dir / "gold" / "v1" / "pdfs" / f"{case_key}.pdf").read_bytes()


def upload_and_process(client: TestClient, case_key: str) -> str:
    response = client.post(
        "/api/requisitions", files=[("files", (f"{case_key}.pdf", gold_pdf(case_key)))]
    )
    assert response.status_code == 201, response.text
    case_id = response.json()["cases"][0]["id"]
    asyncio.run(process(uuid.UUID(case_id), PipelineDeps.create(OracleClient())))
    return str(case_id)


# --------------------------------------------------------------------------- auth


def test_login_rejects_wrong_password_and_unknown_user_alike(db_ready: None) -> None:
    client = TestClient(app)
    wrong = client.post("/api/auth/login", json={"username": "intake", "password": "nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_session_cookie_is_httponly_and_scoped(db_ready: None) -> None:
    response = TestClient(app).post(
        "/api/auth/login", json={"username": "intake", "password": PASSWORD}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    assert response.json()["role"] == "intake"


def test_requests_without_a_session_are_rejected(db_ready: None) -> None:
    assert TestClient(app).get("/api/cases").status_code == 401


def test_mutations_without_csrf_header_are_rejected(db_ready: None) -> None:
    client = client_for("intake")
    del client.headers[CSRF_HEADER]
    response = client.post("/api/requisitions", files=[("files", ("a.pdf", b"%PDF-1.4"))])
    assert response.status_code == 403


# --------------------------------------------------------------------------- role matrix

CASE = "00000000-0000-0000-0000-000000000000"
ENDPOINTS: list[tuple[str, str, set[str]]] = [
    ("GET", "/api/cases", {"intake", "radiologist", "admin"}),
    ("GET", f"/api/cases/{CASE}", {"intake", "radiologist", "admin"}),
    ("PATCH", f"/api/cases/{CASE}/fields", {"intake", "radiologist", "admin"}),
    ("POST", f"/api/cases/{CASE}/open", {"radiologist"}),
    ("POST", f"/api/cases/{CASE}/decision", {"radiologist"}),
    ("GET", f"/api/cases/{CASE}/hl7", {"radiologist", "admin"}),
    ("POST", f"/api/cases/{CASE}/export", {"radiologist", "admin"}),
    ("POST", "/api/requisitions", {"intake", "admin"}),
    ("GET", "/api/protocols", {"intake", "radiologist", "admin"}),
    ("PUT", "/api/protocols/MRI-KNEE", {"admin"}),
    ("POST", "/api/eval/runs", {"admin"}),
    ("GET", "/api/audit", {"admin"}),
    ("GET", "/api/admin/ops", {"admin"}),
]


@pytest.mark.parametrize(("method", "path", "allowed"), ENDPOINTS)
@pytest.mark.parametrize("role", ["intake", "radiologist", "admin"])
def test_role_checks_on_every_endpoint(
    db_ready: None, method: str, path: str, allowed: set[str], role: str
) -> None:
    response = client_for(role).request(method, path, json={})
    if role in allowed:
        assert response.status_code != 403, response.text
    else:
        assert response.status_code == 403


# --------------------------------------------------------------------------- upload


def test_upload_rejects_files_that_are_not_pdf_or_images(intake: TestClient) -> None:
    response = intake.post("/api/requisitions", files=[("files", ("x.pdf", b"MZ\x90\x00exe"))])
    assert response.status_code == 415


def test_upload_creates_a_case_a_job_and_an_audit_row(intake: TestClient) -> None:
    response = intake.post(
        "/api/requisitions", files=[("files", ("req.pdf", gold_pdf("gold-v1-001")))]
    )
    case_id = response.json()["cases"][0]["id"]
    with db.connection() as conn:
        job = conn.execute(
            "SELECT * FROM jobs WHERE payload->>'requisition_id' = %s", (case_id,)
        ).fetchone()
        audit_row = conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = %s AND action = 'requisition.upload'",
            (case_id,),
        ).fetchone()
    assert job is not None and job["status"] == "queued"
    assert audit_row is not None and audit_row["actor"] == "intake"


# --------------------------------------------------------------------------- queue and review


def test_queue_pins_red_flag_cases_first(intake: TestClient) -> None:
    routine = upload_and_process(intake, "gold-v1-004")  # P4
    urgent = upload_and_process(intake, "gold-v1-041")  # buried cauda equina (P1)
    cases = intake.get("/api/cases", params={"status": "ready_for_review"}).json()["cases"]
    ids = [c["id"] for c in cases]
    assert ids.index(urgent) < ids.index(routine)
    first = next(c for c in cases if c["id"] == urgent)
    assert first["pinned"] is True and first["priority"] == "P1"


def _approve_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": "approve",
        "priority": {"action": "accept"},
        "protocol": {"action": "accept"},
        "acknowledged_flags": [],
    }
    return body | overrides


def test_review_lock_blocks_a_second_radiologist(
    intake: TestClient, rad: TestClient, rad2: TestClient
) -> None:
    case_id = upload_and_process(intake, "gold-v1-003")
    assert rad.post(f"/api/cases/{case_id}/open").status_code == 200
    blocked = rad2.post(f"/api/cases/{case_id}/open")
    assert blocked.status_code == 409 and "radiologist" in blocked.json()["detail"]
    assert rad2.post(f"/api/cases/{case_id}/decision", json=_approve_body()).status_code == 409


def test_scenario_6_override_without_reason_is_rejected_by_the_api(
    intake: TestClient, rad: TestClient
) -> None:
    case_id = upload_and_process(intake, "gold-v1-003")
    rad.post(f"/api/cases/{case_id}/open")
    body = _approve_body(priority={"action": "override", "value": "P2", "reason": "  "})
    response = rad.post(f"/api/cases/{case_id}/decision", json=body)
    assert response.status_code == 422 and "reason" in response.json()["detail"]
    reject = rad.post(f"/api/cases/{case_id}/decision", json={"action": "reject"})
    assert reject.status_code == 422


def test_scenario_3_approval_blocked_until_contrast_flags_acknowledged(
    intake: TestClient, rad: TestClient
) -> None:
    case_id = upload_and_process(intake, "gold-v1-045")  # eGFR 25 with contrast
    rad.post(f"/api/cases/{case_id}/open")
    blocked = rad.post(f"/api/cases/{case_id}/decision", json=_approve_body())
    assert blocked.status_code == 422 and "egfr_low" in blocked.json()["detail"]
    approved = rad.post(
        f"/api/cases/{case_id}/decision", json=_approve_body(acknowledged_flags=["egfr_low"])
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    with db.connection() as conn:
        kinds = conn.execute(
            "SELECT kind, action, final_value FROM decisions WHERE requisition_id = %s ORDER BY id",
            (case_id,),
        ).fetchall()
    assert [(k["kind"], k["action"]) for k in kinds] == [
        ("priority", "accept"),
        ("protocol", "accept"),
        ("contrast", "acknowledge"),
        ("case", "accept"),
    ]


def test_override_with_reason_then_hl7_export(intake: TestClient, rad: TestClient) -> None:
    case_id = upload_and_process(intake, "gold-v1-003")
    rad.post(f"/api/cases/{case_id}/open")
    body = _approve_body(
        priority={"action": "override", "value": "P2", "reason": "Locked knee, cannot weight bear"}
    )
    assert rad.post(f"/api/cases/{case_id}/decision", json=body).status_code == 200
    message = rad.post(f"/api/cases/{case_id}/export").text
    segments = [line.split("|")[0] for line in message.strip().split("\n")]
    assert segments[:5] == ["MSH", "PID", "PV1", "ORC", "OBR"]
    assert "|A|" in message  # P2 -> ASAP
    rad.get(f"/api/cases/{case_id}")  # each view is audited (seen on the next read)
    detail = rad.get(f"/api/cases/{case_id}").json()
    assert detail["status"] == "exported"
    actions = [h["action"] for h in detail["history"]]
    assert "case.export_hl7" in actions and "case.view" in actions


def test_correcting_egfr_reruns_the_contrast_rules(intake: TestClient, rad: TestClient) -> None:
    case_id = upload_and_process(intake, "gold-v1-044")  # contrast, no eGFR -> needs_labs
    assert rad.get(f"/api/cases/{case_id}").json()["contrast"]["output"]["result"] == "needs_labs"
    rad.post(f"/api/cases/{case_id}/open")
    changed = rad.patch(
        f"/api/cases/{case_id}/fields", json={"egfr": 72, "egfr_date": "2026-09-01"}
    )
    assert changed.json()["changed"] == ["egfr", "egfr_date"]
    detail = rad.get(f"/api/cases/{case_id}").json()
    assert detail["contrast"]["output"]["result"] == "clear"
    assert {c["field"] for c in detail["corrections"]} == {"egfr", "egfr_date"}


def test_invalid_field_values_are_rejected(intake: TestClient) -> None:
    case_id = upload_and_process(intake, "gold-v1-003")
    response = intake.patch(f"/api/cases/{case_id}/fields", json={"dob": "not a date"})
    assert response.status_code == 422
    response = intake.patch(f"/api/cases/{case_id}/fields", json={"health_card_last4": "12"})
    assert response.status_code == 422


def test_manual_entry_cannot_complete_without_required_fields(intake: TestClient) -> None:
    response = intake.post("/api/requisitions", files=[("files", ("bad.pdf", b"%PDF-broken"))])
    case_id = response.json()["cases"][0]["id"]
    asyncio.run(process(uuid.UUID(case_id), PipelineDeps.create(OracleClient())))
    assert intake.get(f"/api/cases/{case_id}").json()["status"] == "manual_entry"
    blocked = intake.post(f"/api/cases/{case_id}/manual-entry/complete")
    assert blocked.status_code == 422 and "patient_name" in blocked.json()["detail"]
