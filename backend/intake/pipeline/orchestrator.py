"""Runs one requisition through extract -> (triage || protocol) -> contrast.

Each step is saved before the next starts, so a failure resumes from the last saved step.
Triage and protocol both depend only on the extraction, so they run concurrently. Live cases
move through the lifecycle (processing -> ready_for_review or manual_entry); eval runs record
steps against their eval_run_id and never touch a case's status.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from intake import audit, db, lifecycle
from intake.documents.pages import Span, UnreadableDocument, render_document
from intake.llm.client import LLMClient, Usage
from intake.pipeline import steps
from intake.pipeline.steps import PageData, StepOutcome
from intake.retrieval import Candidate, query_text, search
from intake.rules.engine import RulesEngine
from intake.rules.redflags import RedFlagMatcher
from intake.schemas.extraction import REQUIRED_FIELDS, RequisitionExtraction
from intake.schemas.fields import CaseFields, apply_corrections, from_extraction
from intake.settings import Settings, get_settings
from intake.storage import LocalStorage, get_storage

Conn = Connection[dict[str, Any]]


@dataclass
class PipelineConfig:
    prompt_versions: dict[str, str]
    models: dict[str, str]
    use_cache: bool = True

    @classmethod
    def live(cls, settings: Settings) -> "PipelineConfig":
        if settings.llm_backend == "dev-oracle":
            oracle = {k: "dev-oracle" for k in ("extract", "triage", "protocol", "embedding")}
            return cls(prompt_versions=dict(settings.prompt_versions), models=oracle)
        return cls(
            prompt_versions=dict(settings.prompt_versions),
            models={
                "extract": settings.openai_extract_model,
                "triage": settings.openai_triage_model,
                "protocol": settings.openai_protocol_model,
                "embedding": settings.openai_embedding_model,
            },
        )


@dataclass
class PipelineDeps:
    llm: LLMClient
    engine: RulesEngine
    matcher: RedFlagMatcher
    storage: LocalStorage
    config: PipelineConfig
    settings: Settings = field(default_factory=get_settings)

    @classmethod
    def create(cls, llm: LLMClient, config: PipelineConfig | None = None) -> "PipelineDeps":
        settings = get_settings()
        engine = RulesEngine.from_file(settings.data_dir / "rules.yaml")
        return cls(
            llm=llm,
            engine=engine,
            matcher=RedFlagMatcher(engine.spec),
            storage=get_storage(),
            config=config or PipelineConfig.live(settings),
            settings=settings,
        )


@dataclass
class PipelineResult:
    status: str  # final case status for live runs; "done" / "failed" for eval runs
    steps: dict[str, int]  # step -> pipeline_steps.id


# --------------------------------------------------------------------------- persistence


def save_step(conn: Conn, case_id: UUID, eval_run_id: UUID | None, outcome: StepOutcome) -> int:
    row = conn.execute(
        """INSERT INTO pipeline_steps
             (requisition_id, eval_run_id, step, prompt_version, model, input_hash, output, valid,
              error, attempts, latency_ms, input_tokens, cached_input_tokens, output_tokens,
              cost_usd, reused_from)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            case_id,
            eval_run_id,
            outcome.step,
            outcome.prompt_version,
            outcome.model,
            outcome.input_hash,
            Jsonb(outcome.output),
            outcome.valid,
            outcome.error,
            outcome.attempts,
            outcome.latency_ms,
            outcome.usage.input_tokens,
            outcome.usage.cached_input_tokens,
            outcome.usage.output_tokens,
            round(outcome.cost_usd, 5),
            outcome.reused_from,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def latest_step(
    conn: Conn, case_id: UUID, step: str, eval_run_id: UUID | None
) -> dict[str, Any] | None:
    return conn.execute(
        """SELECT * FROM pipeline_steps
           WHERE requisition_id = %s AND step = %s AND eval_run_id IS NOT DISTINCT FROM %s
           ORDER BY id DESC LIMIT 1""",
        (case_id, step, eval_run_id),
    ).fetchone()


def cached_step(conn: Conn, outcome_key: tuple[str, str, str, str | None]) -> dict[str, Any] | None:
    step, key, prompt_version, model = outcome_key
    return conn.execute(
        """SELECT * FROM pipeline_steps
           WHERE step = %s AND input_hash = %s AND prompt_version = %s
             AND model IS NOT DISTINCT FROM %s AND valid
           ORDER BY id DESC LIMIT 1""",
        (step, key, prompt_version, model),
    ).fetchone()


def reuse(row: dict[str, Any]) -> StepOutcome:
    """A copy of an earlier identical step: same output, nothing spent."""
    return StepOutcome(
        step=row["step"],
        prompt_version=row["prompt_version"],
        model=row["model"],
        input_hash=row["input_hash"],
        output=row["output"],
        valid=row["valid"],
        error=row["error"],
        attempts=0,
        latency_ms=row["latency_ms"] or 0,
        reused_from=row["reused_from"] or row["id"],
    )


def corrections(conn: Conn, case_id: UUID) -> list[tuple[str, Any]]:
    rows = conn.execute(
        "SELECT field_path, corrected_value FROM field_corrections "
        "WHERE requisition_id = %s ORDER BY id",
        (case_id,),
    ).fetchall()
    return [(r["field_path"], r["corrected_value"]) for r in rows]


def load_pages(conn: Conn, storage: LocalStorage, req: dict[str, Any]) -> list[PageData]:
    """Page images and text index; rendered (and OCR'd) once per requisition, then reused."""
    rows = conn.execute(
        "SELECT page_no, image_key, words FROM requisition_pages "
        "WHERE requisition_id = %s ORDER BY page_no",
        (req["id"],),
    ).fetchall()
    if rows:
        return [
            PageData(
                r["page_no"],
                storage.get(r["image_key"]),
                [Span(w["t"], tuple(w["b"])) for w in r["words"]],
            )
            for r in rows
        ]
    rendered = render_document(storage.get(req["file_key"]), req["content_type"])
    pages: list[PageData] = []
    for page in rendered:
        key = f"requisitions/{req['id']}/page-{page.page_no}.png"
        storage.put(key, page.png)
        conn.execute(
            """INSERT INTO requisition_pages
                 (requisition_id, page_no, image_key, width_px, height_px, text_source, text, words)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (requisition_id, page_no) DO NOTHING""",
            (
                req["id"],
                page.page_no,
                key,
                page.width,
                page.height,
                page.text.source,
                page.text.text,
                Jsonb([s.to_json() for s in page.text.spans]),
            ),
        )
        pages.append(PageData(page.page_no, page.png, page.text.spans))
    conn.execute("UPDATE requisitions SET pages = %s WHERE id = %s", (len(pages), req["id"]))
    return pages


# --------------------------------------------------------------------------- orchestration


def as_of_for(conn: Conn, req: dict[str, Any]) -> date:
    """Gold cases use their frozen as_of date; live cases use the upload date (clinic time)."""
    if req["source"] == "gold":
        row = conn.execute(
            "SELECT as_of FROM eval_cases WHERE requisition_id = %s", (req["id"],)
        ).fetchone()
        if row is not None:
            return row["as_of"]  # type: ignore[no-any-return]
    uploaded: datetime = req["uploaded_at"]
    return uploaded.astimezone(UTC).date()


async def _run_or_reuse(
    deps: PipelineDeps, conn: Conn, compute: Any, key: tuple[str, str, str, str | None]
) -> StepOutcome:
    if deps.config.use_cache:
        cached = cached_step(conn, key)
        if cached is not None:
            return reuse(cached)
    outcome: StepOutcome = await compute()
    return outcome


async def process(
    case_id: UUID,
    deps: PipelineDeps,
    *,
    eval_run_id: UUID | None = None,
) -> PipelineResult:
    cfg = deps.config
    saved: dict[str, int] = {}
    with db.connection() as conn:
        req = conn.execute("SELECT * FROM requisitions WHERE id = %s", (case_id,)).fetchone()
        if req is None:
            raise LookupError(f"requisition {case_id} not found")
        live = eval_run_id is None and req["source"] == "live"
        if live and req["status"] == "uploaded":
            lifecycle.transition(conn, case_id, "processing", actor="worker", actor_id="worker")
        conn.commit()
        try:
            pages = load_pages(conn, deps.storage, req)
            conn.commit()
        except UnreadableDocument as error:
            outcome = StepOutcome(
                "extract",
                cfg.prompt_versions["extract"],
                None,
                steps.input_hash({"file": req["file_sha256"]}),
                {"extraction": None, "evidence": {}},
                valid=False,
                error=f"unreadable: {error}",
                attempts=0,
            )
            saved["extract"] = save_step(conn, case_id, eval_run_id, outcome)
            return _finish_manual(conn, case_id, live, saved, "unreadable file")
        as_of = as_of_for(conn, req)

        # Step 1: extraction (resume: any saved extraction for this run is final).
        extract_row = latest_step(conn, case_id, "extract", eval_run_id)
        if extract_row is None:
            model = cfg.models["extract"]
            version = cfg.prompt_versions["extract"]
            key = (
                "extract",
                steps.input_hash({"file": req["file_sha256"], "v": steps.EXTRACT_INPUT_VERSION}),
                version,
                model,
            )
            outcome = await _run_or_reuse(
                deps,
                conn,
                lambda: steps.extract(
                    deps.llm,
                    model=model,
                    prompt_version=version,
                    pages=pages,
                    file_sha256=req["file_sha256"],
                    today=as_of,
                ),
                key,
            )
            saved["extract"] = save_step(conn, case_id, eval_run_id, outcome)
            conn.commit()
            extract_row = latest_step(conn, case_id, "extract", eval_run_id)
        assert extract_row is not None

        extraction = (
            RequisitionExtraction.model_validate(extract_row["output"]["extraction"])
            if extract_row["valid"]
            else None
        )
        fields = from_extraction(extraction)
        if live:
            fields = apply_corrections(fields, corrections(conn, case_id))
        if extraction is None and not _required_present(fields):
            return _finish_manual(conn, case_id, live, saved, extract_row["error"] or "invalid")

        # Steps 2 and 3, concurrently.
        triage_row = latest_step(conn, case_id, "triage", eval_run_id)
        protocol_row = latest_step(conn, case_id, "protocol", eval_run_id)
        fresh_triage = triage_row is None or _stale(triage_row, steps.triage_input(fields))
        fresh_protocol = protocol_row is None or _stale(protocol_row, steps.protocol_input(fields))
        tasks: dict[str, Any] = {}
        if fresh_triage:
            tasks["triage"] = _triage(deps, conn, fields, pages)
        if fresh_protocol:
            tasks["protocol"] = _protocol(deps, conn, fields)
        results = await asyncio.gather(*tasks.values())
        for name, outcome in zip(tasks, results, strict=True):
            saved[name] = save_step(conn, case_id, eval_run_id, outcome)
        conn.commit()
        protocol_row = latest_step(conn, case_id, "protocol", eval_run_id)
        choice = (protocol_row or {}).get("output", {}).get("choice") or {}
        protocol_id = choice.get("protocol_id")
        protocol_contrast = _protocol_contrast(conn, protocol_id)

        # Step 4: contrast rules (deterministic, cheap: always recomputed on current fields).
        outcome = steps.contrast(
            deps.engine,
            fields=fields,
            protocol_id=protocol_id,
            protocol_contrast=protocol_contrast,
            as_of=as_of,
        )
        saved["contrast"] = save_step(conn, case_id, eval_run_id, outcome)

        if live:
            lifecycle.transition(
                conn,
                case_id,
                "ready_for_review",
                actor="worker",
                actor_id="worker",
                detail={"steps": saved},
            )
        conn.commit()
    return PipelineResult("ready_for_review" if live else "done", saved)


def _required_present(fields: CaseFields) -> bool:
    return all(getattr(fields, name) not in (None, "") for name in REQUIRED_FIELDS)


def _stale(row: dict[str, Any], current_input: dict[str, Any]) -> bool:
    """A saved triage/protocol step is reused unless the fields it ran on have changed."""
    return bool(row["output"].get("fields_hash") != steps.fields_hash(current_input))


def _finish_manual(
    conn: Conn, case_id: UUID, live: bool, saved: dict[str, int], reason: str
) -> PipelineResult:
    if live:
        lifecycle.transition(
            conn,
            case_id,
            "manual_entry",
            actor="worker",
            actor_id="worker",
            detail={"reason": reason[:200]},
        )
    conn.commit()
    return PipelineResult("manual_entry" if live else "failed", saved)


def _protocol_contrast(conn: Conn, protocol_id: str | None) -> str | None:
    if protocol_id is None:
        return None
    row = conn.execute("SELECT contrast FROM protocols WHERE id = %s", (protocol_id,)).fetchone()
    return row["contrast"] if row else None


async def _triage(
    deps: PipelineDeps, conn: Conn, fields: CaseFields, pages: list[PageData]
) -> StepOutcome:
    model, version = deps.config.models["triage"], deps.config.prompt_versions["triage"]
    if deps.config.use_cache:
        key = steps.triage_key(fields, pages, deps.engine.version)
        cached = cached_step(conn, ("triage", key, version, model))
        if cached is not None:
            return reuse(cached)
    return await steps.triage(
        deps.llm,
        model=model,
        prompt_version=version,
        fields=fields,
        pages=pages,
        matcher=deps.matcher,
        rules_version=deps.engine.version,
    )


async def _protocol(deps: PipelineDeps, conn: Conn, fields: CaseFields) -> StepOutcome:
    model, version = deps.config.models["protocol"], deps.config.prompt_versions["protocol"]
    text = query_text(fields)
    candidates: list[Candidate] = []
    usage, cost = Usage(), 0.0
    if text:
        vectors, usage = await deps.llm.embed([text])
        cost = usage.cost_usd(deps.config.models["embedding"])
        candidates = search(conn, vectors[0], text, fields.modality)
    if deps.config.use_cache:
        cached = cached_step(
            conn, ("protocol", steps.protocol_key(fields, candidates), version, model)
        )
        if cached is not None:
            return reuse(cached)
    return await steps.choose_protocol(
        deps.llm,
        model=model,
        prompt_version=version,
        fields=fields,
        candidates=candidates,
        retrieval_usage=usage,
        retrieval_cost=cost,
    )


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_view(conn: Conn, actor_id: str, case_id: UUID) -> None:
    audit.record(conn, actor_id, "case.view", "requisition", str(case_id))
