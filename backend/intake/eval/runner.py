"""Replay the gold set through the same pipeline code and score it.

Pass 1 uses the step cache (so `--prompts triage=v2` reuses the v1 extraction). With
repeat=2 a second pass bypasses the cache, and cases whose priority, protocol or fields differ
between the passes are reported as non-deterministic.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from intake import db
from intake.eval import metrics
from intake.llm.client import LLMClient
from intake.llm.factory import is_simulation
from intake.pipeline.orchestrator import PipelineConfig, PipelineDeps, process
from intake.schemas.extraction import RequisitionExtraction
from intake.schemas.fields import from_extraction
from intake.settings import get_settings

CONCURRENCY = 4


def gold_cases(
    gold_version: str, case_keys: list[str] | None, limit: int | None
) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM eval_cases WHERE gold_version = %s ORDER BY case_key", (gold_version,)
        ).fetchall()
    if case_keys:
        rows = [r for r in rows if r["case_key"] in case_keys]
    return rows[:limit] if limit else rows


def _steps_for(run_id: uuid.UUID) -> dict[str, dict[str, dict[str, Any]]]:
    """case id -> step -> latest row (with the original row's cost/latency for reused steps)."""
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (s.requisition_id, s.step) s.*,
                      coalesce(o.cost_usd, s.cost_usd) AS true_cost,
                      coalesce(o.latency_ms, s.latency_ms) AS true_latency
               FROM pipeline_steps s LEFT JOIN pipeline_steps o ON o.id = s.reused_from
               WHERE s.eval_run_id = %s
               ORDER BY s.requisition_id, s.step, s.id DESC""",
            (run_id,),
        ).fetchall()
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["requisition_id"]), {})[r["step"]] = r
    return out


def score_case(gold: dict[str, Any], steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    extract = steps.get("extract")
    extraction = None
    if extract and extract["valid"] and extract["output"].get("extraction"):
        extraction = RequisitionExtraction.model_validate(extract["output"]["extraction"])
    predicted = from_extraction(extraction).model_dump(mode="json") if extraction else None
    field_results = metrics.compare_fields(gold["gold_fields"], predicted)
    evidence = list((extract or {}).get("output", {}).get("evidence", {}).values())
    triage = (steps.get("triage") or {}).get("output", {})
    triage_evidence = triage.get("evidence_checks", [])
    protocol = (steps.get("protocol") or {}).get("output", {})
    candidates = [c["id"] for c in protocol.get("candidates", [])]
    contrast = (steps.get("contrast") or {}).get("output", {})
    fired = sorted(f["id"] for f in contrast.get("fired", []))
    latency = None
    if extract:
        parallel = (
            max((steps[s]["true_latency"] or 0) for s in ("triage", "protocol") if s in steps)
            if ("triage" in steps or "protocol" in steps)
            else 0
        )
        latency = (extract["true_latency"] or 0) + parallel
    return {
        "case_key": gold["case_key"],
        "requisition_id": str(gold["requisition_id"]),
        "difficulty": gold["difficulty"],
        "status": "done" if extraction is not None else "manual_entry",
        "gold_priority": gold["gold_priority"],
        "priority": triage.get("final_priority"),
        "model_priority": (triage.get("model") or {}).get("priority"),
        "raised_by_rules": bool(triage.get("raised_by_rules")),
        "rule_floor": (triage.get("rules") or {}).get("floor"),
        "gold_protocol_id": gold["gold_protocol_id"],
        "protocol_id": (protocol.get("choice") or {}).get("protocol_id"),
        "protocol_confidence": (protocol.get("choice") or {}).get("confidence"),
        "candidates": candidates,
        "recall_at_5": gold["gold_protocol_id"] in candidates,
        "gold_contrast_flags": sorted(gold["gold_contrast_flags"]),
        "contrast_flags": fired,
        "contrast_ok": fired == sorted(gold["gold_contrast_flags"]),
        "fields_correct": sum(field_results.values()),
        "fields_total": len(field_results),
        "field_errors": sorted(k for k, ok in field_results.items() if not ok),
        "evidence_found": sum(bool(e.get("found")) for e in evidence)
        + sum(bool(e.get("found")) for e in triage_evidence),
        "evidence_total": len(evidence) + len(triage_evidence),
        "cost_usd": round(sum(float(s["true_cost"] or 0) for s in steps.values()), 5),
        "latency_ms": latency,
        "fields": predicted,
    }


async def _replay(
    run_id: uuid.UUID, cases: list[dict[str, Any]], deps: PipelineDeps
) -> dict[str, str]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    errors: dict[str, str] = {}

    async def one(case: dict[str, Any]) -> None:
        async with semaphore:
            try:
                await process(case["requisition_id"], deps, eval_run_id=run_id)
            except Exception as error:
                errors[case["case_key"]] = f"{type(error).__name__}: {error}"[:300]

    await asyncio.gather(*(one(c) for c in cases))
    return errors


def _disagreements(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_key = {c["case_key"]: c for c in second}
    out = []
    for a in first:
        b = by_key.get(a["case_key"])
        if b is None:
            continue
        diffs = [k for k in ("priority", "model_priority", "protocol_id") if a[k] != b[k]]
        if a["fields"] and b["fields"]:
            diffs += [f"fields.{k}" for k in a["fields"] if a["fields"][k] != b["fields"].get(k)]
        if diffs:
            out.append({"case_key": a["case_key"], "differs": diffs})
    return out


def _create_run(
    run_id: uuid.UUID,
    gold_version: str,
    config: PipelineConfig,
    label: str | None,
    started_by: str,
    repeat_of: uuid.UUID | None = None,
) -> None:
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO eval_runs (id, gold_version, prompt_versions, models, label,
                 started_by, repeat_of)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                run_id,
                gold_version,
                Jsonb(config.prompt_versions),
                Jsonb(config.models),
                label,
                started_by,
                repeat_of,
            ),
        )


def previous_run(gold_version: str, before: uuid.UUID) -> dict[str, Any] | None:
    with db.connection() as conn:
        row: dict[str, Any] | None = conn.execute(
            """SELECT * FROM eval_runs WHERE gold_version = %s AND status = 'done'
                 AND repeat_of IS NULL AND id <> %s AND NOT (models::text LIKE '%%dev-oracle%%')
               ORDER BY started_at DESC LIMIT 1""",
            (gold_version, before),
        ).fetchone()
    return row


async def run_eval(
    llm: LLMClient,
    *,
    gold_version: str = "v1",
    prompt_overrides: dict[str, str] | None = None,
    repeat: int = 2,
    case_keys: list[str] | None = None,
    limit: int | None = None,
    label: str | None = None,
    started_by: str = "cli",
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    config = PipelineConfig.live(settings)
    config.prompt_versions.update(prompt_overrides or {})
    run_id = run_id or uuid.uuid4()
    cases = gold_cases(gold_version, case_keys, limit)
    if not cases:
        raise ValueError(f"no gold cases for {gold_version}; run `python -m intake.seed`")
    _create_run(run_id, gold_version, config, label, started_by)
    try:
        errors = await _replay(run_id, cases, PipelineDeps.create(llm, config))
        steps = _steps_for(run_id)
        per_case = [score_case(c, steps.get(str(c["requisition_id"]), {})) for c in cases]
        for case in per_case:
            if case["case_key"] in errors:
                case["error"] = errors[case["case_key"]]
        result_metrics = metrics.aggregate(per_case)
        if repeat > 1:
            repeat_id = uuid.uuid4()
            _create_run(
                repeat_id, gold_version, config, f"repeat of {run_id}", started_by, repeat_of=run_id
            )
            uncached = PipelineConfig(config.prompt_versions, config.models, use_cache=False)
            await _replay(repeat_id, cases, PipelineDeps.create(llm, uncached))
            repeat_steps = _steps_for(repeat_id)
            second = [score_case(c, repeat_steps.get(str(c["requisition_id"]), {})) for c in cases]
            _finish(repeat_id, metrics.aggregate(second), second)
            result_metrics["nondeterminism"] = {
                "repeat_run_id": str(repeat_id),
                "cases": _disagreements(per_case, second),
            }
        baseline = previous_run(gold_version, run_id)
        result_metrics["simulation"] = any(is_simulation(m) for m in config.models.values())
        result_metrics["baseline_run_id"] = str(baseline["id"]) if baseline else None
        result_metrics["release_gate"] = metrics.release_gate(
            result_metrics, baseline["metrics"] if baseline else None
        )
        _finish(run_id, result_metrics, per_case)
    except Exception as error:
        with db.connection() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'failed', error = %s, finished_at = now() "
                "WHERE id = %s",
                (f"{type(error).__name__}: {error}"[:2000], run_id),
            )
        raise
    return {"id": str(run_id), "metrics": result_metrics, "per_case": per_case}


def _finish(run_id: uuid.UUID, result: dict[str, Any], per_case: list[dict[str, Any]]) -> None:
    slim = [{k: v for k, v in c.items() if k != "fields"} for c in per_case]
    with db.connection() as conn:
        conn.execute(
            """UPDATE eval_runs SET status = 'done', finished_at = now(), metrics = %s,
                 per_case = %s WHERE id = %s""",
            (Jsonb(result), Jsonb(slim), run_id),
        )


def export_run(run_id: str, path: Path) -> None:
    """Snapshot a run for the demo seed (data/eval_runs/*.json)."""
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM eval_runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise LookupError(run_id)
    snapshot = {
        "id": str(row["id"]),
        "gold_version": row["gold_version"],
        "prompt_versions": row["prompt_versions"],
        "models": row["models"],
        "label": row["label"],
        "started_by": row["started_by"],
        "started_at": row["started_at"].isoformat(),
        "finished_at": (row["finished_at"] or datetime.now(UTC)).isoformat(),
        "metrics": row["metrics"],
        "per_case": row["per_case"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=1, default=str))
