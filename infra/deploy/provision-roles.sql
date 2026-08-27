-- One-time database provisioning for a production deployment.
--
-- Run once, as the superuser, inside the application database, BEFORE the
-- release job's first migration. It is idempotent, so re-running it after a
-- password rotation or a restore is safe and is how a rotation is applied.
--
-- Why it exists at all: migration 0001 creates app_user, admin_user and
-- migrator with the literal passwords 'app_user', 'admin_user' and 'migrator',
-- which are checked into a public repository. Its DO blocks are guarded with
-- `IF NOT EXISTS`, and that guard is the intended -- and until now
-- undocumented -- escape hatch: create the three roles here with generated
-- passwords and the migration leaves them alone.
--
-- The second reason is privilege. The deployment runs Alembic as `migrator`
-- rather than as the superuser, which is what makes `migrator` own every base
-- table and therefore what makes `ALTER TABLE ... FORCE ROW LEVEL SECURITY`
-- and the 0010 backfill work. But migration 0001 also *grants* CONNECT and
-- USAGE to the other two roles, and migration 0007 creates a schema -- neither
-- of which a plain login role may do. The grants below are exactly the
-- privileges those statements need and nothing more.
--
-- Expects four psql variables, passed with -v so no password is ever written
-- into this file or into the server log:
--   app_password, admin_password, migrator_password, pgbouncer_auth_password
--
-- Run infra/postgres/pgbouncer-auth.sql immediately after this file: it
-- installs the SECURITY DEFINER lookup that pgBouncer's auth_query mode calls,
-- and it refuses to install if pgbouncer_auth does not exist yet.
--
-- Note what is deliberately absent: `pgbouncer_admin`. That identity is
-- internal to pgBouncer -- it authenticates against the pooler's own
-- userlist.txt to open the admin console -- and has no Postgres role behind
-- it. Creating one here would suggest the API's boot check authenticates
-- against the database, which it does not.
--
-- Every role statement is built with `format(... %L ...)` and run through
-- \gexec rather than written inside a DO block: psql does not interpolate its
-- variables inside dollar-quoted strings, so a DO block would send the literal
-- text :'app_password' to the server and set a password nobody can guess and
-- nobody knows.

\set ON_ERROR_STOP on

BEGIN;

-- app_user: the only role a request handler ever connects as. Plain LOGIN,
-- deliberately NOT BYPASSRLS and NOT SUPERUSER -- src/serving/startup_checks.py
-- refuses to boot the API if this role can bypass row-level security, because
-- an RLS policy the serving role can step over is not an isolation boundary at
-- all (ADR 0008, non-negotiable #9).
SELECT format(
    '%s ROLE app_user WITH LOGIN NOBYPASSRLS PASSWORD %L',
    CASE WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_user')
         THEN 'ALTER' ELSE 'CREATE' END,
    :'app_password'
) \gexec

-- admin_user: BYPASSRLS. Feast materialization and both sidecars connect as
-- this role, direct to Postgres rather than through the pooler, because the
-- feature snapshots are computed across every tenant in one pass.
SELECT format(
    '%s ROLE admin_user WITH LOGIN BYPASSRLS PASSWORD %L',
    CASE WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'admin_user')
         THEN 'ALTER' ELSE 'CREATE' END,
    :'admin_password'
) \gexec

-- migrator: BYPASSRLS so an RLS-enabling migration cannot cage the migration
-- applying it, and the owner of every table the schema step creates.
SELECT format(
    '%s ROLE migrator WITH LOGIN BYPASSRLS PASSWORD %L',
    CASE WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'migrator')
         THEN 'ALTER' ELSE 'CREATE' END,
    :'migrator_password'
) \gexec

-- pgbouncer_auth: the pooler's lookup role. It holds CONNECT and EXECUTE on
-- one SECURITY DEFINER function and nothing else, so its password buys an
-- attacker the stored SCRAM verifiers -- which verify a client but cannot be
-- replayed as passwords.
SELECT format(
    '%s ROLE pgbouncer_auth WITH LOGIN NOBYPASSRLS PASSWORD %L',
    CASE WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'pgbouncer_auth')
         THEN 'ALTER' ELSE 'CREATE' END,
    :'pgbouncer_auth_password'
) \gexec

-- The privileges migration 0001 and migration 0007 exercise on migrator's
-- behalf. GRANT OPTION is what lets migrator pass CONNECT and USAGE on to
-- app_user and admin_user the way migration 0001 does; CREATE ON DATABASE is
-- what lets migration 0007 create the feature_store schema. Ownership of the
-- database and of the public schema deliberately stays with the superuser --
-- migrator gets the statements it makes, not the keys to the building.
GRANT CONNECT ON DATABASE :"DBNAME" TO migrator WITH GRANT OPTION;
GRANT CREATE ON DATABASE :"DBNAME" TO migrator;
GRANT USAGE, CREATE ON SCHEMA public TO migrator WITH GRANT OPTION;

-- The model-server's pre-deploy fence reads public.alembic_version to learn
-- whether the release job's schema has arrived, and it runs as admin_user
-- because that is the identity the features image carries. alembic_version is
-- created by whoever ran the migration and carries no GRANT of its own, so
-- without this the fence fails with insufficient_privilege on every deploy.
-- It has to be set before the first migration: default privileges apply only
-- to objects created after them.
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO admin_user;

COMMIT;

\echo 'provision-roles: app_user, admin_user, migrator and pgbouncer_auth are provisioned'
