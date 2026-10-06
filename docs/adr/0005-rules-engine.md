# ADR 0005: Contrast rules as a safe expression subset, not eval

- Status: accepted
- Date: 2026-10-05

## Context

Contrast safety must be decided by rules a clinician can read and change, not by the model,
and every result must name the rule that fired. The design's `rules.yaml` uses expressions
such as `contrast in [iv, optional] and egfr < egfr_review_threshold`.

## Decision

- `data/rules.yaml` holds parameters, rules (`when` expression, result, message) and red-flag
  phrases. `intake/rules/engine.py` parses each `when` with `ast` and evaluates it with a small
  interpreter that accepts only boolean logic, comparisons, literals, known variables and two
  functions (`days_since`, `any_match`). Anything else is rejected when the file loads.
- A comparison involving a missing value is false; missing data is caught by explicit rules
  (`egfr_missing`).
- The engine takes an explicit `as_of` date, so date rules never drift: gold cases use a frozen
  date and live cases their upload date.
- Effective contrast: `iv` and `none` protocols are taken as is; an `optional` protocol uses
  contrast when the request asks for it, and stays `optional` (rules apply) when it does not say.
- Worst result wins (needs_review > needs_labs > clear); all fired rules are shown and each
  must be acknowledged before approval. The rules version is stored with every check.

## Consequences

Boundary tests (eGFR exactly 30, eGFR exactly 90 days old) pin the semantics. A test replays
every gold case through the engine: contrast flag accuracy on gold inputs must be 100%.
