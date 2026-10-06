"""Step 2 output: suggested priority with red flags and cited evidence."""

from typing import Literal

from intake.schemas.extraction import Confidence, Evidence, Strict

Priority = Literal["P1", "P2", "P3", "P4"]
PRIORITIES: tuple[Priority, ...] = ("P1", "P2", "P3", "P4")


def more_urgent(a: Priority, b: Priority) -> Priority:
    """P1 is the most urgent. Used wherever two opinions disagree: the more urgent wins."""
    return a if PRIORITIES.index(a) <= PRIORITIES.index(b) else b


class TriageSuggestion(Strict):
    priority: Priority
    red_flags: list[str]
    rationale: str
    evidence: list[Evidence]
    confidence: Confidence
