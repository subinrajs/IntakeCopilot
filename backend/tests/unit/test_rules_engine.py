from datetime import date, timedelta
from typing import Any

import pytest

from intake.eval.gold import load_gold_set
from intake.protocols import load_protocol_book
from intake.rules.engine import (
    ContrastInput,
    RulesEngine,
    RulesFile,
    UnsafeExpression,
    effective_contrast,
)
from intake.settings import get_settings

AS_OF = date(2026, 9, 15)


@pytest.fixture(scope="module")
def engine() -> RulesEngine:
    return RulesEngine.from_file(get_settings().data_dir / "rules.yaml")


def _input(**overrides: Any) -> ContrastInput:
    base: dict[str, Any] = {
        "modality": "CT",
        "protocol_contrast": "iv",
        "contrast_requested": True,
        "egfr": 80.0,
        "egfr_date": AS_OF - timedelta(days=10),
        "allergies": [],
        "medications": [],
        "as_of": AS_OF,
    }
    return ContrastInput(**(base | overrides))


def _ids(engine: RulesEngine, **overrides: Any) -> list[str]:
    return [f.id for f in engine.check(_input(**overrides)).fired]


def test_clear_when_nothing_fires(engine: RulesEngine) -> None:
    check = engine.check(_input())
    assert check.result == "clear" and check.fired == []


def test_missing_egfr_on_contrast_exam_needs_labs(engine: RulesEngine) -> None:
    check = engine.check(_input(egfr=None, egfr_date=None))
    assert check.result == "needs_labs"
    assert [f.id for f in check.fired] == ["egfr_missing"]


def test_no_contrast_means_no_renal_rules(engine: RulesEngine) -> None:
    assert _ids(engine, protocol_contrast="none", egfr=None, egfr_date=None) == []


@pytest.mark.parametrize(("egfr", "fires"), [(29.9, True), (30.0, False), (25.0, True)])
def test_egfr_threshold_boundary(engine: RulesEngine, egfr: float, fires: bool) -> None:
    assert ("egfr_low" in _ids(engine, egfr=egfr)) is fires


@pytest.mark.parametrize(("age_days", "fires"), [(90, False), (91, True)])
def test_egfr_recency_boundary(engine: RulesEngine, age_days: int, fires: bool) -> None:
    assert ("egfr_stale" in _ids(engine, egfr_date=AS_OF - timedelta(days=age_days))) is fires


def test_egfr_25_is_needs_review_with_message(engine: RulesEngine) -> None:
    check = engine.check(_input(egfr=25.0))
    assert check.result == "needs_review"
    assert check.fired[0].message == "eGFR 25 is below the review threshold of 30."


def test_worst_result_wins_and_all_fired_rules_are_listed(engine: RulesEngine) -> None:
    check = engine.check(_input(egfr=20.0, egfr_date=AS_OF - timedelta(days=200)))
    assert check.result == "needs_review"
    assert {f.id for f in check.fired} == {"egfr_stale", "egfr_low"}


def test_prior_contrast_reaction(engine: RulesEngine) -> None:
    assert _ids(engine, allergies=["Penicillin - rash"]) == []
    assert _ids(engine, allergies=["Iodinated contrast - anaphylaxis"]) == ["prior_reaction"]


def test_metformin_only_matters_for_ct_with_iv_contrast(engine: RulesEngine) -> None:
    meds = ["Metformin 500 mg twice daily"]
    assert _ids(engine, medications=meds) == ["metformin_iodinated"]
    assert _ids(engine, medications=meds, modality="MRI") == []


def test_optional_contrast_follows_the_request() -> None:
    assert effective_contrast("optional", True) == "iv"
    assert effective_contrast("optional", False) == "none"
    assert effective_contrast("optional", None) == "optional"
    assert effective_contrast("iv", False) == "iv"


def test_optional_protocol_without_request_needs_no_egfr(engine: RulesEngine) -> None:
    assert _ids(engine, protocol_contrast="optional", contrast_requested=False, egfr=None) == []


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('true')",
        "egfr.__class__",
        "open('x')",
        "[x for x in allergies]",
        "unknown_variable == 1",
        "lambda: 1",
    ],
)
def test_unsafe_expressions_are_rejected_at_load(expression: str) -> None:
    spec = RulesFile(
        version=1,
        params={},
        rules=[{"id": "x", "when": expression, "result": "needs_labs", "message": "x"}],
        red_flags={"P1": [], "P2": []},
    )
    with pytest.raises((UnsafeExpression, SyntaxError)):
        RulesEngine(spec)


def test_rules_reproduce_every_gold_contrast_label(engine: RulesEngine) -> None:
    """Contrast flag accuracy on gold inputs must be 100%: anything less is a bug."""
    settings = get_settings()
    book = load_protocol_book(settings.data_dir / "protocols.yaml")
    protocols = {p.id: p for p in book.protocols}
    for case in load_gold_set(settings.data_dir, "v1").cases:
        f = case.fields
        check = engine.check(
            ContrastInput(
                modality=f.modality,
                protocol_contrast=protocols[case.protocol_id].contrast,
                contrast_requested=f.contrast_requested,
                egfr=f.egfr,
                egfr_date=f.egfr_date,
                allergies=f.allergies,
                medications=f.medications_of_note,
                as_of=case.as_of,
            )
        )
        assert sorted(x.id for x in check.fired) == sorted(case.contrast_flags), case.case_key
