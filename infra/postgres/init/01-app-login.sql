-- Local only: the login user the API and worker connect as. Its privileges come from app_role,
-- which migration 0002 grants. In production, create this user by hand with a generated
-- password and add it to app_role.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_role') THEN
    CREATE ROLE app_role NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intake_app') THEN
    CREATE ROLE intake_app LOGIN PASSWORD 'app_dev_password' IN ROLE app_role;
  END IF;
END
$$;
