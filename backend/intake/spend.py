"""Model spend tracking for the public demo's daily cap (from pipeline_steps)."""

from typing import Any

from psycopg import Connection


def spent_today_usd(conn: Connection[Any]) -> float:
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) AS usd FROM pipeline_steps "
        "WHERE created_at > date_trunc('day', now())"
    ).fetchone()
    if row is None:
        return 0.0
    return float(row["usd"] if isinstance(row, dict) else row[0])
