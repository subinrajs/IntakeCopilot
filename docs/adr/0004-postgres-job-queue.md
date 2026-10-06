# ADR 0004: A small Postgres job queue (SKIP LOCKED)

- Status: accepted
- Date: 2026-10-05
- Resolves: design document open decision "Procrastinate or a SKIP LOCKED table"

## Context

Uploads must return a case id at once and be processed in the background with retries. The
app connects as a least-privilege role (no CREATE, no UPDATE/DELETE on the trails), so a queue
library that manages its own schema at runtime does not fit. ClinicVoice made the same call
(its ADR 0005).

## Decision

A `jobs` table plus a ~100-line worker (`intake/pipeline/queue.py`, `worker.py`): claim with
`UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)`, idempotent enqueue via `dedupe_key`,
retries with backoff (5 s, 30 s, 2 min), jobs stuck in `running` for 10 minutes are reclaimed.
A job that fails for good sends its case to manual entry rather than leaving it in processing.
The worker runs in a thread inside the API process (`RUN_WORKER=true`) or standalone.

## Consequences

No extra dependency or schema privileges; easy to explain. No scheduling features: the nightly
demo reset is a check inside the worker loop (a Render cron job cannot reach the web
service's disk).
