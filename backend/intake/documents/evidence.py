"""Check that evidence quotes really appear in the document, and locate them for highlighting.

Matching is fuzzy (OCR makes small errors, and models normalise whitespace and punctuation).
A quote that cannot be found lowers that field's confidence to "low", so the reviewer sees it.
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from intake.documents.pages import Span

MATCH_THRESHOLD = 85.0
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalise(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


@dataclass(frozen=True)
class PageIndex:
    page_no: int
    spans: list[Span]

    @property
    def joined(self) -> tuple[str, list[tuple[int, int, Span]]]:
        """Normalised page text, plus each span's character range within it."""
        parts: list[str] = []
        ranges: list[tuple[int, int, Span]] = []
        position = 0
        for span in self.spans:
            text = normalise(span.text)
            if not text:
                continue
            ranges.append((position, position + len(text), span))
            parts.append(text)
            position += len(text) + 1
        return " ".join(parts), ranges


@dataclass(frozen=True)
class QuoteMatch:
    page_no: int
    score: float
    boxes: list[tuple[int, int, int, int]]

    @property
    def found(self) -> bool:
        return self.score >= MATCH_THRESHOLD


def locate_quote(quote: str, pages: list[PageIndex], page_hint: int | None = None) -> QuoteMatch:
    """Best fuzzy match of the quote on any page (the hinted page is tried first)."""
    needle = normalise(quote)
    best = QuoteMatch(page_no=page_hint or 1, score=0.0, boxes=[])
    if not needle:
        return best
    ordered = sorted(pages, key=lambda p: p.page_no != page_hint)
    for page in ordered:
        haystack, ranges = page.joined
        if not haystack:
            continue
        if needle in haystack:
            start = haystack.index(needle)
            score, end = 100.0, start + len(needle)
        else:
            alignment = fuzz.partial_ratio_alignment(needle, haystack)
            if alignment is None:
                continue
            score, start, end = alignment.score, alignment.dest_start, alignment.dest_end
        if score > best.score:
            boxes = [span.box for s, e, span in ranges if s < end and e > start]
            best = QuoteMatch(page.page_no, score, boxes)
            if score == 100.0:
                break
    return best
