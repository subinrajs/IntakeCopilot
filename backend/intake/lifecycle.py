"""Case lifecycle: the one place status transitions are defined, checked and audited.

The worker can move a case only as far as ready_for_review. Every later transition needs a
signed-in human with the right role.
"""

from typing import Any, Literal
from uuid import UUID

from psycopg import Connection

from intake import audit

Status = Literal[
    "uploaded",
    "processing",
    "manual_entry",
    "ready_for_review",
    "in_review",
    "approved",
    "rejected",
    "exported",
]
Actor = Literal["worker", "intake", "radiologist", "admin"]

# (from, to) -> roles allowed to make that transition
TRANSITIONS: dict[tuple[Status, Status], frozenset[Actor]] = {
    ("uploaded", "processing"): frozenset({"worker"}),
    ("processing", "ready_for_review"): frozenset({"worker"}),
    ("processing", "manual_entry"): frozenset({"worker"}),
    # Staff complete the fields by hand; triage, protocol and rules then run on them.
    ("manual_entry", "processing"): frozenset({"intake", "radiologist", "admin"}),
    ("ready_for_review", "in_review"): frozenset({"radiologist"}),
    ("in_review", "ready_for_review"): frozenset({"radiologist", "admin"}),  # release the lock
    ("in_review", "approved"): frozenset({"radiologist"}),
    ("in_review", "rejected"): frozenset({"radiologist"}),
    ("approved", "exported"): frozenset({"radiologist", "admin"}),
}


class InvalidTransition(Exception):
    pass


def check(current: Status, target: Status, actor: Actor) -> None:
    allowed = TRANSITIONS.get((current, target))
    if allowed is None:
        raise InvalidTransition(f"cannot move a case from {current} to {target}")
    if actor not in allowed:
        raise InvalidTransition(f"{actor} cannot move a case from {current} to {target}")


def transition(
    conn: Connection[Any],
    case_id: UUID,
    target: Status,
    *,
    actor: Actor,
    actor_id: str,
    detail: dict[str, Any] | None = None,
) -> Status:
    """Move a live case to `target` (row-locked), writing an audit event. Returns the old status."""
    row = conn.execute(
        "SELECT status, source FROM requisitions WHERE id = %s FOR UPDATE", (case_id,)
    ).fetchone()
    if row is None:
        raise InvalidTransition("case not found")
    current: Status = row["status"] if isinstance(row, dict) else row[0]
    check(current, target, actor)
    lock_sql = ""
    if target == "in_review":
        lock_sql = ", locked_by = %(actor_id)s, locked_at = now()"
    elif current == "in_review":
        lock_sql = ", locked_by = NULL, locked_at = NULL"
    conn.execute(
        f"UPDATE requisitions SET status = %(target)s, status_changed_at = now(){lock_sql} "
        "WHERE id = %(id)s",
        {"target": target, "id": case_id, "actor_id": actor_id},
    )
    audit.record(
        conn,
        actor_id,
        "case.status",
        "requisition",
        str(case_id),
        {"from": current, "to": target, **(detail or {})},
    )
    return current
