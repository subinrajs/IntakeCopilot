"""Insert-only audit trail. The app role cannot update or delete these rows (migration 0002)."""

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


def record(
    conn: Connection[Any],
    actor: str,
    action: str,
    entity: str,
    entity_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Never put document text or other PHI in `detail`; ids, field names and codes only."""
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, detail) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor, action, entity, entity_id, Jsonb(detail) if detail is not None else None),
    )
