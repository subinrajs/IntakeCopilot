# ADR 0003: A page text index from the PDF text layer or OCR

- Status: accepted
- Date: 2026-10-05
- Resolves: design document open decision "page images only, or also PDF text"

## Context

The design checks every evidence quote against "the document text" and highlights evidence in
the review screen. Scanned and faxed requisitions (a third of the gold set) have no text layer,
so there is no text to check against and no coordinates to highlight. The red-flag rules also
need text: running them only on extracted fields misses a red flag the extraction left out.

## Decision

Every page is rendered once (150 DPI PNG, pypdfium2) and gets a text index: a list of spans
(text + pixel box). Spans come from the PDF text layer when the page has one, and from RapidOCR
(ONNX, pip-only, no system packages) on a 200 DPI render otherwise. The index is stored in
`requisition_pages` and used for:

1. evidence checks: fuzzy matching of each quote (rapidfuzz, threshold 85); an unfound quote
   lowers that field to low confidence and is marked "unverified";
2. highlights: matched spans' boxes are drawn over the page image (no react-pdf);
3. red-flag rules over the full document text plus the effective fields;
4. extraction input: the machine-read text goes to the model alongside the page images.

## Consequences

- OCR adds about 1-3 s per scanned page, once per requisition.
- Evidence validity becomes a measurable metric for scans too.
- Fuzzy matching tolerates OCR errors but can accept a near-miss quote; the threshold is a
  tunable in `intake/documents/evidence.py`.
