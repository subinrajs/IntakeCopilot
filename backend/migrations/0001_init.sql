-- Core schema. Follows the design doc's data model, plus the additions in the implementation
-- strategy: requisitions.source, pipeline_steps.eval_run_id and the step cache key, a fixed
-- as_of date and gold contrast flags on eval cases, a per-page text index, jobs and users.
CREATE EXTENSION IF NOT EXISTS vector;

-- Demo staff accounts. A production version would use SSO with MFA.
CREATE TABLE users (
  id             text PRIMARY KEY,
  display_name   text NOT NULL,
  role           text NOT NULL CHECK (role IN ('intake', 'radiologist', 'admin')),
  password_hash  text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- One row per uploaded requisition (a "case"). Gold-set cases have source = 'gold' and never
-- appear in the review queue.
CREATE TABLE requisitions (
  id                 uuid PRIMARY KEY,
  source             text NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'gold')),
  file_key           text NOT NULL,
  file_sha256        text NOT NULL,
  content_type       text NOT NULL CHECK (content_type IN ('application/pdf', 'image/png', 'image/jpeg')),
  original_filename  text,
  pages              int,
  uploaded_by        text NOT NULL,
  uploaded_at        timestamptz NOT NULL DEFAULT now(),
  status             text NOT NULL DEFAULT 'uploaded' CHECK (status IN (
                       'uploaded', 'processing', 'manual_entry', 'ready_for_review',
                       'in_review', 'approved', 'rejected', 'exported')),
  status_changed_at  timestamptz NOT NULL DEFAULT now(),
  locked_by          text,
  locked_at          timestamptz
);
CREATE INDEX requisitions_queue_idx ON requisitions (status, status_changed_at) WHERE source = 'live';

-- Rendered page image plus a word-level text index (from the PDF text layer, or OCR for scans).
-- Used to check evidence quotes, draw highlight boxes and run red-flag rules on the full text.
CREATE TABLE requisition_pages (
  requisition_id  uuid NOT NULL REFERENCES requisitions ON DELETE CASCADE,
  page_no         int NOT NULL CHECK (page_no >= 1),
  image_key       text NOT NULL,
  width_px        int NOT NULL,
  height_px       int NOT NULL,
  text_source     text NOT NULL CHECK (text_source IN ('text_layer', 'ocr')),
  text            text NOT NULL,
  words           jsonb NOT NULL,   -- [{"t": "word", "b": [x0, y0, x1, y1]}], pixel coordinates
  PRIMARY KEY (requisition_id, page_no)
);

CREATE TABLE protocols (
  id            text PRIMARY KEY,
  modality      text NOT NULL CHECK (modality IN ('MRI', 'CT')),
  body_part     text NOT NULL,
  name          text NOT NULL,
  contrast      text NOT NULL CHECK (contrast IN ('none', 'iv', 'optional')),
  indications   text NOT NULL,
  slot_minutes  int NOT NULL,
  embedding     vector(384),
  search_tsv    tsvector GENERATED ALWAYS AS (
                  to_tsvector('english', name || ' ' || body_part || ' ' || indications)) STORED,
  version       int NOT NULL DEFAULT 1,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX protocols_embedding_idx ON protocols USING hnsw (embedding vector_cosine_ops);
CREATE INDEX protocols_search_idx ON protocols USING gin (search_tsv);

CREATE TABLE eval_cases (
  requisition_id       uuid PRIMARY KEY REFERENCES requisitions,
  case_key             text NOT NULL UNIQUE,          -- e.g. gold-v1-017
  gold_version         text NOT NULL,                 -- gold sets are frozen: v1, v2, ...
  gold_fields          jsonb NOT NULL,
  gold_priority        text NOT NULL CHECK (gold_priority IN ('P1', 'P2', 'P3', 'P4')),
  gold_protocol_id     text NOT NULL REFERENCES protocols,
  gold_contrast_flags  jsonb NOT NULL DEFAULT '[]',   -- ids of rules that must fire
  as_of                date NOT NULL,                 -- fixed "today" so date rules never drift
  difficulty           text NOT NULL CHECK (difficulty IN ('clean', 'hard')),
  notes                text
);

CREATE TABLE eval_runs (
  id               uuid PRIMARY KEY,
  gold_version     text NOT NULL,
  prompt_versions  jsonb NOT NULL,
  models           jsonb NOT NULL,
  label            text,
  started_by       text NOT NULL,
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  status           text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
  metrics          jsonb,
  per_case         jsonb
);

-- Every AI (and rules) output, one immutable row per attempt. eval_run_id is null for live
-- cases. input_hash + step + prompt_version + model is the cache key the eval runner reuses.
CREATE TABLE pipeline_steps (
  id                   bigserial PRIMARY KEY,
  requisition_id       uuid NOT NULL REFERENCES requisitions,
  eval_run_id          uuid REFERENCES eval_runs,
  step                 text NOT NULL CHECK (step IN ('extract', 'triage', 'protocol', 'contrast')),
  prompt_version       text NOT NULL,                 -- for 'contrast', the rules version
  model                text,                          -- null for rules-only steps
  input_hash           text NOT NULL,
  output               jsonb NOT NULL,
  valid                boolean NOT NULL,
  error                text,
  attempts             int NOT NULL DEFAULT 1,
  latency_ms           int,
  input_tokens         int,
  cached_input_tokens  int,
  output_tokens        int,
  cost_usd             numeric(8, 5),
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pipeline_steps_case_idx ON pipeline_steps (requisition_id, step, created_at DESC);
CREATE INDEX pipeline_steps_cache_idx ON pipeline_steps (step, input_hash, prompt_version, model)
  WHERE valid;

CREATE TABLE field_corrections (
  id               bigserial PRIMARY KEY,
  requisition_id   uuid NOT NULL REFERENCES requisitions,
  field_path       text NOT NULL,
  ai_value         jsonb,
  corrected_value  jsonb,
  corrected_by     text NOT NULL,
  corrected_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE decisions (
  id              bigserial PRIMARY KEY,
  requisition_id  uuid NOT NULL REFERENCES requisitions,
  kind            text NOT NULL CHECK (kind IN ('priority', 'protocol', 'contrast', 'case')),
  ai_value        text,
  final_value     text,
  action          text NOT NULL CHECK (action IN ('accept', 'override', 'reject', 'acknowledge')),
  reason          text,
  decided_by      text NOT NULL,
  decided_at      timestamptz NOT NULL DEFAULT now(),
  -- Enforced here as well as in the API: overrides and rejections always carry a reason.
  CHECK (action NOT IN ('override', 'reject') OR length(trim(coalesce(reason, ''))) > 0)
);
CREATE INDEX decisions_case_idx ON decisions (requisition_id);

-- A small Postgres-backed queue claimed with FOR UPDATE SKIP LOCKED (same design as
-- ClinicVoice ADR 0005). Payloads carry ids only, never PHI.
CREATE TABLE jobs (
  id            bigserial PRIMARY KEY,
  type          text NOT NULL,
  payload       jsonb NOT NULL,
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  attempts      int NOT NULL DEFAULT 0,
  max_attempts  int NOT NULL DEFAULT 3,
  run_after     timestamptz NOT NULL DEFAULT now(),
  last_error    text,
  dedupe_key    text UNIQUE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim_idx ON jobs (run_after) WHERE status = 'queued';

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text NOT NULL,          -- 'user:<id>', 'worker' or 'system'
  action     text NOT NULL,
  entity     text NOT NULL,
  entity_id  text NOT NULL,
  detail     jsonb
);
CREATE INDEX audit_log_entity_idx ON audit_log (entity, entity_id, at);
