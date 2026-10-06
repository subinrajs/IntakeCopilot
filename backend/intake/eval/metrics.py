"""Evaluation metrics (design doc: Evaluation design). Pure functions over per-case results.

Every rate is reported with its numerator, denominator and a Wilson 95% interval: on a
60-case gold set, "under 3%" means at most one case, so bare percentages would mislead.
"""

import math
import re
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from rapidfuzz import fuzz

from intake.schemas.triage import PRIORITIES

_NON_WORD = re.compile(r"[^0-9a-z]+")
_TITLES = re.compile(r"\b(dr|md|mr|mrs|ms)\b")


def norm(value: Any) -> str:
    return _NON_WORD.sub(" ", str(value).lower()).strip()


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def rate(successes: int, total: int) -> dict[str, Any]:
    low, high = wilson(successes, total)
    return {
        "value": round(successes / total, 4) if total else None,
        "n": successes,
        "total": total,
        "ci95": [round(low, 4), round(high, 4)],
    }


# --------------------------------------------------------------------------- field comparison


def _text_match(threshold: float) -> Callable[[Any, Any], bool]:
    def compare(gold: Any, predicted: Any) -> bool:
        if gold in (None, "") or predicted in (None, ""):
            return gold in (None, "") and predicted in (None, "")
        return fuzz.ratio(norm(gold), norm(predicted)) >= threshold

    return compare


def _name_match(gold: Any, predicted: Any) -> bool:
    if not gold or not predicted:
        return not gold and not predicted
    a = _TITLES.sub(" ", norm(gold)).split()
    b = _TITLES.sub(" ", norm(predicted)).split()
    return sorted(a) == sorted(b)


def _digits_match(gold: Any, predicted: Any) -> bool:
    def digits(v: Any) -> str:
        return re.sub(r"\D", "", str(v or ""))

    return digits(gold) == digits(predicted)


def _exact(gold: Any, predicted: Any) -> bool:
    if isinstance(gold, date) or isinstance(predicted, date):
        return str(gold) == str(predicted)
    return bool(gold == predicted)


def _number(gold: Any, predicted: Any) -> bool:
    if gold is None or predicted is None:
        return gold is None and predicted is None
    return abs(float(gold) - float(predicted)) < 0.05


def _list_match(gold: list[str] | None, predicted: list[str] | None) -> bool:
    """Every gold item matched once (fuzzy) and no extra items. Missing means empty."""
    remaining = [norm(p) for p in predicted or []]
    for item in gold or []:
        target = norm(item)
        best = max(
            range(len(remaining)), default=None, key=lambda i: fuzz.ratio(target, remaining[i])
        )
        if best is None or fuzz.ratio(target, remaining[best]) < 85:
            return False
        remaining.pop(best)
    return not remaining


FIELD_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "patient_name": _name_match,
    "dob": _exact,
    "health_card_last4": _digits_match,
    "referrer_name": _name_match,
    "referrer_billing_number": _digits_match,
    "modality": _exact,
    "body_part": _text_match(85),
    "laterality": _exact,
    "contrast_requested": _exact,
    "clinical_indication": _text_match(90),
    "relevant_history": _list_match,
    "allergies": _list_match,
    "egfr": _number,
    "egfr_date": _exact,
    "medications_of_note": _list_match,
    "physician_marked_urgent": _exact,
}


def compare_fields(gold: dict[str, Any], predicted: dict[str, Any] | None) -> dict[str, bool]:
    if predicted is None:
        return dict.fromkeys(FIELD_COMPARATORS, False)
    return {
        name: cmp(gold.get(name), predicted.get(name)) for name, cmp in FIELD_COMPARATORS.items()
    }


# --------------------------------------------------------------------------- priority


def urgency(priority: str | None) -> int:
    """0 = P1 (most urgent). A missing suggestion counts as the least urgent (worst case)."""
    return PRIORITIES.index(priority) if priority in PRIORITIES else len(PRIORITIES) - 1


def under_triaged(gold: str, suggested: str | None) -> bool:
    return urgency(gold) < urgency(suggested)


def over_triaged(gold: str, suggested: str | None) -> bool:
    return suggested is not None and urgency(gold) > urgency(suggested)


def severe_under_triage(gold: str, suggested: str | None) -> bool:
    """The release gate's hard line: a P1 case suggested as P3 or P4 (or not at all)."""
    return gold == "P1" and urgency(suggested) >= 2


def confusion(cases: Iterable[dict[str, Any]], key: str = "priority") -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {
        g: {s: 0 for s in [*PRIORITIES, "none"]} for g in PRIORITIES
    }
    for case in cases:
        matrix[case["gold_priority"]][case.get(key) or "none"] += 1
    return matrix


# --------------------------------------------------------------------------- aggregation


def aggregate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def subset(difficulty: str | None) -> list[dict[str, Any]]:
        return [c for c in cases if difficulty is None or c["difficulty"] == difficulty]

    def summarise(group: list[dict[str, Any]]) -> dict[str, Any]:
        fields_correct = sum(c["fields_correct"] for c in group)
        fields_total = sum(c["fields_total"] for c in group)
        evidence_found = sum(c["evidence_found"] for c in group)
        evidence_total = sum(c["evidence_total"] for c in group)
        n = len(group)
        costs = [c["cost_usd"] for c in group]
        latencies = sorted(c["latency_ms"] for c in group if c["latency_ms"] is not None)
        return {
            "cases": n,
            "field_accuracy": rate(fields_correct, fields_total),
            "evidence_validity": rate(evidence_found, evidence_total),
            "priority_agreement": rate(sum(c["priority"] == c["gold_priority"] for c in group), n),
            "model_priority_agreement": rate(
                sum(c["model_priority"] == c["gold_priority"] for c in group), n
            ),
            "under_triage": rate(
                sum(under_triaged(c["gold_priority"], c["priority"]) for c in group), n
            ),
            "model_under_triage": rate(
                sum(under_triaged(c["gold_priority"], c["model_priority"]) for c in group), n
            ),
            "over_triage": rate(
                sum(over_triaged(c["gold_priority"], c["priority"]) for c in group), n
            ),
            "severe_under_triage": sum(
                severe_under_triage(c["gold_priority"], c["priority"]) for c in group
            ),
            "retrieval_recall_at_5": rate(sum(c["recall_at_5"] for c in group), n),
            "protocol_top1": rate(sum(c["protocol_id"] == c["gold_protocol_id"] for c in group), n),
            "contrast_flag_accuracy": rate(sum(c["contrast_ok"] for c in group), n),
            "manual_entry": sum(c["status"] != "done" for c in group),
            "cost_usd_per_case": round(sum(costs) / n, 5) if n else None,
            "cost_usd_total": round(sum(costs), 4),
            "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
            "latency_ms_max": latencies[-1] if latencies else None,
        }

    return {
        "all": summarise(subset(None)),
        "clean": summarise(subset("clean")),
        "hard": summarise(subset("hard")),
        "confusion": confusion(cases),
        "model_confusion": confusion(cases, "model_priority"),
        "field_errors": _field_error_counts(cases),
    }


def _field_error_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(FIELD_COMPARATORS, 0)
    for case in cases:
        for name in case.get("field_errors", []):
            counts[name] = counts.get(name, 0) + 1
    return counts


def release_gate(candidate: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """A prompt version becomes the default only if: under-triage does not increase, no P1 case
    is suggested as P3/P4, and field accuracy does not drop (design doc: Release gate)."""
    c = candidate["all"]
    checks = [
        {
            "check": "no P1 suggested as P3/P4",
            "ok": c["severe_under_triage"] == 0,
            "value": c["severe_under_triage"],
        }
    ]
    if baseline is not None:
        b = baseline["all"]
        checks.append(
            {
                "check": "under-triage does not increase",
                "ok": c["under_triage"]["n"] <= b["under_triage"]["n"],
                "value": [b["under_triage"]["n"], c["under_triage"]["n"]],
            }
        )
        checks.append(
            {
                "check": "field accuracy does not drop",
                "ok": (c["field_accuracy"]["value"] or 0) >= (b["field_accuracy"]["value"] or 0),
                "value": [b["field_accuracy"]["value"], c["field_accuracy"]["value"]],
            }
        )
    return {"passed": all(x["ok"] for x in checks), "checks": checks}
