-- Pipeline support: step reuse across eval runs, live-update notifications, and the stored
-- query embedding used for retrieval.

-- A step row copied from an earlier identical step (same input hash, prompt version and model)
-- points at the original. Its tokens and cost are zero: nothing was spent producing it.
ALTER TABLE pipeline_steps ADD COLUMN reused_from bigint REFERENCES pipeline_steps;

-- Gold cases can be processed outside an eval run (e.g. `make seed-demo` precomputes them).
CREATE INDEX pipeline_steps_eval_idx ON pipeline_steps (eval_run_id, requisition_id, step);

-- Live updates: one NOTIFY per change, carrying ids only (never PHI). The API turns these into
-- server-sent events; the browser refetches through the authenticated API.
CREATE FUNCTION notify_intake_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  case_id uuid;
  detail text;
BEGIN
  -- Separate branches: PL/pgSQL resolves NEW.<column> per table, so a CASE over all tables fails.
  IF TG_TABLE_NAME = 'requisitions' THEN
    case_id := NEW.id;
    detail := NEW.status;
  ELSIF TG_TABLE_NAME = 'pipeline_steps' THEN
    case_id := NEW.requisition_id;
    detail := NEW.step;
  ELSE
    case_id := NEW.requisition_id;
    detail := NULL;
  END IF;
  PERFORM pg_notify(
    'intake_events',
    json_build_object('table', TG_TABLE_NAME, 'requisition_id', case_id::text,
                      'op', lower(TG_OP), 'detail', detail)::text
  );
  RETURN NEW;
END
$$;

CREATE TRIGGER requisitions_notify AFTER INSERT OR UPDATE OF status, locked_by ON requisitions
  FOR EACH ROW EXECUTE FUNCTION notify_intake_event();
CREATE TRIGGER pipeline_steps_notify AFTER INSERT ON pipeline_steps
  FOR EACH ROW EXECUTE FUNCTION notify_intake_event();
CREATE TRIGGER decisions_notify AFTER INSERT ON decisions
  FOR EACH ROW EXECUTE FUNCTION notify_intake_event();
