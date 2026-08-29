# Security policy

This is a personal portfolio project, but it runs real security machinery —
Keycloak OIDC with a realm per tenant, JWT validation against a cached JWKS,
PostgreSQL row-level security as the tenant boundary, a token-bucket rate
limiter, and a deploy path that puts all of it on a public host behind Caddy.
A report against any of that is on-topic and welcome.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the repository's **Security** tab
→ **Report a vulnerability**. That opens a private advisory thread visible only
to the maintainer.

If that option is not visible (private reporting has to be enabled on the
repository, and it may not be yet), email the address on the maintainer's
GitHub profile — `<owner's email in the GitHub profile>` — with `SECURITY` in
the subject line. Please do not open a public issue for a vulnerability, and
please do not disclose publicly until there has been a chance to fix it.

Useful in a report: the endpoint or component, the exact request, the observed
result, and which of the guarantees below it breaks. The `X-Request-ID` header
is echoed on every response and stored on the audit row as `correlation_id`, so
including it makes a report trivially reproducible.

This is maintained by one person, in evenings. Expect an acknowledgement within
a few days rather than within hours, and no fixed remediation SLA. **There is no
bug bounty** and no payment of any kind — reports are accepted on that basis.

## What the project considers highest severity

Stated so a report can be aimed rather than guessed at. These are the
project's own non-negotiables, not aspirations:

1. **Cross-tenant data leakage.** Any code path that returns one tenant's data
   in response to another tenant's request. RLS is `FORCE`d on every scoped
   table and the auth middleware sets `app.tenant_id` inside a per-request
   transaction, so a leak means either the database enforcement or the
   middleware boundary failed — both are serious.
2. **Authentication bypass.** Every endpoint requires a valid token except
   `GET /healthz` and `GET /readyz`, which carry no tenant or user data. A way
   to reach anything else without a valid token, or to have a token for tenant
   A resolve to tenant B, is in this class.
3. **Persona impersonation.** Selecting an arbitrary persona requires the
   confidential service client or the `demo-impersonator` realm role. A way
   around that check is in this class.
4. **Secret exposure.** The TMDB token, the model-server token, and every
   database credential are meant to stay server-side. A path that returns one
   to a browser, a log, or a response body is in this class.

## In scope

- The FastAPI service in `src/` — auth middleware, tenancy, serving,
  the rate limiter, the audit writer, the TMDB proxy.
- The Next.js app in `web/`, including the BFF session boundary and its
  Origin/CSRF handling.
- The Compose stacks (`docker-compose.yml`, `.demo.yml`, `.prod.yml`) as
  configuration — for example a production stack that publishes a port it
  should not, or that would boot with a development flag set.
- `infra/deploy/`, `infra/host/`, and the GitHub Actions workflows, including
  anything that could let a change reach the production host without passing
  the gates.
- Any deployment run from this repository.

## Out of scope

- The MovieLens dataset and its contents. It is a public research dataset.
- Vulnerabilities in third-party services and images themselves — Keycloak,
  Postgres, Redis, TMDB, GitHub Actions. Report those upstream. A report about
  *how this project configures* one of them is in scope.
- The development-only credentials described below.
- Volumetric denial of service against a demo or portfolio host. The system is
  one small VPS and does not pretend otherwise; the single-host failure domain
  is a documented, deliberate tradeoff (ADR 0013).
- Scanner output with no demonstrated impact on this codebase.

## The development credentials in this repository

`docker-compose.yml` and `infra/keycloak/realms/*.json` contain literal
passwords, an `admin`/`admin` Keycloak account, and a client secret whose value
is the string `movielens-api-secret-dev-only`. These are seeded for local
development, are labelled as such in the files, and are not a finding.

What keeps them from becoming one is enforced in code rather than by
convention:

- `Settings.__init__` in `src/config.py` raises at construction time — before
  the app can accept a request — if `dev_auth_bypass` is set outside
  `environment == "dev"`, and again if the default model-server token or the
  default pgBouncer admin password is still in place outside `dev`.
- `docker-compose.prod.yml` names no `DEV_AUTH_BYPASS` variable at all: absent
  rather than `false`, so a typo during an incident cannot turn it back on. CI's
  `demo-compose` job renders the production model and fails if that string
  appears in it.
- Every credential in the production stack is a `${VAR:?...}` reference with no
  default, so a missing secret fails the Compose render rather than falling
  back to a known value. `infra/deploy/production.env.example` is the variable
  contract and carries `REPLACE_ME__` placeholders rather than secrets;
  `tests/unit/test_prod_compose.py` asserts it in both directions, so neither a
  forgotten variable nor a stale one survives.
- Production Keycloak realms are built from the templates in
  `infra/keycloak/realms/prod/` with generated secrets, and CI's `realm-drift`
  job compares what a Keycloak actually builds from the committed seeds against
  those templates, so production cannot quietly become the looser of the two.

A demonstration that one of those guards can be defeated — a way to boot a
non-`dev` environment with the bypass on, or to have a production stack accept
a development credential — is very much in scope.
