-- Development-only values. Replace credentials and use managed secrets in production.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_app') THEN
        CREATE ROLE crm_app LOGIN PASSWORD 'local_app_password'
          NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;
GRANT CONNECT ON DATABASE fundcrm TO crm_app;
GRANT USAGE ON SCHEMA public TO crm_app;
