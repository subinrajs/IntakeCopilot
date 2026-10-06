"""Queue, case detail, review actions and export. All checks are server-side."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from intake import audit, review
from intake.api.deps import Conn
from intake.auth import RadiologistUser, ReviewerOrAdmin, StaffUser
from intake.cases import case_detail, list_cases
from intake.storage import get_storage

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("")
def queue(
    conn: Conn,
    user: StaffUser,
    status: Annotated[list[str] | None, Query()] = None,
    priority: str | None = None,
) -> dict[str, Any]:
    return {"cases": list_cases(conn, status, priority)}


@router.get("/{case_id}")
def detail(
    case_id: uuid.UUID,
    conn: Conn,
    user: StaffUser,
) -> dict[str, Any]:
    found = case_detail(conn, case_id)
    if found is None:
        raise HTTPException(404, "case not found")
    audit.record(conn, user.id, "case.view", "requisition", str(case_id))
    return found


@router.get("/{case_id}/pages/{page_no}")
def page_image(
    case_id: uuid.UUID,
    page_no: int,
    conn: Conn,
    user: StaffUser,
) -> Response:
    row = conn.execute(
        "SELECT p.image_key FROM requisition_pages p "
        "JOIN requisitions r ON r.id = p.requisition_id "
        "WHERE p.requisition_id = %s AND p.page_no = %s AND r.source = 'live'",
        (case_id, page_no),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "page not found")
    return Response(
        get_storage().get(row["image_key"]),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{case_id}/file")
def original_file(
    case_id: uuid.UUID,
    conn: Conn,
    user: StaffUser,
) -> Response:
    row = conn.execute(
        "SELECT file_key, content_type FROM requisitions WHERE id = %s AND source = 'live'",
        (case_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "case not found")
    audit.record(conn, user.id, "case.download", "requisition", str(case_id))
    return Response(
        get_storage().get(row["file_key"]),
        media_type=row["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{case_id}/open")
def open_case(
    case_id: uuid.UUID,
    conn: Conn,
    user: RadiologistUser,
) -> dict[str, bool]:
    review.open_case(conn, case_id, user)
    return {"ok": True}


@router.post("/{case_id}/release")
def release_case(
    case_id: uuid.UUID,
    conn: Conn,
    user: ReviewerOrAdmin,
) -> dict[str, bool]:
    review.release_case(conn, case_id, user)
    return {"ok": True}


@router.patch("/{case_id}/fields")
def correct_fields(
    case_id: uuid.UUID,
    changes: dict[str, Any],
    conn: Conn,
    user: StaffUser,
) -> dict[str, list[str]]:
    return {"changed": review.correct_fields(conn, case_id, user, changes)}


@router.post("/{case_id}/manual-entry/complete")
def complete_manual_entry(
    case_id: uuid.UUID,
    conn: Conn,
    user: StaffUser,
) -> dict[str, bool]:
    review.complete_manual_entry(conn, case_id, user)
    return {"ok": True}


@router.post("/{case_id}/decision")
def decide(
    case_id: uuid.UUID,
    body: review.DecisionRequest,
    conn: Conn,
    user: RadiologistUser,
) -> dict[str, str]:
    return {"status": review.decide(conn, case_id, user, body)}


@router.get("/{case_id}/hl7", response_class=PlainTextResponse)
def hl7(
    case_id: uuid.UUID,
    conn: Conn,
    user: ReviewerOrAdmin,
) -> str:
    message = review.hl7_for(conn, case_id)
    audit.record(conn, user.id, "case.view_hl7", "requisition", str(case_id))
    return message.replace("\r", "\n")


@router.post("/{case_id}/export", response_class=PlainTextResponse)
def export(
    case_id: uuid.UUID,
    conn: Conn,
    user: ReviewerOrAdmin,
) -> str:
    return review.export_case(conn, case_id, user).replace("\r", "\n")
