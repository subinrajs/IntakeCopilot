"""FastAPI application: REST API under /api, plus the pipeline worker thread when enabled."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from intake import db
from intake.api import routes_admin, routes_auth, routes_cases, routes_requisitions
from intake.lifecycle import InvalidTransition
from intake.review import ReviewError
from intake.settings import get_settings

log = logging.getLogger("intake.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = None
    if get_settings().run_worker:
        from intake.pipeline.worker import WorkerThread

        worker = WorkerThread()
        worker.start()
    yield
    if worker is not None:
        worker.shutdown()
    db.close_pool()


app = FastAPI(title="IntakeCopilot API", version="1.0.0", lifespan=lifespan)
for module in (routes_auth, routes_requisitions, routes_cases, routes_admin):
    app.include_router(module.router)


@app.exception_handler(ReviewError)
async def review_error(_: Request, error: ReviewError) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=error.status_code)


@app.exception_handler(InvalidTransition)
async def invalid_transition(_: Request, error: InvalidTransition) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.get("/healthz")
@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
@app.get("/api/readyz")
def readyz() -> dict[str, str]:
    """Readiness: the database is reachable as the app role."""
    try:
        with db.connection() as conn:
            conn.execute("SELECT 1")
    except Exception as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    return {"status": "ready"}
