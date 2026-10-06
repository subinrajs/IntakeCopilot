"""Red-flag phrase rules: deterministic urgency floor that runs alongside the model's triage.

Phrases come from data/rules.yaml. Matching is whole-word and case-insensitive over the full
document text. A NegEx-style check skips a phrase when a negation cue appears shortly before it
in the same clause ("no saddle anaesthesia, no leg weakness"), unless a contrast word such as
"but" intervenes.
"""

import re
from dataclasses import dataclass
from typing import Literal

from intake.rules.engine import RulesFile

NEGATION_CUES = (
    "no",
    "not",
    "denies",
    "denied",
    "without",
    "negative for",
    "no evidence of",
    "absence of",
    "free of",
    "nil",
    "never",
    "ruled out",
)
NEGATION_WINDOW_WORDS = 6
CLAUSE_SPLIT = re.compile(r"[.;:\n!?]|\bbut\b|\bhowever\b|\bexcept\b", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class RedFlagHit:
    level: Literal["P1", "P2"]
    phrase: str
    context: str  # the clause it was found in, for the reviewer


def _negated(clause: str, start: int) -> bool:
    before = clause[:start].lower().split()[-NEGATION_WINDOW_WORDS:]
    window = " ".join(before)
    return any(re.search(rf"\b{re.escape(cue)}\b", window) for cue in NEGATION_CUES)


class RedFlagMatcher:
    def __init__(self, spec: RulesFile) -> None:
        self.patterns: list[tuple[Literal["P1", "P2"], str, re.Pattern[str]]] = []
        for level, phrases in spec.red_flags.items():
            for phrase in phrases:
                words = r"[\s-]+".join(re.escape(w) for w in re.split(r"[\s-]+", phrase))
                flags = 0 if phrase.isupper() else re.IGNORECASE  # "TIA" only in capitals
                self.patterns.append((level, phrase, re.compile(rf"\b{words}\b", flags)))

    def find(self, text: str) -> list[RedFlagHit]:
        hits: dict[str, RedFlagHit] = {}
        for clause in CLAUSE_SPLIT.split(text):
            clause = _SPACE.sub(" ", clause).strip()
            if not clause:
                continue
            for level, phrase, pattern in self.patterns:
                for match in pattern.finditer(clause):
                    if not _negated(clause, match.start()) and phrase not in hits:
                        hits[phrase] = RedFlagHit(level, phrase, clause[:200])
        return sorted(hits.values(), key=lambda h: (h.level, h.phrase))

    @staticmethod
    def floor(hits: list[RedFlagHit]) -> Literal["P1", "P2"] | None:
        """The most urgent level any unnegated red flag demands, or None."""
        levels = {h.level for h in hits}
        return "P1" if "P1" in levels else "P2" if "P2" in levels else None
