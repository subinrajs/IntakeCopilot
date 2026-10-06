# IntakeCopilot

A requisition intake assistant for a fictional MRI and CT clinic (Lakeshore MRI & CT). It reads
incoming imaging requisitions, extracts structured fields with evidence, suggests a priority
(P1–P4) and a protocol from the clinic's own protocol book, runs deterministic contrast safety
rules, and puts every case in front of a radiologist for approval.

**All data is synthetic.** Clinical content (protocol book, rubric, thresholds) is demo content.

> Work in progress: day 1 of a 10-day build. See `docs/adr/` for decisions made so far.

## Local development

Requires Docker, [uv](https://docs.astral.sh/uv/), Node 22+ and pnpm.

```sh
cp .env.example .env
make setup        # backend (uv) and web (pnpm) dependencies
make db-up        # Postgres 16 + pgvector on localhost:5433
make migrate      # apply backend/migrations/*.sql as the schema owner
make check        # lint, typecheck, tests (backend + web)
make api          # http://localhost:8000  (GET /healthz, /readyz)
make web          # http://localhost:5174
```

## Layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI API, pipeline worker, rules engine, eval runner (`intake` package) |
| `backend/migrations/` | Forward-only SQL migrations |
| `web/` | React + TypeScript app (intake queue, case review, evaluation dashboard) |
| `data/protocols.yaml` | Protocol book (30 MRI/CT protocols) |
| `data/rubric.md` | P1–P4 triage rubric, shared by the prompt, gold labels and this README |
| `data/gold/` | Frozen, labelled synthetic requisitions for evaluation |
| `prompts/` | Versioned prompts (`triage.v1.md`, ...) |
| `docs/adr/` | Architecture decision records |
