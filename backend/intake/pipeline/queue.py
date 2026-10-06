"""A small Postgres job queue (same design as ClinicVoice ADR 0005; see ADR 0004 here).

Jobs are claimed with FOR UPDATE SKIP LOCKED, so several workers can run safely. Payloads
carry ids only, never PHI. Failed jobs retry with backoff; jobs stuck in `running` for 10
minutes (a crashed worker) are put back in the queue.
"""

from datetime import timedelta
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

BACKOFF = (timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2))
STUCK_AFTER = timedelta(minutes=10)
PROCESS_REQUISITION = "process_requisition"


def enqueue(
    conn: Connection[Any],
    job_type: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> int | None:
    """Returns the job id, or None if a job with the same dedupe key already exists."""
    row = conn.execute(
        """INSERT INTO jobs (type, payload, dedupe_key, max_attempts) VALUES (%s, %s, %s, %s)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
        (job_type, Jsonb(payload), dedupe_key, max_attempts),
    ).fetchone()
    return None if row is None else int(row["id"] if isinstance(row, dict) else row[0])


def claim(conn: Connection[Any]) -> dict[str, Any] | None:
    row: dict[str, Any] | None = conn.execute(
        """UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = now()
           WHERE id = (
             SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now()
             ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1
           )
           RETURNING *"""
    ).fetchone()
    return row


def complete(conn: Connection[Any], job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = %s",
        (job_id,),
    )


def fail(conn: Connection[Any], job: dict[str, Any], error: str) -> bool:
    """Schedules a retry with backoff; returns True when the job has now failed for good."""
    attempts = int(job["attempts"])
    if attempts >= int(job["max_attempts"]):
        conn.execute(
            "UPDATE jobs SET status = 'failed', last_error = %s, updated_at = now() WHERE id = %s",
            (error[:2000], job["id"]),
        )
        return True
    delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
    conn.execute(
        """UPDATE jobs SET status = 'queued', last_error = %s, run_after = now() + %s,
                  updated_at = now() WHERE id = %s""",
        (error[:2000], delay, job["id"]),
    )
    return False


def reclaim_stuck(conn: Connection[Any]) -> int:
    return conn.execute(
        "UPDATE jobs SET status = 'queued', updated_at = now() "
        "WHERE status = 'running' AND updated_at < now() - %s",
        (STUCK_AFTER,),
    ).rowcount


def depth(conn: Connection[Any]) -> int:
    row = conn.execute("SELECT count(*) AS n FROM jobs WHERE status IN ('queued', 'running')")
    result = row.fetchone()
    return int(result["n"] if isinstance(result, dict) else result[0]) if result else 0
