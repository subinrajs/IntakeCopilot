# ADR 0001: Use OpenAI as the LLM provider, behind a vendor-neutral client

- Status: accepted
- Date: 2026-10-05
- Amends: the design document (which specifies Claude)

## Context

The design document specifies Claude for vision extraction, triage and protocol choice, with
every call returning data through a tool schema generated from a Pydantic model. The sibling
project ClinicVoice moved to OpenAI (its ADR 0004). Keeping one provider across the portfolio
means one key, one spend cap and one set of operational lessons. None of the safety design
(deterministic rules, more-urgent-wins, constrained protocol choice, human approval) depends on
the vendor.

## Decision

- `intake/llm/client.py` defines an `LLMClient` protocol: page images or text in, a validated
  Pydantic model out, plus token usage and latency. Pipeline steps depend only on it.
- `OpenAIClient` uses Structured Outputs (strict JSON schema derived from the Pydantic model).
  Strict mode requires every field to be present, so optional values are typed `T | None`.
- The protocol step builds its output model per call with `protocol_id` as a `Literal` of the
  five retrieved ids, so an invented protocol cannot be expressed (same guarantee as the
  design document's per-call tool enum).
- Models come from environment variables: `gpt-5.5` for extraction (vision) and triage,
  `gpt-5.4-mini` for protocol choice.
- Prompt caching is automatic for stable prefixes of 1024 tokens or more, so static
  instructions and the rubric come first and per-case content last. Cached tokens are
  recorded in `pipeline_steps.cached_input_tokens`.
- `RecordedClient` replays stored responses so pipeline tests run without network or cost.

## Consequences

- Changing provider again means one new adapter; prompts may need re-tuning, and the eval
  runner measures whether they do.
- The README and prompts say "the model", not a vendor name, except where describing setup.
