"""Read model for live cases: the queue listing and the case detail the review screen shows."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection

from intake.schemas.extraction import RequisitionExtraction
from intake.schemas.fields import CaseFields, apply_corrections, from_extraction

Conn = Connection[dict[str, Any]]

# Review targets from the design doc (demo values, to be set by the Medical Director).
REVIEW_TARGET = {
    "P1": timedelta(hours=1),
    "P2": timedelta(hours=24),
    "P3": timedelta(days=7),
    "P4": timedelta(days=14),
}
# A case with no suggested priority is treated as P1 for sorting and timers (fail safe).
UNKNOWN_PRIORITY_RANK = 0
PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
LOCK_TTL = timedelta(minutes=15)


def latest_steps(conn: Conn, case_id: UUID) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """SELECT DISTINCT ON (step) * FROM pipeline_steps
           WHERE requisition_id = %s AND eval_run_id IS NULL
           ORDER BY step, id DESC""",
        (case_id,),
    ).fetchall()
    return {r["step"]: r for r in rows}


def corrections(conn: Conn, case_id: UUID) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM field_corrections WHERE requisition_id = %s ORDER BY id", (case_id,)
    ).fetchall()


def extraction_of(step: dict[str, Any] | None) -> RequisitionExtraction | None:
    if step is None or step["output"].get("extraction") is None:
        return None
    return RequisitionExtraction.model_validate(step["output"]["extraction"])


def effective_fields(conn: Conn, case_id: UUID) -> CaseFields:
    extraction = extraction_of(latest_steps(conn, case_id).get("extract"))
    fixes = [(c["field_path"], c["corrected_value"]) for c in corrections(conn, case_id)]
    return apply_corrections(from_extraction(extraction), fixes)


def _age(since: datetime, now: datetime) -> timedelta:
    return now - since


def queue_item(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    priority = row["final_priority"]
    target = REVIEW_TARGET.get(priority or "P1")
    waiting = _age(row["status_changed_at"], now)
    breached = row["status"] == "ready_for_review" and target is not None and waiting > target
    red_flags = row["red_flags"] or []
    lock_fresh = row["locked_at"] is not None and now - row["locked_at"] < LOCK_TTL
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "patient_name": row["patient_name"],
        "modality": row["modality"],
        "body_part": row["body_part"],
        "priority": priority,
        "model_priority": row["model_priority"],
        "raised_by_rules": bool(row["raised_by_rules"]),
        "red_flags": red_flags,
        "pinned": any(f.get("level") == "P1" for f in red_flags),
        "protocol_id": row["protocol_id"],
        "contrast_result": row["contrast_result"],
        "uploaded_at": row["uploaded_at"].isoformat(),
        "status_changed_at": row["status_changed_at"].isoformat(),
        "waiting_seconds": int(waiting.total_seconds()),
        "target_seconds": int(target.total_seconds()) if target else None,
        "sla_breached": breached,
        "locked_by": row["locked_by"] if lock_fresh else None,
        "original_filename": row["original_filename"],
    }


def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    rank = PRIORITY_RANK.get(item["priority"] or "", UNKNOWN_PRIORITY_RANK)
    return (0 if item["pinned"] else 1, rank, -item["waiting_seconds"])


def list_cases(
    conn: Conn, statuses: list[str] | None = None, priority: str | None = None
) -> list[dict[str, Any]]:
    """Live cases with their latest step summaries. Sorted: red-flag pinned, priority, age."""
    rows = conn.execute(
        """
        SELECT r.id, r.status, r.status_changed_at, r.uploaded_at, r.locked_by, r.locked_at,
               r.original_filename,
               coalesce(corr.name, ex.output #>> '{extraction,patient_name,value}')
                 AS patient_name,
               coalesce(corr.modality, ex.output #>> '{extraction,modality,value}') AS modality,
               coalesce(corr.body_part, ex.output #>> '{extraction,body_part,value}')
                 AS body_part,
               coalesce(tr.output ->> 'final_priority', NULL) AS final_priority,
               tr.output #>> '{model,priority}' AS model_priority,
               (tr.output ->> 'raised_by_rules')::boolean AS raised_by_rules,
               tr.output #> '{rules,hits}' AS red_flags,
               pr.output #>> '{choice,protocol_id}' AS protocol_id,
               ct.output ->> 'result' AS contrast_result
        FROM requisitions r
        LEFT JOIN LATERAL (SELECT output FROM pipeline_steps s WHERE s.requisition_id = r.id
             AND s.step = 'extract' AND s.eval_run_id IS NULL ORDER BY id DESC LIMIT 1) ex
          ON true
        LEFT JOIN LATERAL (SELECT output FROM pipeline_steps s WHERE s.requisition_id = r.id
             AND s.step = 'triage' AND s.eval_run_id IS NULL ORDER BY id DESC LIMIT 1) tr
          ON true
        LEFT JOIN LATERAL (SELECT output FROM pipeline_steps s WHERE s.requisition_id = r.id
             AND s.step = 'protocol' AND s.eval_run_id IS NULL ORDER BY id DESC LIMIT 1) pr
          ON true
        LEFT JOIN LATERAL (SELECT output FROM pipeline_steps s WHERE s.requisition_id = r.id
             AND s.step = 'contrast' AND s.eval_run_id IS NULL ORDER BY id DESC LIMIT 1) ct
          ON true
        LEFT JOIN LATERAL (
          SELECT
            (SELECT corrected_value #>> '{}' FROM field_corrections c
              WHERE c.requisition_id = r.id AND c.field_path = 'patient_name'
              ORDER BY id DESC LIMIT 1) AS name,
            (SELECT corrected_value #>> '{}' FROM field_corrections c
              WHERE c.requisition_id = r.id AND c.field_path = 'modality'
              ORDER BY id DESC LIMIT 1) AS modality,
            (SELECT corrected_value #>> '{}' FROM field_corrections c
              WHERE c.requisition_id = r.id AND c.field_path = 'body_part'
              ORDER BY id DESC LIMIT 1) AS body_part
        ) corr ON true
        WHERE r.source = 'live'
          AND (%(statuses)s::text[] IS NULL OR r.status = ANY(%(statuses)s::text[]))
        """,
        {"statuses": statuses},
    ).fetchall()
    now = datetime.now(UTC)
    items = [queue_item(r, now) for r in rows]
    if priority:
        items = [i for i in items if i["priority"] == priority]
    return sorted(items, key=sort_key)


def case_detail(conn: Conn, case_id: UUID) -> dict[str, Any] | None:
    req = conn.execute(
        "SELECT * FROM requisitions WHERE id = %s AND source = 'live'", (case_id,)
    ).fetchone()
    if req is None:
        return None
    steps = latest_steps(conn, case_id)
    pages = conn.execute(
        "SELECT page_no, width_px, height_px, text_source FROM requisition_pages "
        "WHERE requisition_id = %s ORDER BY page_no",
        (case_id,),
    ).fetchall()
    fixes = corrections(conn, case_id)
    extraction = extraction_of(steps.get("extract"))
    fields = apply_corrections(
        from_extraction(extraction), [(c["field_path"], c["corrected_value"]) for c in fixes]
    )
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE requisition_id = %s ORDER BY id", (case_id,)
    ).fetchall()
    history = conn.execute(
        "SELECT at, actor, action, detail FROM audit_log WHERE entity = 'requisition' "
        "AND entity_id = %s ORDER BY id",
        (str(case_id),),
    ).fetchall()
    protocol_step = steps.get("protocol")
    candidate_ids = [c["id"] for c in (protocol_step or {}).get("output", {}).get("candidates", [])]
    candidates = conn.execute(
        "SELECT id, name, modality, body_part, contrast, indications FROM protocols "
        "WHERE id = ANY(%s)",
        (candidate_ids,),
    ).fetchall()
    now = datetime.now(UTC)
    lock_fresh = req["locked_at"] is not None and now - req["locked_at"] < LOCK_TTL
    return {
        "id": str(req["id"]),
        "status": req["status"],
        "uploaded_by": req["uploaded_by"],
        "uploaded_at": req["uploaded_at"].isoformat(),
        "status_changed_at": req["status_changed_at"].isoformat(),
        "original_filename": req["original_filename"],
        "content_type": req["content_type"],
        "locked_by": req["locked_by"] if lock_fresh else None,
        "pages": [
            {
                "page_no": p["page_no"],
                "width": p["width_px"],
                "height": p["height_px"],
                "text_source": p["text_source"],
            }
            for p in pages
        ],
        "fields": fields.model_dump(mode="json"),
        "extraction": steps["extract"]["output"] if "extract" in steps else None,
        "extraction_error": steps["extract"]["error"] if "extract" in steps else None,
        "corrections": [
            {
                "field": c["field_path"],
                "ai_value": c["ai_value"],
                "value": c["corrected_value"],
                "by": c["corrected_by"],
                "at": c["corrected_at"].isoformat(),
            }
            for c in fixes
        ],
        "triage": _step_summary(steps.get("triage")),
        "protocol": _step_summary(protocol_step),
        "protocol_candidates": {c["id"]: c for c in candidates},
        "contrast": _step_summary(steps.get("contrast")),
        "decisions": [
            {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in d.items()
                if k != "requisition_id"
            }
            for d in decisions
        ],
        "history": [
            {
                "at": h["at"].isoformat(),
                "actor": h["actor"],
                "action": h["action"],
                "detail": h["detail"],
            }
            for h in history
        ],
    }


def _step_summary(step: dict[str, Any] | None) -> dict[str, Any] | None:
    if step is None:
        return None
    return {
        "id": step["id"],
        "valid": step["valid"],
        "error": step["error"],
        "prompt_version": step["prompt_version"],
        "model": step["model"],
        "output": step["output"],
        "latency_ms": step["latency_ms"],
        "cost_usd": float(step["cost_usd"]) if step["cost_usd"] is not None else None,
        "created_at": step["created_at"].isoformat(),
    }
