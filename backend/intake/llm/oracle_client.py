"""Development stand-in for the model: answers from the gold labels.

Used for UI development, end-to-end tests and the demo when no API key is configured. Every
output it produces is recorded with model "dev-oracle", the dashboard labels such runs as
simulations, and they are never reported as model metrics. For documents that are not in the
gold set it returns an empty, low-confidence extraction (so they go to manual entry).
"""

import json
import re
from typing import Any

from pydantic import BaseModel

from intake.eval.gold import GoldCase, load_gold_set
from intake.llm.client import LLMRequest, LLMResult, TextPart, Usage
from intake.llm.fake_client import _bag_of_words_vector
from intake.schemas.extraction import LIST_FIELDS, SCALAR_FIELDS, RequisitionExtraction
from intake.settings import get_settings

ORACLE_MODEL = "dev-oracle"


def _text(request: LLMRequest[Any]) -> str:
    return "\n".join(p.text for p in request.parts if isinstance(p, TextPart))


class OracleClient:
    def __init__(self) -> None:
        self.cases = load_gold_set(get_settings().data_dir, "v1").cases

    def _find_by_document(self, text: str) -> GoldCase | None:
        lowered = text.lower()
        for case in self.cases:
            surname = case.fields.patient_name.split()[-1].lower()
            if surname in lowered and case.fields.health_card_last4 in text.replace(" ", ""):
                return case
        return None

    def _find_by_indication(self, text: str) -> GoldCase | None:
        for case in self.cases:
            if case.fields.clinical_indication.lower() in text.lower():
                return case
        return None

    def _extraction(self, case: GoldCase | None) -> dict[str, Any]:
        def field(value: Any, quote: str | None = None) -> dict[str, Any]:
            if value is None:
                return {"value": None, "confidence": "low", "evidence": None}
            q = quote if quote is not None else str(value)
            return {"value": value, "confidence": "high", "evidence": {"quote": q, "page": 1}}

        if case is None:
            empty: dict[str, Any] = {n: field(None) for n in SCALAR_FIELDS}
            empty.update({n: [] for n in LIST_FIELDS})
            return empty
        f = case.fields.model_dump(mode="json")
        out: dict[str, Any] = {}
        for name in SCALAR_FIELDS:
            value = f[name]
            quote = None
            if name == "patient_name":
                quote = value.split()[-1]
            elif name in (
                "modality",
                "laterality",
                "contrast_requested",
                "physician_marked_urgent",
            ):
                quote = {"modality": value, "laterality": value}.get(name, "Contrast")
                if name == "physician_marked_urgent":
                    quote = "Urgent" if value else "URGENT"
            elif name == "egfr" and value is not None:
                quote = f"{value:g}"
            out[name] = field(value, quote)
        for name in LIST_FIELDS:
            out[name] = [field(v) for v in f[name]]
        return out

    async def complete[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        text = _text(request)
        data: dict[str, Any]
        if request.purpose == "extract":
            data = self._extraction(self._find_by_document(text))
        elif request.purpose == "triage":
            case = self._find_by_indication(text)
            priority = case.priority if case else "P3"
            data = {
                "priority": priority,
                "red_flags": [],
                "rationale": "dev-oracle: gold label (simulation, not a model output)",
                "evidence": [],
                "confidence": "high" if case else "low",
            }
        elif request.purpose == "protocol":
            case = self._find_by_indication(text)
            ids = re.findall(r"^id: (\S+)$", text, flags=re.M)
            chosen = case.protocol_id if case and case.protocol_id in ids else ids[0]
            data = {
                "protocol_id": chosen,
                "contrast": "none",
                "rationale": "dev-oracle: gold label (simulation, not a model output)",
                "confidence": "high" if chosen == (case.protocol_id if case else None) else "low",
            }
        else:
            raise ValueError(f"unknown purpose {request.purpose}")
        parsed = request.output.model_validate_json(json.dumps(data))
        return LLMResult(parsed=parsed, model=ORACLE_MODEL, usage=Usage(), latency_ms=0)

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        return [_bag_of_words_vector(t) for t in texts], Usage()


__all__ = ["ORACLE_MODEL", "OracleClient", "RequisitionExtraction"]
