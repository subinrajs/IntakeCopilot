from datetime import date

import pytest

from intake.eval import metrics


def test_wilson_interval_is_wide_on_small_samples() -> None:
    low, high = metrics.wilson(1, 60)
    assert low < 0.017 < high
    assert high > 0.08  # one miss in 60 cannot prove "under 3%"


def test_rate_reports_counts_and_interval() -> None:
    r = metrics.rate(57, 60)
    assert r["value"] == 0.95 and r["n"] == 57 and r["total"] == 60
    assert r["ci95"][0] < 0.95 < r["ci95"][1]


@pytest.mark.parametrize(
    ("gold", "suggested", "under", "over"),
    [
        ("P1", "P2", True, False),
        ("P3", "P2", False, True),
        ("P2", "P2", False, False),
        ("P2", None, True, False),
    ],
)
def test_under_and_over_triage(gold: str, suggested: str | None, under: bool, over: bool) -> None:
    assert metrics.under_triaged(gold, suggested) is under
    assert metrics.over_triaged(gold, suggested) is over


@pytest.mark.parametrize(
    ("suggested", "severe"),
    [("P1", False), ("P2", False), ("P3", True), ("P4", True), (None, True)],
)
def test_severe_under_triage_is_p1_as_p3_or_p4(suggested: str | None, severe: bool) -> None:
    assert metrics.severe_under_triage("P1", suggested) is severe


def test_field_comparators_normalise_sensibly() -> None:
    gold = {
        "patient_name": "Maria Andrews",
        "dob": "1984-05-31",
        "health_card_last4": "1828",
        "referrer_name": "Gary Dalton",
        "referrer_billing_number": "666458",
        "modality": "MRI",
        "body_part": "Abdomen (MRCP)",
        "laterality": "none",
        "contrast_requested": False,
        "clinical_indication": "Neck pain with left arm paresthesia",
        "relevant_history": ["A"],
        "allergies": [],
        "egfr": 84.0,
        "egfr_date": date(2026, 9, 1),
        "medications_of_note": ["Metformin 500 mg twice daily", "Ramipril 5 mg"],
        "physician_marked_urgent": True,
    }
    predicted = dict(gold) | {
        "patient_name": "ANDREWS Maria",
        "referrer_name": "Dr. Gary Dalton",
        "body_part": "abdomen mrcp",
        "clinical_indication": "Neck pain with left arm paraesthesia",
        "egfr_date": "2026-09-01",
        "egfr": 84,
        "medications_of_note": ["Ramipril 5mg", "Metformin 500 mg twice daily"],
    }
    assert all(metrics.compare_fields(gold, predicted).values())


def test_list_fields_penalise_missing_and_extra_items() -> None:
    gold = {"relevant_history": ["Hypertension"]}
    assert not metrics.compare_fields(gold, {"relevant_history": []})["relevant_history"]
    extra = {"relevant_history": ["Hypertension", "Asthma"]}
    assert not metrics.compare_fields(gold, extra)["relevant_history"]


def test_missing_extraction_scores_every_field_wrong() -> None:
    assert not any(metrics.compare_fields({"dob": "x"}, None).values())


def _summary(under: int, severe: int, field_value: float) -> dict[str, object]:
    return {
        "all": {
            "under_triage": {"n": under},
            "severe_under_triage": severe,
            "field_accuracy": {"value": field_value},
        }
    }


def test_release_gate() -> None:
    baseline = _summary(under=3, severe=0, field_value=0.95)
    assert metrics.release_gate(_summary(1, 0, 0.96), baseline)["passed"]
    assert not metrics.release_gate(_summary(4, 0, 0.96), baseline)["passed"]  # more under
    assert not metrics.release_gate(_summary(1, 1, 0.96), baseline)["passed"]  # P1 as P3/P4
    assert not metrics.release_gate(_summary(1, 0, 0.94), baseline)["passed"]  # fields drop
    assert metrics.release_gate(_summary(1, 0, 0.5), None)["passed"]  # first run: hard line only
