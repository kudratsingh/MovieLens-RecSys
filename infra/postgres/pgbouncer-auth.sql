-- Password lookup for pgBouncer's auth_query mode.
--
-- Run once, as the superuser, inside the application database — the deployment
-- runbook's one-time provisioning block runs it immediately after creating the
-- roles. It is idempotent, so re-running it after a restore or a pgBouncer
-- upgrade is a no-op.
--
-- Why a function instead of pointing auth_query straight at pg_shadow: reading
-- pg_shadow needs superuser, and pgBouncer would then have to hold a superuser
-- credential to perform one lookup. A SECURITY DEFINER wrapper owned by the
-- superuser inverts that — pgbouncer_auth is a login role with CONNECT and
-- EXECUTE on this single function and nothing else, and its password buys an
-- attacker the stored SCRAM secrets, which are verifiers and cannot be replayed
-- as passwords.
--
-- Why this file is not in infra/postgres-init/: that directory is mounted into
-- the dev Postgres's docker-entrypoint-initdb.d and runs on a fresh volume,
-- where the pgbouncer_auth role does not exist. The guard below would then
-- abort the whole database initialization. The dev pooler authenticates with
-- auth_type = trust (infra/pgbouncer/pgbouncer.ini) and needs none of this.

-- Fail loudly and early rather than leave a lookup function nobody can call.
-- The role is created with a generated password by the provisioning block, so
-- this script cannot create it: it has no password to give it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'pgbouncer_auth') THEN
        RAISE EXCEPTION 'role "pgbouncer_auth" does not exist; create it first: CREATE ROLE pgbouncer_auth LOGIN PASSWORD ''<generated>''';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS pgbouncer_auth;

REVOKE ALL ON SCHEMA pgbouncer_auth FROM PUBLIC;
GRANT USAGE ON SCHEMA pgbouncer_auth TO pgbouncer_auth;

-- The output parameters are named usename/passwd because the auth_query
-- selects them by those names, and they are two columns in that order because
-- that is the shape pgBouncer requires. The body qualifies every column with
-- the table alias so a column can never be read as the parameter that shares
-- its name. search_path is pinned — pg_temp last, so a temporary object cannot
-- shadow anything the body resolves — because a SECURITY DEFINER function that
-- resolves names through the caller's search_path is a privilege-escalation
-- shape, whoever the caller happens to be today.
CREATE OR REPLACE FUNCTION pgbouncer_auth.user_lookup(
    IN i_username text,
    OUT usename name,
    OUT passwd text
)
RETURNS record
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT s.usename, s.passwd FROM pg_catalog.pg_shadow AS s WHERE s.usename = i_username;
$$;

REVOKE ALL ON FUNCTION pgbouncer_auth.user_lookup(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pgbouncer_auth.user_lookup(text) TO pgbouncer_auth;

-- pgBouncer runs the lookup on the connection's target database, so the role
-- needs CONNECT there. Taken from the current database rather than named, so
-- the script works unchanged against a restore drill's throwaway database.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pgbouncer_auth', current_database());
END
$$;

-- Note for whoever reads this next: pgbouncer_admin is deliberately not a
-- Postgres role. It exists only inside pgBouncer, as the admin-console identity
-- the API authenticates with at boot to assert transaction pool mode, and the
-- entrypoint renders it into the pooler's userlist in both auth modes.
