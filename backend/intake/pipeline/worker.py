"""Pipeline worker: claims jobs and runs requisitions through the pipeline.

Runs in its own thread with its own event loop (inside the API process when RUN_WORKER=true,
or standalone with `python -m intake.pipeline.worker`), so rendering, OCR and model calls never
block API requests.
"""

import asyncio
import logging
import threading
from typing import Any
from uuid import UUID

from intake import db, lifecycle
from intake.llm.client import LLMError
from intake.llm.factory import make_llm
from intake.pipeline import queue
from intake.pipeline.orchestrator import PipelineDeps, process
from intake.settings import get_settings

log = logging.getLogger("intake.worker")
POLL_SECONDS = 1.0


async def handle(job: dict[str, Any], deps: PipelineDeps) -> None:
    if job["type"] != queue.PROCESS_REQUISITION:
        raise ValueError(f"unknown job type {job['type']}")
    await process(UUID(job["payload"]["requisition_id"]), deps)


def _give_up(job: dict[str, Any], error: str) -> None:
    """A job that failed for good sends its case to manual entry rather than leaving it stuck."""
    case_id = job["payload"].get("requisition_id")
    if not case_id:
        return
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM requisitions WHERE id = %s", (case_id,)).fetchone()
        if row and row["status"] == "processing":
            lifecycle.transition(
                conn,
                UUID(case_id),
                "manual_entry",
                actor="worker",
                actor_id="worker",
                detail={"reason": f"pipeline failed: {error}"[:200]},
            )


async def _run_one(job: dict[str, Any], deps: PipelineDeps) -> None:
    try:
        await handle(job, deps)
    except Exception as error:
        kind = "model" if isinstance(error, LLMError) else type(error).__name__
        log.warning("job %s failed (%s)", job["id"], kind)
        message = f"{kind}: {error}"
        with db.connection() as conn:
            final = queue.fail(conn, job, message)
        if final:
            _give_up(job, kind)
        return
    with db.connection() as conn:
        queue.complete(conn, job["id"])


async def run(stop: threading.Event) -> None:
    settings = get_settings()
    deps = PipelineDeps.create(make_llm())
    running: set[asyncio.Task[None]] = set()
    with db.connection() as conn:
        queue.reclaim_stuck(conn)
    log.info("worker started (backend=%s)", settings.llm_backend)
    while not stop.is_set():
        claimed = False
        if len(running) < settings.worker_concurrency:
            with db.connection() as conn:
                job = queue.claim(conn)
            if job is not None:
                claimed = True
                task = asyncio.create_task(_run_one(job, deps))
                running.add(task)
                task.add_done_callback(running.discard)
        if not claimed:
            await asyncio.sleep(POLL_SECONDS)
    if running:
        await asyncio.gather(*running, return_exceptions=True)


class WorkerThread:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._main, name="pipeline-worker", daemon=True)

    def _main(self) -> None:
        try:
            asyncio.run(run(self.stop))
        except Exception:
            log.exception("worker crashed")

    def start(self) -> None:
        self.thread.start()

    def shutdown(self, timeout: float = 10.0) -> None:
        self.stop.set()
        self.thread.join(timeout)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    try:
        asyncio.run(run(stop))
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
