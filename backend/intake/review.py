"""Human actions on a case: lock, correct fields, complete manual entry, decide, export.

Every check here is enforced by the API, not just the UI (design doc scenario 6), and every
action writes an audit row in the same transaction.
"""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from intake import audit, lifecycle
from intake.auth import User
from intake.cases import LOCK_TTL, effective_fields, latest_steps
from intake.hl7 import orm_message
from intake.pipeline import queue, steps
from intake.pipeline.orchestrator import save_step
from intake.rules.engine import RulesEngine
from intake.schemas.extraction import REQUIRED_FIELDS
from intake.schemas.fields import validate_value
from intake.settings import get_settings

Conn = Connection[dict[str, Any]]
CONTRAST_FIELDS = frozenset(
    {"modality", "contrast_requested", "egfr", "egfr_date", "allergies", "medications_of_note"}
)


class ReviewError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@lru_cache(maxsize=1)
def rules_engine() -> RulesEngine:
    return RulesEngine.from_file(get_settings().data_dir / "rules.yaml")


def _case(conn: Conn, case_id: UUID, lock: bool = True) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM requisitions WHERE id = %s AND source = 'live'"
        + (" FOR UPDATE" if lock else ""),
        (case_id,),
    ).fetchone()
    if row is None:
        raise ReviewError(404, "case not found")
    return row


def _lock_fresh(row: dict[str, Any]) -> bool:
    return row["locked_at"] is not None and datetime.now(UTC) - row["locked_at"] < LOCK_TTL


def _require_lock_holder(row: dict[str, Any], user: User) -> None:
    if row["status"] != "in_review":
        raise ReviewError(409, "open the case for review first")
    if row["locked_by"] != user.id:
        raise ReviewError(409, f"case is being reviewed by {row['locked_by']}")


# --------------------------------------------------------------------------- lock


def open_case(conn: Conn, case_id: UUID, user: User) -> None:
    row = _case(conn, case_id)
    if row["status"] == "in_review":
        if row["locked_by"] == user.id:
            conn.execute("UPDATE requisitions SET locked_at = now() WHERE id = %s", (case_id,))
            return
        if _lock_fresh(row):
            raise ReviewError(409, f"case is being reviewed by {row['locked_by']}")
        conn.execute(
            "UPDATE requisitions SET locked_by = %s, locked_at = now() WHERE id = %s",
            (user.id, case_id),
        )
        audit.record(
            conn,
            user.id,
            "case.lock_takeover",
            "requisition",
            str(case_id),
            {"previous": row["locked_by"]},
        )
        return
    try:
        lifecycle.transition(conn, case_id, "in_review", actor=user.role, actor_id=user.id)
    except lifecycle.InvalidTransition as error:
        raise ReviewError(409, str(error)) from error


def release_case(conn: Conn, case_id: UUID, user: User) -> None:
    row = _case(conn, case_id)
    if row["status"] != "in_review":
        return
    if row["locked_by"] != user.id and user.role != "admin":
        raise ReviewError(409, f"case is being reviewed by {row['locked_by']}")
    lifecycle.transition(
        conn,
        case_id,
        "ready_for_review",
        actor=user.role,
        actor_id=user.id,
        detail={"released": True},
    )


# --------------------------------------------------------------------------- fields


def correct_fields(conn: Conn, case_id: UUID, user: User, changes: dict[str, Any]) -> list[str]:
    row = _case(conn, case_id)
    if row["status"] not in ("manual_entry", "ready_for_review", "in_review"):
        raise ReviewError(409, f"fields cannot be edited while the case is {row['status']}")
    if row["status"] == "in_review":
        _require_lock_holder(row, user)
    if not changes:
        raise ReviewError(422, "no changes")
    current = effective_fields(conn, case_id).model_dump(mode="json")
    changed: list[str] = []
    for field, raw in changes.items():
        try:
            value = validate_value(field, raw)
        except ValueError as error:
            raise ReviewError(422, f"{field}: {error}") from error
        if (
            field == "health_card_last4"
            and value is not None
            and not (isinstance(value, str) and len(value) == 4 and value.isdigit())
        ):
            raise ReviewError(422, "health_card_last4 must be exactly 4 digits")
        if current.get(field) == value:
            continue
        conn.execute(
            """INSERT INTO field_corrections
                 (requisition_id, field_path, ai_value, corrected_value, corrected_by)
               VALUES (%s, %s, %s, %s, %s)""",
            (case_id, field, Jsonb(current.get(field)), Jsonb(value), user.id),
        )
        changed.append(field)
    if changed:
        audit.record(
            conn, user.id, "case.fields_corrected", "requisition", str(case_id), {"fields": changed}
        )
        if row["status"] != "manual_entry" and CONTRAST_FIELDS & set(changed):
            recheck_contrast(conn, case_id, row)
    return changed


def recheck_contrast(conn: Conn, case_id: UUID, row: dict[str, Any]) -> None:
    """Re-run the contrast rules on the corrected fields (deterministic, no model call)."""
    latest = latest_steps(conn, case_id)
    choice = (latest.get("protocol") or {}).get("output", {}).get("choice") or {}
    protocol_id = choice.get("protocol_id")
    contrast_row = (
        conn.execute("SELECT contrast FROM protocols WHERE id = %s", (protocol_id,)).fetchone()
        if protocol_id
        else None
    )
    outcome = steps.contrast(
        rules_engine(),
        fields=effective_fields(conn, case_id),
        protocol_id=protocol_id,
        protocol_contrast=contrast_row["contrast"] if contrast_row else None,
        as_of=row["uploaded_at"].astimezone(UTC).date(),
    )
    save_step(conn, case_id, None, outcome)


def complete_manual_entry(conn: Conn, case_id: UUID, user: User) -> None:
    row = _case(conn, case_id)
    if row["status"] != "manual_entry":
        raise ReviewError(409, "case is not waiting for manual entry")
    fields = effective_fields(conn, case_id)
    missing = [f for f in REQUIRED_FIELDS if getattr(fields, f) in (None, "")]
    if missing:
        raise ReviewError(422, f"required fields missing: {', '.join(missing)}")
    lifecycle.transition(
        conn,
        case_id,
        "processing",
        actor=user.role,
        actor_id=user.id,
        detail={"manual_entry": True},
    )
    queue.enqueue(
        conn,
        queue.PROCESS_REQUISITION,
        {"requisition_id": str(case_id)},
        dedupe_key=f"process:{case_id}:manual:{datetime.now(UTC).timestamp()}",
    )


# --------------------------------------------------------------------------- decisions


class ItemDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "override"]
    value: str | None = None
    reason: str | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    priority: ItemDecision | None = None
    protocol: ItemDecision | None = None
    acknowledged_flags: list[str] = []
    reason: str | None = None


def _has_reason(reason: str | None) -> bool:
    return bool(reason and reason.strip())


def _resolve(kind: str, decision: ItemDecision | None, ai_value: str | None) -> tuple[str, str]:
    """Returns (action, final value). Overrides need a reason and a different value."""
    if decision is None:
        raise ReviewError(422, f"{kind}: accept or override is required")
    if decision.action == "accept":
        if ai_value is None:
            raise ReviewError(422, f"{kind}: there is no AI suggestion to accept; override it")
        return "accept", ai_value
    if not decision.value:
        raise ReviewError(422, f"{kind}: an override needs a value")
    if not _has_reason(decision.reason):
        raise ReviewError(422, f"{kind}: an override needs a reason")
    return "override", decision.value


def decide(conn: Conn, case_id: UUID, user: User, request: DecisionRequest) -> str:
    row = _case(conn, case_id)
    _require_lock_holder(row, user)
    latest = latest_steps(conn, case_id)
    if request.action == "reject":
        if not _has_reason(request.reason):
            raise ReviewError(422, "a rejection needs a reason")
        _insert_decision(conn, case_id, "case", None, "rejected", "reject", request.reason, user)
        lifecycle.transition(
            conn,
            case_id,
            "rejected",
            actor=user.role,
            actor_id=user.id,
            detail={"reason_given": True},
        )
        return "rejected"

    ai_priority = (latest.get("triage") or {}).get("output", {}).get("final_priority")
    ai_protocol = ((latest.get("protocol") or {}).get("output", {}).get("choice") or {}).get(
        "protocol_id"
    )
    p_action, priority = _resolve("priority", request.priority, ai_priority)
    if priority not in ("P1", "P2", "P3", "P4"):
        raise ReviewError(422, "priority must be one of P1-P4")
    r_action, protocol_id = _resolve("protocol", request.protocol, ai_protocol)
    if conn.execute("SELECT 1 FROM protocols WHERE id = %s", (protocol_id,)).fetchone() is None:
        raise ReviewError(422, f"unknown protocol {protocol_id}")

    contrast = (latest.get("contrast") or {}).get("output", {})
    fired = [f["id"] for f in contrast.get("fired", [])]
    if r_action == "override" and protocol_id != ai_protocol:
        # Different protocol: the rules must be re-run on its contrast setting first.
        contrast_row = conn.execute(
            "SELECT contrast FROM protocols WHERE id = %s", (protocol_id,)
        ).fetchone()
        check = steps.contrast(
            rules_engine(),
            fields=effective_fields(conn, case_id),
            protocol_id=protocol_id,
            protocol_contrast=contrast_row["contrast"] if contrast_row else None,
            as_of=row["uploaded_at"].astimezone(UTC).date(),
        )
        save_step(conn, case_id, None, check)
        fired = [f["id"] for f in check.output["fired"]]
    missing = sorted(set(fired) - set(request.acknowledged_flags))
    if missing:
        raise ReviewError(422, f"acknowledge every contrast flag first: {', '.join(missing)}")

    _insert_decision(
        conn,
        case_id,
        "priority",
        ai_priority,
        priority,
        p_action,
        request.priority.reason if request.priority else None,
        user,
    )
    _insert_decision(
        conn,
        case_id,
        "protocol",
        ai_protocol,
        protocol_id,
        r_action,
        request.protocol.reason if request.protocol else None,
        user,
    )
    for flag in fired:
        _insert_decision(conn, case_id, "contrast", flag, flag, "acknowledge", None, user)
    _insert_decision(conn, case_id, "case", None, "approved", "accept", None, user)
    lifecycle.transition(
        conn,
        case_id,
        "approved",
        actor=user.role,
        actor_id=user.id,
        detail={
            "priority": priority,
            "protocol": protocol_id,
            "overrides": [
                k for k, a in (("priority", p_action), ("protocol", r_action)) if a == "override"
            ],
        },
    )
    return "approved"


def _insert_decision(
    conn: Conn,
    case_id: UUID,
    kind: str,
    ai_value: str | None,
    final_value: str | None,
    action: str,
    reason: str | None,
    user: User,
) -> None:
    conn.execute(
        """INSERT INTO decisions
             (requisition_id, kind, ai_value, final_value, action, reason, decided_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (case_id, kind, ai_value, final_value, action, reason.strip() if reason else None, user.id),
    )


# --------------------------------------------------------------------------- export


def final_decisions(conn: Conn, case_id: UUID) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT kind, final_value, decided_by FROM decisions WHERE requisition_id = %s ORDER BY id",
        (case_id,),
    ).fetchall()
    out: dict[str, Any] = {"contrast_flags": []}
    for r in rows:
        if r["kind"] == "contrast":
            out["contrast_flags"].append(r["final_value"])
        else:
            out[r["kind"]] = r["final_value"]
            out["decided_by"] = r["decided_by"]
    return out


def hl7_for(conn: Conn, case_id: UUID) -> str:
    row = _case(conn, case_id, lock=False)
    if row["status"] not in ("approved", "exported"):
        raise ReviewError(409, "only approved cases can be exported")
    decided = final_decisions(conn, case_id)
    protocol = conn.execute(
        "SELECT name FROM protocols WHERE id = %s", (decided["protocol"],)
    ).fetchone()
    return orm_message(
        case_id=str(case_id),
        fields=effective_fields(conn, case_id).model_dump(mode="json"),
        priority=decided["priority"],
        protocol_id=decided["protocol"],
        protocol_name=protocol["name"] if protocol else "",
        contrast_flags=decided["contrast_flags"],
        approved_by=decided.get("decided_by", ""),
        now=datetime.now(UTC),
    )


def export_case(conn: Conn, case_id: UUID, user: User) -> str:
    message = hl7_for(conn, case_id)
    row = _case(conn, case_id)
    if row["status"] == "approved":
        lifecycle.transition(conn, case_id, "exported", actor=user.role, actor_id=user.id)
    audit.record(conn, user.id, "case.export_hl7", "requisition", str(case_id))
    return message
