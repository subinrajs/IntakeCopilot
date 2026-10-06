"""Upload and live pipeline progress (server-sent events)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from intake import audit
from intake.api.deps import Conn
from intake.auth import IntakeUser, StaffUser
from intake.pipeline import queue
from intake.pipeline.orchestrator import file_sha256
from intake.settings import get_settings
from intake.spend import spent_today_usd
from intake.storage import get_storage

router = APIRouter(prefix="/api", tags=["requisitions"])
SIGNATURES = (
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
)
HEARTBEAT_SECONDS = 15


def sniff(data: bytes) -> tuple[str, str] | None:
    """Content type from the file's own bytes, never from the client's claim."""
    for magic, content_type, ext in SIGNATURES:
        if data.startswith(magic):
            return content_type, ext
    return None


@router.post("/requisitions", status_code=status.HTTP_201_CREATED)
async def upload(
    files: list[UploadFile],
    conn: Conn,
    user: IntakeUser,
) -> dict[str, Any]:
    settings = get_settings()
    if not files:
        raise HTTPException(422, "no files")
    today = conn.execute(
        "SELECT count(*) AS n FROM requisitions WHERE uploaded_by = %s "
        "AND uploaded_at > now() - interval '1 day'",
        (user.id,),
    ).fetchone()
    if today and today["n"] + len(files) > settings.max_uploads_per_user_per_day:
        raise HTTPException(429, "daily upload limit reached for this demo account")
    if spent_today_usd(conn) >= settings.daily_spend_cap_usd:
        raise HTTPException(429, "the demo's daily model budget is used up; try tomorrow")
    created: list[dict[str, str]] = []
    storage = get_storage()
    for upload_file in files:
        data = await upload_file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(
                413,
                f"{upload_file.filename}: larger than "
                f"{settings.max_upload_bytes // (1024 * 1024)} MB",
            )
        kind = sniff(data)
        if kind is None:
            raise HTTPException(415, f"{upload_file.filename}: only PDF, PNG and JPEG are accepted")
        content_type, ext = kind
        case_id = uuid.uuid4()
        key = f"requisitions/{case_id}/original.{ext}"
        storage.put(key, data)
        conn.execute(
            """INSERT INTO requisitions (id, file_key, file_sha256, content_type,
                 original_filename, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                case_id,
                key,
                file_sha256(data),
                content_type,
                (upload_file.filename or "upload")[:200],
                user.id,
            ),
        )
        queue.enqueue(
            conn,
            queue.PROCESS_REQUISITION,
            {"requisition_id": str(case_id)},
            dedupe_key=f"process:{case_id}",
        )
        audit.record(
            conn,
            user.id,
            "requisition.upload",
            "requisition",
            str(case_id),
            {"content_type": content_type, "bytes": len(data)},
        )
        created.append({"id": str(case_id), "filename": upload_file.filename or ""})
    return {"cases": created}


async def _events(request: Request, case_id: str | None) -> AsyncIterator[str]:
    """Postgres LISTEN -> SSE. Payloads carry ids only; the browser refetches through the API."""
    settings = get_settings()
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as conn:
        await conn.execute("LISTEN intake_events")
        yield "event: ready\ndata: {}\n\n"
        generator = conn.notifies(timeout=HEARTBEAT_SECONDS)
        while not await request.is_disconnected():
            try:
                async for notify in generator:
                    payload: dict[str, Any] = json.loads(notify.payload)
                    if case_id is None or payload.get("requisition_id") == case_id:
                        yield f"event: change\ndata: {json.dumps(payload)}\n\n"
                    if await request.is_disconnected():
                        return
            except asyncio.CancelledError:
                return
            yield ": heartbeat\n\n"
            generator = conn.notifies(timeout=HEARTBEAT_SECONDS)


def _sse(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/requisitions/{case_id}/events")
async def case_events(
    case_id: uuid.UUID,
    request: Request,
    user: StaffUser,
) -> StreamingResponse:
    return _sse(_events(request, str(case_id)))


@router.get("/events")
async def all_events(
    request: Request,
    user: StaffUser,
) -> StreamingResponse:
    return _sse(_events(request, None))
