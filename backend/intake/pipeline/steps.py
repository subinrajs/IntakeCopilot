"""The four pipeline steps. Each returns a StepOutcome; the orchestrator persists it.

Steps do not touch the database (except retrieval, which the orchestrator passes in as
candidates), so they are easy to test with a fake model client.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from intake.documents.evidence import PageIndex, locate_quote
from intake.documents.pages import Span
from intake.llm.client import (
    ImagePart,
    LLMClient,
    LLMOutputInvalid,
    LLMRequest,
    Part,
    TextPart,
    Usage,
)
from intake.prompts import load_prompt
from intake.retrieval import Candidate
from intake.rules.engine import ContrastInput, RulesEngine
from intake.rules.redflags import RedFlagMatcher
from intake.schemas.extraction import (
    LIST_FIELDS,
    REQUIRED_FIELDS,
    SCALAR_FIELDS,
    Evidence,
    RequisitionExtraction,
)
from intake.schemas.fields import CaseFields
from intake.schemas.protocol import ProtocolChoice, constrained_protocol_choice
from intake.schemas.triage import Priority, TriageSuggestion, more_urgent

# Bump when rendering or OCR changes, so cached extractions are not reused across them.
EXTRACT_INPUT_VERSION = "render150-ocr200-v1"
MAX_ATTEMPTS = 2  # design doc: invalid output retries once with the error, then manual entry


def input_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class PageData:
    page_no: int
    png: bytes
    spans: list[Span]

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.spans)


@dataclass
class StepOutcome:
    step: str
    prompt_version: str
    model: str | None
    input_hash: str
    output: dict[str, Any]
    valid: bool
    error: str | None = None
    attempts: int = 1
    latency_ms: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    reused_from: int | None = None


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.cached_input_tokens + b.cached_input_tokens,
        a.output_tokens + b.output_tokens,
    )


def document_text(pages: list[PageData]) -> str:
    return "\n\n".join(f'<page number="{p.page_no}">\n{p.text}\n</page>' for p in pages)


# --------------------------------------------------------------------------- step 1: extract


def validate_extraction(
    extraction: RequisitionExtraction, page_count: int, today: date
) -> list[str]:
    """Checks beyond the JSON schema. Any error triggers the retry, then manual entry."""
    errors: list[str] = []
    for name in REQUIRED_FIELDS:
        if getattr(extraction, name).value in (None, ""):
            errors.append(f"{name} is required but was empty")
    last4 = extraction.health_card_last4.value
    if last4 is not None and not (len(last4) == 4 and last4.isdigit()):
        errors.append("health_card_last4 must be exactly 4 digits")
    dob = extraction.dob.value
    if dob is not None and not (date(1900, 1, 1) <= dob <= today):
        errors.append("dob must be a past date after 1900")
    egfr = extraction.egfr.value
    if egfr is not None and not (0 < egfr < 250):
        errors.append("egfr must be between 0 and 250")
    for path, evidence in _evidence_items(extraction):
        if not 1 <= evidence.page <= page_count:
            errors.append(f"{path}: evidence page {evidence.page} does not exist")
    return errors


def _evidence_items(extraction: RequisitionExtraction) -> list[tuple[str, Evidence]]:
    items: list[tuple[str, Evidence]] = []
    for name in SCALAR_FIELDS:
        evidence = getattr(extraction, name).evidence
        if evidence is not None:
            items.append((name, evidence))
    for name in LIST_FIELDS:
        for index, item in enumerate(getattr(extraction, name)):
            if item.evidence is not None:
                items.append((f"{name}.{index}", item.evidence))
    return items


def check_evidence(
    extraction: RequisitionExtraction, pages: list[PageData]
) -> tuple[RequisitionExtraction, dict[str, dict[str, Any]]]:
    """Locate every quote in the page text. Unfound quotes lower that field to low confidence."""
    index = [PageIndex(p.page_no, p.spans) for p in pages]
    checks: dict[str, dict[str, Any]] = {}
    data = extraction.model_dump()
    for path, evidence in _evidence_items(extraction):
        match = locate_quote(evidence.quote, index, page_hint=evidence.page)
        checks[path] = {
            "found": match.found,
            "score": round(match.score, 1),
            "page": match.page_no,
            "boxes": [list(b) for b in match.boxes],
        }
        if not match.found:
            name, _, position = path.partition(".")
            target = data[name][int(position)] if position else data[name]
            target["confidence"] = "low"
    return RequisitionExtraction.model_validate(data), checks


async def extract(
    llm: LLMClient,
    *,
    model: str,
    prompt_version: str,
    pages: list[PageData],
    file_sha256: str,
    today: date,
) -> StepOutcome:
    instructions = load_prompt("extract", prompt_version)
    parts: list[Part] = [ImagePart(p.png) for p in pages]
    parts.append(TextPart(f"<document_text>\n{document_text(pages)}\n</document_text>"))
    usage, latency, error = Usage(), 0, None
    extraction: RequisitionExtraction | None = None
    attempts = 0
    for attempts in range(1, MAX_ATTEMPTS + 1):  # noqa: B007 (count kept after the loop)
        request_parts = (
            parts
            if error is None
            else [
                *parts,
                TextPart(
                    f"Your previous answer failed validation: {error}. Return a corrected "
                    "record. Leave a field null rather than guessing."
                ),
            ]
        )
        try:
            result = await llm.complete(
                LLMRequest("extract", model, instructions, request_parts, RequisitionExtraction)
            )
        except LLMOutputInvalid as invalid:
            error = f"schema: {invalid}"[:2000]
            continue
        usage, latency = _add(usage, result.usage), latency + result.latency_ms
        problems = validate_extraction(result.parsed, len(pages), today)
        if not problems:
            extraction, error = result.parsed, None
            break
        error = "; ".join(problems)
        extraction = result.parsed  # kept for the manual-entry screen, marked invalid
    output: dict[str, Any] = {"extraction": None, "evidence": {}}
    if extraction is not None:
        checked, evidence = check_evidence(extraction, pages)
        output = {"extraction": checked.model_dump(mode="json"), "evidence": evidence}
    return StepOutcome(
        step="extract",
        prompt_version=prompt_version,
        model=model,
        input_hash=input_hash({"file": file_sha256, "v": EXTRACT_INPUT_VERSION}),
        output=output,
        valid=error is None and extraction is not None,
        error=error,
        attempts=attempts,
        latency_ms=latency,
        usage=usage,
        cost_usd=usage.cost_usd(model),
    )


# --------------------------------------------------------------------------- step 2: triage


def triage_input(fields: CaseFields) -> dict[str, Any]:
    """Minimum necessary: clinical content only, no name, birth date, card or referrer."""
    return {
        "modality": fields.modality,
        "body_part": fields.body_part,
        "laterality": fields.laterality,
        "contrast_requested": fields.contrast_requested,
        "clinical_indication": fields.clinical_indication,
        "relevant_history": fields.relevant_history,
        "allergies": fields.allergies,
        "medications": fields.medications_of_note,
        "physician_marked_urgent": fields.physician_marked_urgent,
    }


def triage_text(fields: CaseFields, pages: list[PageData]) -> str:
    """Everything the red-flag rules scan: the full document plus the effective fields."""
    case = triage_input(fields)
    return "\n".join([document_text(pages), *(str(v) for v in case.values() if v)])


def triage_key(fields: CaseFields, pages: list[PageData], rules_version: int) -> str:
    return input_hash(
        {"case": triage_input(fields), "text": triage_text(fields, pages), "rules": rules_version}
    )


def fields_hash(payload: dict[str, Any]) -> str:
    return input_hash(payload)[:16]


async def triage(
    llm: LLMClient,
    *,
    model: str,
    prompt_version: str,
    fields: CaseFields,
    pages: list[PageData],
    matcher: RedFlagMatcher,
    rules_version: int,
) -> StepOutcome:
    """Model triage and red-flag rules on the full document text; the more urgent one wins."""
    case = triage_input(fields)
    hits = matcher.find(triage_text(fields, pages))
    floor = matcher.floor(hits)
    instructions = load_prompt("triage", prompt_version)
    case_part = TextPart(f"<case>\n{json.dumps(case, indent=1, default=str)}\n</case>")
    usage, latency, error, attempts = Usage(), 0, None, 0
    suggestion: TriageSuggestion | None = None
    for attempts in range(1, MAX_ATTEMPTS + 1):  # noqa: B007 (count kept after the loop)
        parts: list[Part] = [case_part]
        if error:
            parts.append(TextPart(f"Your previous answer failed validation: {error}."))
        try:
            result = await llm.complete(
                LLMRequest("triage", model, instructions, parts, TriageSuggestion)
            )
        except LLMOutputInvalid as invalid:
            error = str(invalid)[:2000]
            continue
        usage, latency = _add(usage, result.usage), latency + result.latency_ms
        suggestion, error = result.parsed, None
        break

    model_priority = suggestion.priority if suggestion else None
    final: Priority | None
    if model_priority and floor:
        final = more_urgent(model_priority, floor)
    else:
        final = model_priority or floor  # None: no suggestion, the radiologist sets it
    index = [PageIndex(p.page_no, p.spans) for p in pages]
    evidence_checks = [
        {"quote": e.quote, "found": locate_quote(e.quote, index, e.page).found}
        for e in (suggestion.evidence if suggestion else [])
    ]
    output = {
        "model": suggestion.model_dump(mode="json") if suggestion else None,
        "rules": {
            "rules_version": rules_version,
            "floor": floor,
            "hits": [{"level": h.level, "phrase": h.phrase, "context": h.context} for h in hits],
        },
        "final_priority": final,
        "raised_by_rules": bool(model_priority and final != model_priority),
        "evidence_checks": evidence_checks,
        "fields_hash": fields_hash(case),
    }
    return StepOutcome(
        step="triage",
        prompt_version=prompt_version,
        model=model,
        input_hash=triage_key(fields, pages, rules_version),
        output=output,
        valid=suggestion is not None,
        error=error,
        attempts=attempts,
        latency_ms=latency,
        usage=usage,
        cost_usd=usage.cost_usd(model),
    )


# --------------------------------------------------------------------------- step 3: protocol


def protocol_input(fields: CaseFields) -> dict[str, Any]:
    return {
        "modality": fields.modality,
        "body_part": fields.body_part,
        "laterality": fields.laterality,
        "contrast_requested": fields.contrast_requested,
        "clinical_indication": fields.clinical_indication,
        "relevant_history": fields.relevant_history,
    }


def protocol_key(fields: CaseFields, candidates: list[Candidate]) -> str:
    return input_hash({"case": protocol_input(fields), "candidates": [c.id for c in candidates]})


async def choose_protocol(
    llm: LLMClient,
    *,
    model: str,
    prompt_version: str,
    fields: CaseFields,
    candidates: list[Candidate],
    retrieval_usage: Usage,
    retrieval_cost: float,
) -> StepOutcome:
    request_data = protocol_input(fields)
    base_output: dict[str, Any] = {
        "candidates": [c.to_json() for c in candidates],
        "choice": None,
        "fields_hash": fields_hash(request_data),
    }
    key = protocol_key(fields, candidates)
    if not candidates:
        return StepOutcome(
            "protocol",
            prompt_version,
            model,
            key,
            base_output,
            valid=False,
            error="no candidate protocols retrieved",
            attempts=0,
        )
    candidate_text = "\n\n".join(
        f"id: {c.id}\nname: {c.name}\ncontrast: {c.contrast}\nindications: {c.indications}"
        for c in candidates
    )
    parts: list[Part] = [
        TextPart(f"<request>\n{json.dumps(request_data, indent=1)}\n</request>"),
        TextPart(f"<candidates>\n{candidate_text}\n</candidates>"),
    ]
    output_model = constrained_protocol_choice([c.id for c in candidates])
    usage, latency, error, attempts = retrieval_usage, 0, None, 0
    choice: ProtocolChoice | None = None
    for attempts in range(1, MAX_ATTEMPTS + 1):  # noqa: B007 (count kept after the loop)
        try:
            result = await llm.complete(
                LLMRequest(
                    "protocol",
                    model,
                    load_prompt("protocol", prompt_version),
                    parts if not error else [*parts, TextPart(f"Fix: {error}")],
                    output_model,
                )
            )
        except (LLMOutputInvalid, ValidationError) as invalid:
            error = str(invalid)[:2000]
            continue
        usage, latency = _add(usage, result.usage), latency + result.latency_ms
        choice, error = result.parsed, None
        break
    output = {
        **base_output,
        "choice": choice.model_dump(mode="json") if choice else None,
        "show_all_candidates": choice is None or choice.confidence == "low",
    }
    return StepOutcome(
        step="protocol",
        prompt_version=prompt_version,
        model=model,
        input_hash=key,
        output=output,
        valid=choice is not None,
        error=error,
        attempts=attempts,
        latency_ms=latency,
        usage=usage,
        cost_usd=usage.cost_usd(model) + retrieval_cost,
    )


# --------------------------------------------------------------------------- step 4: contrast


def contrast(
    engine: RulesEngine,
    *,
    fields: CaseFields,
    protocol_id: str | None,
    protocol_contrast: str | None,
    as_of: date,
) -> StepOutcome:
    data = ContrastInput(
        modality=fields.modality,
        protocol_contrast=protocol_contrast
        if protocol_contrast in ("none", "iv", "optional")
        else ("iv" if fields.contrast_requested else "none"),  # type: ignore[arg-type]
        contrast_requested=fields.contrast_requested,
        egfr=fields.egfr,
        egfr_date=fields.egfr_date,
        allergies=fields.allergies,
        medications=fields.medications_of_note,
        as_of=as_of,
    )
    check = engine.check(data)
    return StepOutcome(
        step="contrast",
        prompt_version=f"rules.v{engine.version}",
        model=None,
        input_hash=input_hash(
            {"input": data.__dict__, "protocol": protocol_id, "rules": engine.version}
        ),
        output={
            "protocol_id": protocol_id,
            "as_of": as_of.isoformat(),
            **check.model_dump(mode="json"),
        },
        valid=True,
    )
