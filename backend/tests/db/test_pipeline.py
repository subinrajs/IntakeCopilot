"""Pipeline orchestration with scripted model responses (no network, no cost).

Covers the design document's named scenarios that live in the pipeline:
1 buried red flag raises priority, 2 missing eGFR, 3 low eGFR, 4 injection text,
5 unreadable or invalid extraction goes to manual entry; plus resume, caching and
data minimisation.
"""

import asyncio
import uuid
from typing import Any

import pytest

from intake import db, lifecycle
from intake.llm.client import LLMError, LLMRequest
from intake.llm.fake_client import FakeLLMClient
from intake.llm.oracle_client import OracleClient
from intake.pipeline.orchestrator import PipelineConfig, PipelineDeps, file_sha256, process
from intake.settings import get_settings
from intake.storage import get_storage

pytestmark = pytest.mark.db

CONFIG = PipelineConfig(
    prompt_versions={"extract": "v1", "triage": "v1", "protocol": "v1"},
    models={
        "extract": "m-extract",
        "triage": "m-triage",
        "protocol": "m-protocol",
        "embedding": "m-embed",
    },
)


def fake(**responders: Any) -> FakeLLMClient:
    return FakeLLMClient({k: list(v) for k, v in responders.items()}, fallback=OracleClient())


def deps(llm: FakeLLMClient, use_cache: bool = False) -> PipelineDeps:
    config = PipelineConfig(CONFIG.prompt_versions, CONFIG.models, use_cache=use_cache)
    return PipelineDeps.create(llm, config)


def live_case(data: bytes, content_type: str = "application/pdf") -> uuid.UUID:
    case_id = uuid.uuid4()
    key = f"requisitions/{case_id}/original"
    get_storage().put(key, data)
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO requisitions (id, file_key, file_sha256, content_type, uploaded_by) "
            "VALUES (%s, %s, %s, %s, 'intake')",
            (case_id, key, file_sha256(data), content_type),
        )
    return case_id


def gold_pdf(case_key: str) -> bytes:
    path = get_settings().data_dir / "gold" / "v1" / "pdfs" / f"{case_key}.pdf"
    return path.read_bytes()


def run(case_id: uuid.UUID, llm: FakeLLMClient, **kwargs: Any) -> Any:
    return asyncio.run(process(case_id, deps(llm, kwargs.pop("use_cache", False)), **kwargs))


def step(case_id: uuid.UUID, name: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_steps WHERE requisition_id = %s AND step = %s "
            "ORDER BY id DESC LIMIT 1",
            (case_id, name),
        ).fetchone()
    assert row is not None, f"no {name} step"
    return row


def status(case_id: uuid.UUID) -> str:
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM requisitions WHERE id = %s", (case_id,)).fetchone()
    assert row is not None
    return str(row["status"])


def triage_says(priority: str) -> Any:
    return lambda _: {
        "priority": priority,
        "red_flags": [],
        "rationale": "scripted",
        "evidence": [],
        "confidence": "high",
    }


def test_happy_path_reaches_ready_for_review_with_four_steps(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-001"))
    result = run(case_id, fake())
    assert result.status == "ready_for_review"
    assert status(case_id) == "ready_for_review"
    assert set(result.steps) == {"extract", "triage", "protocol", "contrast"}
    with db.connection() as conn:
        moves = conn.execute(
            "SELECT detail->>'to' AS to FROM audit_log WHERE entity_id = %s "
            "AND action = 'case.status' ORDER BY id",
            (str(case_id),),
        ).fetchall()
    assert [m["to"] for m in moves] == ["processing", "ready_for_review"]


def test_scenario_1_buried_red_flag_raises_model_priority_to_p1(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-041"))
    run(case_id, fake(triage=[triage_says("P3")]))
    output = step(case_id, "triage")["output"]
    assert output["model"]["priority"] == "P3"
    assert output["rules"]["floor"] == "P1"
    assert output["final_priority"] == "P1"
    assert output["raised_by_rules"] is True


def test_rules_never_lower_the_model_priority(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-003"))  # no red flags
    run(case_id, fake(triage=[triage_says("P1")]))
    assert step(case_id, "triage")["output"]["final_priority"] == "P1"


def test_scenario_2_ct_contrast_without_egfr_needs_labs(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-044"))
    run(case_id, fake())
    output = step(case_id, "contrast")["output"]
    assert output["result"] == "needs_labs"
    assert [f["id"] for f in output["fired"]] == ["egfr_missing"]


def test_scenario_3_egfr_25_needs_review(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-045"))
    run(case_id, fake())
    output = step(case_id, "contrast")["output"]
    assert output["result"] == "needs_review"
    assert "egfr_low" in [f["id"] for f in output["fired"]]


def test_scenario_4_injection_text_never_reaches_triage_and_prompt_says_ignore(
    db_ready: None,
) -> None:
    llm = fake()
    case_id = live_case(gold_pdf("gold-v1-049"))
    run(case_id, llm)
    triage_requests = [r for r in llm.requests if r.purpose == "triage"]
    payload = "\n".join(getattr(p, "text", "") for p in triage_requests[0].parts)
    assert "ignore previous instructions" not in payload.lower()
    assert "ignore them" in triage_requests[0].instructions
    assert step(case_id, "triage")["output"]["final_priority"] == "P2"


def test_triage_and_protocol_requests_are_de_identified(db_ready: None) -> None:
    llm = fake()
    run(live_case(gold_pdf("gold-v1-001")), llm)
    gold = OracleClient().cases[0].fields
    for request in llm.requests:
        if request.purpose not in ("triage", "protocol"):
            continue
        payload = "\n".join(getattr(p, "text", "") for p in request.parts)
        for identifier in (
            gold.patient_name.split()[-1],
            gold.health_card_last4,
            gold.dob.isoformat(),
            gold.referrer_name.split()[-1],
        ):
            assert identifier not in payload, (request.purpose, identifier)


def _empty_extraction(_: LLMRequest[Any]) -> dict[str, Any]:
    from intake.schemas.extraction import LIST_FIELDS, SCALAR_FIELDS

    out: dict[str, Any] = {
        n: {"value": None, "confidence": "low", "evidence": None} for n in SCALAR_FIELDS
    }
    out.update({n: [] for n in LIST_FIELDS})
    return out


def test_scenario_5_invalid_twice_goes_to_manual_entry_not_the_queue(db_ready: None) -> None:
    llm = fake(extract=[_empty_extraction])
    case_id = live_case(gold_pdf("gold-v1-054"))
    result = run(case_id, llm)
    assert result.status == "manual_entry"
    extract = step(case_id, "extract")
    assert extract["valid"] is False and extract["attempts"] == 2
    assert "required" in extract["error"]
    assert [r.purpose for r in llm.requests] == ["extract", "extract"]
    # The retry tells the model what was wrong.
    retry_text = "\n".join(getattr(p, "text", "") for p in llm.requests[1].parts)
    assert "failed validation" in retry_text


def test_unreadable_file_goes_to_manual_entry(db_ready: None) -> None:
    case_id = live_case(b"%PDF-1.4 this is not really a pdf")
    result = run(case_id, fake())
    assert result.status == "manual_entry"
    assert step(case_id, "extract")["error"].startswith("unreadable")


def test_manual_entry_completion_runs_the_remaining_steps_on_entered_fields(
    db_ready: None,
) -> None:
    case_id = live_case(gold_pdf("gold-v1-054"))
    run(case_id, fake(extract=[_empty_extraction]))
    entered = {
        "patient_name": "Maria Andrews",
        "dob": "1984-05-31",
        "modality": "MRI",
        "body_part": "Cervical spine",
        "clinical_indication": "Progressive leg weakness, query cord compression",
    }
    with db.connection() as conn:
        for field, value in entered.items():
            conn.execute(
                "INSERT INTO field_corrections (requisition_id, field_path, corrected_value, "
                "corrected_by) VALUES (%s, %s, to_jsonb(%s::text), 'intake')",
                (case_id, field, value),
            )
        lifecycle.transition(conn, case_id, "processing", actor="intake", actor_id="intake")
    llm = fake()
    result = run(case_id, llm)
    assert result.status == "ready_for_review"
    assert "extract" not in [r.purpose for r in llm.requests]  # extraction is not retried
    assert step(case_id, "triage")["output"]["final_priority"] == "P1"


def test_failure_mid_pipeline_resumes_without_redoing_extraction(db_ready: None) -> None:
    case_id = live_case(gold_pdf("gold-v1-002"))
    failing = fake(triage=[lambda _: LLMError("rate limited")])
    with pytest.raises(LLMError):
        run(case_id, failing)
    assert status(case_id) == "processing"
    llm = fake()
    assert run(case_id, llm).status == "ready_for_review"
    assert "extract" not in [r.purpose for r in llm.requests]


def test_protocol_outside_candidates_is_rejected_and_all_candidates_shown(
    db_ready: None,
) -> None:
    invented = {
        "protocol_id": "MRI-INVENTED",
        "contrast": "none",
        "rationale": "x",
        "confidence": "high",
    }
    case_id = live_case(gold_pdf("gold-v1-003"))
    run(case_id, fake(protocol=[lambda _: invented]))
    output = step(case_id, "protocol")
    assert output["valid"] is False
    assert output["output"]["show_all_candidates"] is True
    assert len(output["output"]["candidates"]) == 5


def test_eval_runs_reuse_identical_steps_from_the_cache(db_ready: None) -> None:
    from intake.seed import gold_requisition_id

    case_id = gold_requisition_id("gold-v1-010")
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    with db.connection() as conn:
        for run_id in run_ids:
            conn.execute(
                "INSERT INTO eval_runs (id, gold_version, prompt_versions, models, started_by) "
                "VALUES (%s, 'v1', '{}', '{}', 'test')",
                (run_id,),
            )
    first = fake()
    run(case_id, first, eval_run_id=run_ids[0], use_cache=True)
    second = fake()
    run(case_id, second, eval_run_id=run_ids[1], use_cache=True)
    assert second.requests == []  # nothing recomputed
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT step, reused_from FROM pipeline_steps WHERE eval_run_id = %s",
            (run_ids[1],),
        ).fetchall()
    assert all(r["reused_from"] is not None for r in rows if r["step"] != "contrast")
    assert status(case_id) == "uploaded"  # eval runs never move a gold case's status
