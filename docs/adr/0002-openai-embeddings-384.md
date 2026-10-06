# ADR 0002: OpenAI embeddings at 384 dimensions for protocol retrieval

- Status: accepted
- Date: 2026-10-05
- Resolves: design document open decision "local embedding model or hosted embeddings"

## Context

The design document suggests a local sentence-transformers model (bge-small, 384 dimensions).
That pulls in PyTorch (about 1 GB of memory), which does not fit a small always-on Render
instance running both the API and the worker. A hosted embedding API avoids that, and OpenAI
is already the LLM provider (ADR 0001), so no new vendor is added.

## Decision

Use `text-embedding-3-small` with `dimensions=384`. The `protocols.embedding vector(384)`
column and its HNSW index stay as designed. Protocol documents (~30) are embedded at seed time
and whenever an admin edits a protocol; each case's retrieval query is embedded once per case.

## Consequences

- Retrieval quality is measured, not assumed: the eval runner reports recall@5 against the
  gold protocol (target 98%). If it falls short, the fallback is a local ONNX model
  (fastembed, no PyTorch) behind the same `embed()` function.
- One small external call per case (fractions of a cent).
