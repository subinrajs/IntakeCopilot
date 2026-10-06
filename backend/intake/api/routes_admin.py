"""Admin: protocol book, evaluation runs, audit trail and operations."""

import asyncio
import threading
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from intake import audit
from intake.api.deps import Conn
from intake.auth import AdminUser, StaffUser
from intake.eval.runner import run_eval
from intake.llm.factory import make_llm
from intake.pipeline import queue
from intake.protocols import Protocol
from intake.retrieval import vector_literal
from intake.settings import get_settings
from intake.spend import spent_today_usd

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/protocols")
def protocols(
    conn: Conn,
    user: StaffUser,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, modality, body_part, name, contrast, indications, slot_minutes, version, "
        "updated_at FROM protocols ORDER BY modality DESC, id"
    ).fetchall()
    return {"protocols": [{**r, "indications": r["indications"].split("; ")} for r in rows]}


class ProtocolUpdate(BaseModel):
    body_part: str
    name: str
    contrast: Literal["none", "iv", "optional"]
    slot_minutes: int = Field(gt=0, le=120)
    indications: list[str] = Field(min_length=1)


@router.put("/protocols/{protocol_id}")
def update_protocol(
    protocol_id: str,
    body: ProtocolUpdate,
    conn: Conn,
    user: AdminUser,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM protocols WHERE id = %s FOR UPDATE", (protocol_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "protocol not found")
    protocol = Protocol(id=protocol_id, modality=row["modality"], **body.model_dump())
    vectors, _ = asyncio.run(make_llm().embed([protocol.retrieval_document()]))  # re-embed
    conn.execute(
        """UPDATE protocols SET body_part = %s, name = %s, contrast = %s, slot_minutes = %s,
             indications = %s, embedding = %s::vector, version = version + 1,
             updated_at = now() WHERE id = %s""",
        (
            body.body_part,
            body.name,
            body.contrast,
            body.slot_minutes,
            "; ".join(body.indications),
            vector_literal(vectors[0]),
            protocol_id,
        ),
    )
    audit.record(
        conn, user.id, "protocol.update", "protocol", protocol_id, {"from_version": row["version"]}
    )
    return {"ok": True, "version": row["version"] + 1}


class EvalRequest(BaseModel):
    prompts: dict[str, str] = {}
    repeat: int = Field(default=1, ge=1, le=2)
    limit: int | None = Field(default=None, ge=1, le=60)
    label: str | None = None


@router.post("/eval/runs", status_code=202)
def start_eval(
    body: EvalRequest,
    conn: Conn,
    user: AdminUser,
) -> dict[str, str]:
    if spent_today_usd(conn) >= get_settings().daily_spend_cap_usd:
        raise HTTPException(429, "daily model budget used up")
    run_id = uuid.uuid4()

    def work() -> None:
        asyncio.run(
            run_eval(
                make_llm(),
                prompt_overrides=body.prompts,
                repeat=body.repeat,
                limit=body.limit,
                label=body.label,
                started_by=user.id,
                run_id=run_id,
            )
        )

    threading.Thread(target=work, name=f"eval-{run_id}", daemon=True).start()
    audit.record(conn, user.id, "eval.start", "eval_run", str(run_id), body.model_dump())
    return {"id": str(run_id)}


@router.get("/eval/runs")
def eval_runs(
    conn: Conn,
    user: StaffUser,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT id, gold_version, prompt_versions, models, label, started_by, started_at,
                  finished_at, status, error, metrics
           FROM eval_runs WHERE repeat_of IS NULL ORDER BY started_at DESC LIMIT 50"""
    ).fetchall()
    return {"runs": rows}


@router.get("/eval/runs/{run_id}")
def eval_run(
    run_id: uuid.UUID,
    conn: Conn,
    user: StaffUser,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM eval_runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "run not found")
    return dict(row)


@router.get("/audit")
def audit_trail(
    conn: Conn,
    user: AdminUser,
    entity: str | None = None,
    id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT id, at, actor, action, entity, entity_id, detail FROM audit_log
           WHERE (%(entity)s::text IS NULL OR entity = %(entity)s::text)
             AND (%(id)s::text IS NULL OR entity_id = %(id)s::text)
           ORDER BY id DESC LIMIT %(limit)s""",
        {"entity": entity, "id": id, "limit": min(limit, 1000)},
    ).fetchall()
    return {"events": rows}


@router.get("/admin/ops")
def ops(conn: Conn, user: AdminUser) -> dict[str, Any]:
    steps = conn.execute(
        """SELECT step, count(*) AS runs,
                  round(avg(latency_ms))::int AS avg_latency_ms,
                  percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                  sum(input_tokens) AS input_tokens, sum(cached_input_tokens) AS cached_tokens,
                  sum(output_tokens) AS output_tokens, sum(cost_usd) AS cost_usd,
                  avg(CASE WHEN valid THEN 0 ELSE 1 END) AS invalid_rate
           FROM pipeline_steps WHERE created_at > now() - interval '7 days'
             AND reused_from IS NULL
           GROUP BY step ORDER BY step"""
    ).fetchall()
    oldest = conn.execute(
        """SELECT coalesce(t.output ->> 'final_priority', 'none') AS priority, count(*) AS cases,
                  min(r.status_changed_at) AS oldest
           FROM requisitions r
           LEFT JOIN LATERAL (SELECT output FROM pipeline_steps s WHERE s.requisition_id = r.id
                AND s.step = 'triage' AND s.eval_run_id IS NULL ORDER BY id DESC LIMIT 1) t
             ON true
           WHERE r.source = 'live' AND r.status = 'ready_for_review'
           GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    jobs = conn.execute(
        "SELECT status, count(*) AS n FROM jobs GROUP BY status ORDER BY status"
    ).fetchall()
    return {
        "steps": steps,
        "waiting_by_priority": oldest,
        "jobs": jobs,
        "queue_depth": queue.depth(conn),
        "spent_today_usd": spent_today_usd(conn),
        "daily_spend_cap_usd": get_settings().daily_spend_cap_usd,
        "llm_backend": get_settings().llm_backend,
    }
