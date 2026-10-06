-- The API and worker connect as a login user that is a member of app_role. Migrations run as
-- the schema owner, so the REVOKE on audit_log is effective for the app (owners bypass grants).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role') THEN
    CREATE ROLE app_role NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;
-- Tables created by later migrations (run by the same owner) get the same grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_role;

-- Insert-only trails: the audit log, and AI outputs (never overwritten).
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON pipeline_steps FROM app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON decisions FROM app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON field_corrections FROM app_role;
-- Migration bookkeeping is owner-only.
REVOKE ALL ON schema_migrations FROM app_role;
