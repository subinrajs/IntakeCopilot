-- Evaluation support: the repeat pass that exposes non-determinism is its own (hidden) run,
-- linked to the run it repeats; failed runs keep their error.
ALTER TABLE eval_runs ADD COLUMN repeat_of uuid REFERENCES eval_runs;
ALTER TABLE eval_runs ADD COLUMN error text;
CREATE INDEX eval_runs_listing_idx ON eval_runs (started_at DESC) WHERE repeat_of IS NULL;
