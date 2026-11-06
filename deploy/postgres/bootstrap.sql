-- Runs once as a superuser, before migrations. Login passwords are set separately,
-- so this file holds no secrets.
--   docuflow_migrator  owns the schema
--   docuflow_app       runtime role; row-level security always applies
--   docuflow_platform  cross-tenant operator role, used only outside request handling

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'docuflow_migrator') THEN
    CREATE ROLE docuflow_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'docuflow_app') THEN
    CREATE ROLE docuflow_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'docuflow_platform') THEN
    CREATE ROLE docuflow_platform NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
  END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO docuflow_migrator;
GRANT USAGE ON SCHEMA public TO docuflow_app, docuflow_platform;

ALTER DEFAULT PRIVILEGES FOR ROLE docuflow_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docuflow_app, docuflow_platform;
ALTER DEFAULT PRIVILEGES FOR ROLE docuflow_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO docuflow_app, docuflow_platform;
