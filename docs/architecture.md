# Architecture

A multi-tenant, authenticated two-stage movie recommender, built on MovieLens
25M. A request arrives carrying a token; the tenant is derived from who signed
that token; a candidate generator retrieves from a precomputed index; a LightGBM
ranker scores those candidates against features read from Redis; the response
comes back with an explicit statement of which policy served it, and a durable
audit row is committed before the answer is sent. A Next.js product sits on top
of the same authenticated API any other client would use.

This document describes the engineering around the models — the isolation
boundary, the feature-freshness contract, the artifact pinning, the latency gate,
and the deployment — because that is what makes them usable by a real person.
The models are the project's main line of work: the two-stage stack is
documented in ADRs 0003–0006 and measured in [`results.md`](results.md), and the
first sequence model is already past its gates — SASRec
([ADR 0016](adr/0016-sasrec-sequential-retrieval.md)) is trained, clears the
retrieval and end-to-end gates offline, and the private sidecar can load it
(#161). It is measured, not promoted: the demo stack still serves the
item-item fixture, and the champion swap is deferred. The ladder of what
comes next, with its approval gate, is
[`modeling-roadmap.md`](modeling-roadmap.md).

**What is running right now: nothing.** The production target is specified,
built and rehearsed end to end — one Hetzner CX22 running `docker-compose.prod.yml`
behind its own Caddy edge, a deploy workflow that ships on a green CI run and
rolls back automatically on a failed verify — but the machine has not been
created, so there is no URL. As of the owner's 2026-10-05 brief, serving work
is parked behind the modeling track: the first deploy and the SASRec champion
swap both wait. Everything below is on `main` and runs locally through Docker
Compose. Where something is planned rather than built, this document says so in
the same sentence.

---

## The whole system

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/system-overview.dark.svg">
  <img alt="The full system: browser through the Next.js BFF to FastAPI's middleware chain, the recommendation coordinator, and the private model-server sidecar, with Postgres behind pgBouncer, Keycloak, Redis, and the offline lane that produces the serving bundle." src="diagrams/system-overview.svg" width="100%">
</picture>

Two paths run through it.

**The offline path** turns MovieLens into a serving bundle: ingest into Postgres,
a time-respecting split, point-in-time features into `feature_store.*`, a
candidate generator and a ranker, and a manifest that pins each artifact by
SHA-256. Bundles are committed to the repository and baked into an image. It
is run by hand today — there is no orchestrator on `main`.

**The online path** answers a request: request-id, auth, rate limit, audit, then
a coordinator that reads the user's durable movie state, decides between the
learned two-stage path and the popularity fallback, and calls a private sidecar
that owns retrieval, the online feature read and the ranker.

The split between the API and the sidecar is deliberate. The API image never
imports Feast, pandas or LightGBM; the sidecar never terminates a public
request, never holds a user token, and publishes no port. That keeps the image
that faces the internet small and keeps the model runtime's dependencies out of
the request-handling process.

---

## One request

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/online-request-path.dark.svg">
  <img alt="Sequence diagram of one authenticated recommendations request, from request-id adoption through token verification, tenant derivation, rate limiting, the state read, the learned or fallback branch, the audit insert, and the commit that precedes the response." src="diagrams/online-request-path.svg" width="100%">
</picture>

Four pieces of middleware wrap every request, outermost first
(`src/serving/app.py`):

1. **RequestId** adopts a well-formed inbound `X-Request-ID` — 1 to 128
   printable ASCII characters, space excluded so nothing can smuggle a newline
   into a log line — and mints a UUID otherwise. It echoes the value on every
   response, including a 401, which is why it sits outside auth.
2. **Auth** verifies the token, derives the tenant, opens the request
   transaction and sets `app.tenant_id` on it.
3. **RateLimit** applies a token bucket per `(tenant, subject)`. It is installed
   everywhere except `environment == "dev"`.
4. **Audit** is two middlewares, one per table. The prediction audit matches
   exactly one route shape — `GET /users/{id}/recommendations` — and writes the
   prediction log row. The request audit writes an operational row for every
   *other* authenticated route, on the same transaction; it skips that one route
   rather than duplicating a richer row into a second table, and it sits inside
   the limiter so a 429 writes nothing.

The coordinator reads positives and exclusions from `user_movie_state` in a
single `UNION ALL` round trip, written that way so each side can use its own
partial index. Positives are watched-and-not-dismissed titles, newest first,
because that is the order retrieval walks them in. Exclusions are dismissals
plus everything already seen.

If there are fewer than ten positive signals (ADR 0001's `COLD_START_THRESHOLD`,
amended to 10 on 2026-08-30), the request takes the popularity
fallback. Otherwise the coordinator asks the sidecar for
`max(100, limit × 10)` candidates over a 0.5-second timeout. The sidecar
retrieves through whichever retriever its bundle's family names — the generic
interface in `src/serving/sequence_retrieval.py`, with `ItemItemSidecarRetriever`
over the item-item index and `SASRecSidecarRetriever` over a SASRec encoder —
batch-reads eight features per candidate from Redis through Feast, scores with
the LightGBM booster for the request's route (learned or fallback, chosen from
the history size against the bundle's threshold), and returns the ranked list
with its own per-stage timings and attribution. The bundle loaded today in the
demo stack is the item-item fixture. A SASRec bundle loads fail-closed in the
same sidecar and its encoder was timed inside the `linux/amd64` image (#165),
but the authenticated k6 gate has not measured it and no committed tenant row
names it as champion.

**Exclusions are re-applied at every stage that could reintroduce a title** —
retrieval, the sidecar's ranking loop, the client's contract check, the
hydration SQL, and a final fail-closed sweep — because the alternative is
showing someone a movie they explicitly dismissed, and each of those layers has
a different reason to be stale.

The audit row is inserted on the same RLS-bound connection as everything else,
and **the transaction commits before the response is returned**. There is no
2xx for a mutation or an audit row that could still fail to become durable. A
commit failure discards the handler's response and answers 500.

### What the numbers are, and when they were measured

| Measurement | Result | Source |
|---|---|---|
| CI gate, accepted baseline 2026-08-20 | p50 6.31 ms, p95 14.27 ms, p99 41.30 ms, 54.08 req/s over 3 301 requests | [ADR 0010](adr/0010-synthetic-load-k6.md) |
| Production-topology rehearsal 2026-08-27 | p50 6.85 ms, p95 9.47 ms, p99 12.93 ms | [production-readiness-review.md](production-readiness-review.md) |
| SLO | p99 < 100 ms, zero errors, more than 50 requests/second | [ADR 0010](adr/0010-synthetic-load-k6.md) |

Two of the regressions that gate found are worth reading, because both looked
like a slow model and were neither:

- The four sidecar workers were each letting LightGBM's OpenMP and BLAS size a
  thread team to the whole host, so process parallelism multiplied by native
  parallelism into a periodic backlog: **p99 903.64 ms at 0% host CPU steal**.
  Pinning `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and
  `VECLIB_MAXIMUM_THREADS` to 1 brought the same unchanged gate back to
  **p99 48.99 ms**. No threshold, timeout or worker count moved.
- Because every request commits a durable audit row before it answers, one
  `fdatasync` sits inside the p99 — and a rented CI runner's block device was
  in that path, at **3.15 ms per commit**. Moving the job's Postgres data
  directory to tmpfs took it to **0.21 ms** and the gate's p99 from
  **230.74 ms to 24.41 ms**. Durability semantics did not change:
  `synchronous_commit` stays on and the commit still precedes the response.
  What a commit costs on a real disk is measured in production instead.

A breached window may be re-measured exactly once, and only when the host's own
CPU-steal record shows the runner was preempted. That is a measurement-validity
rule, not a relaxed threshold, and the thresholds have never moved.

### Which policy served it

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/serving-policy-decision.dark.svg">
  <img alt="Decision tree for the serving policy: no registered champion, cold start, a sidecar that refuses because its bundle is not the champion, model-server unavailable, empty or fully-excluded learned output, unseeded retrieval, and the learned two-stage path — with the policy and reason strings the code emits." src="diagrams/serving-policy-decision.svg" width="100%">
</picture>

Every recommendation response carries a `serving_policy` object: the policy
name, a `learned` boolean, the positive-signal count and the threshold it was
compared against, a structured reason, the score scale, the filter policy, and
the excluded count. The frontend labels the response from that flag rather than
inferring it, and the same values land in the audit row.

Before either branch is taken, the coordinator reads which model the tenant is
registered on — the three champion columns on `public.tenants` (migration 0016),
resolved through the tenant router's 30-second cache. A tenant with no champion
never reaches the sidecar and is answered from popularity under its own reason;
a champion that does not match the bundle the sidecar loaded is refused by the
sidecar with a coded 409 and audited as a mismatch rather than as an outage,
because a half-finished promotion and a dead process need different fixes.

The `unseeded-retrieval` case exists because of a bug worth keeping visible.
The exclusion set the coordinator sends the sidecar necessarily contains the
user's own watched titles, and retrieval was using it to filter the *seed* set
as well as the output — so every warm persona was in fact being served the
index's popularity fill, scored by LightGBM, while the response claimed
`learned: true` over zero seeds. Dismissals now travel on their own input as the
only signal that may drop a seed, `seed_count` reports the seeds retrieval
actually used rather than the ones offered, and a retrieval no seed reached
reports itself as `popularity-fill+lightgbm` with `learned: false` instead of
borrowing the learned label.

---

## Tenancy and auth

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/tenancy-and-auth.dark.svg">
  <img alt="Tenant isolation: Keycloak realms issuing tokens, the issuer-to-tenant derivation, the impersonation gate, the four Postgres identities, pgBouncer's transaction pool, and the eight forced-RLS tables against the deliberately shared ones." src="diagrams/tenancy-and-auth.svg" width="100%">
</picture>

**Auth** is self-hosted Keycloak with one realm per tenant
([ADR 0007](adr/0007-auth-provider-keycloak.md)). The tenant is the realm in the
token's `iss` claim, and the issuer is checked against the configured public
base URL before it is trusted — so the tenant comes from whoever signed the
token, never from a claim the client controls. Tokens must carry
`aud=movielens-api` and an `azp` in an explicit allow-list. Signing keys come
from a JWKS cache with a 300-second TTL that force-refreshes once on a key-id
miss, so a rotation does not need a restart. Every endpoint except `/healthz`
and `/readyz` requires a valid token; both exceptions serve no tenant or user
data and, in production, publish no port.

**Isolation** is Postgres row-level security
([ADR 0008](adr/0008-multi-tenancy-rls.md)). Eight tables carry `tenant_id` with
`FORCE ROW LEVEL SECURITY` and a policy of
`tenant_id = current_setting('app.tenant_id', true)` on both `USING` and
`WITH CHECK`. The auth middleware opens a transaction and issues
`SET LOCAL app.tenant_id` before any handler runs, so the database — not
application filtering — is the enforcer of last resort. A verified realm with no
row in `public.tenants` is refused with a 403: the token is fine, the tenant is
not registered.

Three things hold that up:

- **pgBouncer runs in transaction pool mode.** In session mode a `SET LOCAL`
  could outlive its request on a returned connection. `src/serving/startup_checks.py`
  opens the pooler's admin console at boot and refuses to start if the mode is
  anything else.
- **The serving role cannot bypass RLS.** The same startup check refuses to boot
  if the connected role holds `BYPASSRLS` or `SUPERUSER`. `app_user` is the only
  role that serves a request, and the only one that reaches Postgres through the
  pooler's `movielens_app` forced-user alias.
- **Two canaries.** `tests/tenant_isolation/` runs 23 cross-tenant assertions
  against the real Compose stack on every CI run; `synthetic/tenant_isolation/remote_canary.py`
  is the same idea as a deployable probe, which is why it lives under
  `synthetic/` rather than `tests/` — the API image ships one and not the other.

Persona impersonation is gated separately. Selecting a user other than your own
subject requires either the confidential service client or a `demo-impersonator`
realm role; `/whoami` is the only authenticated route without that gate.

**Rate limiting** ([ADR 0014](adr/0014-request-rate-limiting.md)) is a token
bucket keyed on `(tenant, subject)` from the verified token rather than on a
client address, because behind an edge every request comes from a proxy. The
bucket lives in Redis and is charged by one atomic Lua script
(`src/serving/ratelimit.py`), so every uvicorn worker meets the same bucket and
the configured limit describes the service: 600/minute with a burst of 120 by
default. If Redis is unreachable the limiter fails open onto a per-worker
in-process bucket and `/readyz` reports it. A tenant can carry its own quota in
`public.tenants.rate_limit_requests_per_minute` and `rate_limit_burst`; a NULL
column falls back to the global setting. The shared bucket replaced a per-worker
one that, at the first defaults of 120/minute and a burst of 30, refused
**37.9% of one subject's 301 canary requests** because keep-alive pinned a
client to one worker (ADR 0014's 2026-08-30 note).

---

## Features, artifacts, and why the bundle is baked

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/offline-training-to-serving.dark.svg">
  <img alt="The offline path: MovieLens 25M through ingest, the temporal split, point-in-time features, the candidate and ranker stages, the SHA-256-pinned schema 2 serving manifest, the two bundles baked at distinct paths, and how one reaches a running sidecar." src="diagrams/offline-training-to-serving.svg" width="100%">
</picture>

**The split respects time** ([ADR 0001](adr/0001-evaluation-protocol.md)). The
cutoff `T` is the 80th-percentile timestamp — 2016-06-25 — and train lands on
exactly 80.00% of rows. Holdout is the following 28 days: 129 683 interactions
across 2 641 users. Everything at or after `T + 28d` is test. Ties go to the
later slice. There are no random splits on this data anywhere.

The dataset itself is 25 000 095 ratings from 162 541 users over 59 047 rated
movies, at 0.2605% density ([`eda.md`](eda.md)).

**Features** are declared to Feast ([ADR 0009](adr/0009-feature-store-feast.md))
over three Postgres tables, with `tenant_id` as a join key on every view, and
materialized into Redis as tenant-keyed snapshots. The ranker consumes eight of
them in a fixed order that `src/feature_contract.py` owns, so training, the
manifest and the sidecar cannot disagree about column order without failing
loudly.

**Offline/online parity is tested, not assumed.** `tests/feature_parity/` runs
in CI against live Postgres and Redis and asserts the offline value equals the
online value for the same key. This is the bug class that quietly ruins most
recommender deployments, and the only way to know it has not happened is to
check.

**Serving artifacts are pinned by content.** A `ServingManifest`
(`src/models/artifacts.py`, schema 2) binds a retriever family — item-item or
SASRec — one LightGBM booster per route, the tenant, the ordered feature
contract and the bundle's lineage, with a SHA-256 for each artifact. A schema 1
manifest still loads and is normalised into that shape, because rollback is by
image and an older bundle must stay servable. Two bundles are **baked into the
sidecar image at build time** at distinct paths, along with the applied Feast
registry (`infra/features/Dockerfile`): the compact demo fixture from
`infra/model-bundle/` at `/app/models/serving`, and the full-data served bundle
from `infra/model-bundle-served/` at `/app/models/served-bundle`.
`MODEL_ARTIFACT_DIR` picks one. The demo stack names the fixture;
`docker-compose.prod.yml` names the served path as a literal. Today
`infra/model-bundle-served/` holds only a placeholder `.gitkeep` — no bundle has
been published into it with `make serving-artifacts-publish` — so a production
sidecar would refuse to boot rather than fall back to the fixture. Three
consequences follow, and all three are the point:

- Rolling back the model is rolling back the image. There is no second
  mechanism to get wrong at 02:00.
- The sidecar verifies every hash when it loads, then warms itself through a
  real retrieve → feature read → rank before it joins the accept loop, and its
  `/healthz` returns 503 until that finishes. Warmth is by construction rather
  than by luck, and a warm-up that produces an all-zero feature matrix is
  treated as a failure rather than a fast success.
- CI's `serving-artifacts` job rebuilds the demo fixture in the image and
  diffs it against the committed one, so a change to training that would
  silently move the artifacts fails the build instead. A served bundle is
  assembled rather than trained, so it gets a different check:
  `make serving-artifacts-verify` re-hashes every artifact and re-runs every
  manifest validator.

**The offline numbers are in [`results.md`](results.md)**, each with its run,
date and machine, and the grids, gate verdicts and benchmark outputs behind them
are committed under [`experiments/`](experiments/README.md). None are drawn on a
diagram.

---

## The data model

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/data-model.dark.svg">
  <img alt="Entity-relationship diagram of every table: the tenants registry, the MovieLens base tables, the shared catalog read model and the twelve TMDB snapshot tables, and the tenant-scoped movie state, feedback events, preferences, prediction audits and request audits, with forced-RLS tables marked." src="diagrams/data-model.svg" width="100%">
</picture>

Nineteen migrations, and the shape is worth a paragraph each for the tables
that carry the design.

**`user_movie_state`** is the current state of one user's relationship to one
movie: watched, rated, watchlisted, dismissed, plus a `state_version` the write
path uses for optimistic concurrency. Its CHECK constraints encode the semantics
rather than leaving them to application code — a rating implies a watch, and
watchlisted and dismissed are mutually exclusive. It was backfilled from
`ratings` without modifying a single imported row, because the MovieLens data is
a dataset and not a user's opinion.

**`user_feedback_events`** is the append-only log beside it, attributed to the
OIDC subject that made the change rather than to the persona it was made
against. Append-only is enforced by grant: neither runtime role has `UPDATE` or
`DELETE` on it.

**`recommendation_audits`** is the prediction log. Every recommendation writes
one row carrying the exact predictions and the feature values behind them, the
four per-stage latencies, the model, candidate, ranker and feature versions, the
policy and its structured reason, the input-state revision and hash, the
exclusion hash, the feature event time, and the correlation id. Migration
0019 (#168) added retrieval provenance: `retriever_family` (which family
answered), `retriever_sha256` (the manifest's digest for its primary artifact,
which is what makes a row replayable), `ranker_route` (which of the bundle's two
boosters scored it) and `encoder_ms` (time inside the sequence encoder alone,
0.0 for item-item). All four are nullable with no default, so rows older than
the columns read NULL rather than a claim nobody measured. `request_id` is
the row's own identity and `correlation_id` is the echoed `X-Request-ID`, kept
separate so a replayed header cannot collide with an existing row's primary key.

**`request_audits`** is the operational log for every other authenticated
route: tenant, actor subject, the persona the route addressed (null where it
addresses none), the matched route *template*, method, status, outcome, latency
and the same correlation id, which is the join key back to the prediction audit
when both exist for one call. It stores no request body and no query string, so
a viewer's search terms never reach it, and the template rather than the
concrete path so one operation cannot fan out into one `endpoint` value per
persona. Forced RLS and the same `SELECT`-plus-`INSERT` grant as the prediction
audit, so it is append-only from the request path's point of view.

The `feature_store.*` tables are outside RLS on purpose: `app_user` has no grant
on them at all, because online reads go through Redis and nothing serving a
request has any business reading the offline store. `movies`, `links`,
`movie_catalog_metadata`, the twelve `tmdb_*` tables and `public.tenants` are
shared by design — a movie catalog is not tenant data, and the tenant registry
is by definition the thing RLS is looking things up in. The `tmdb_*` tables
(migration 0018) hold the normalised 2026-09-05 TMDB snapshot
([`data/tmdb-metadata.md`](data/tmdb-metadata.md)); `app_user` reads them and
only `admin_user` writes. Six `tmdb_movies` columns — `vote_average`,
`vote_count`, `popularity`, `budget`, `revenue`, `status` — are as-of-pull
values with no observation timestamp, carry a column comment saying so, and are
kept out of the feature contract by `tests/unit/test_tmdb_leakage.py`.

---

## The frontend

The Next.js app is a real client against the same authenticated API as anything
else, and it is a portfolio surface rather than a product: it exists to make the
ML engineering visible. It is poster-first movie discovery
([frontend ADR 0002](adr/frontend/0002-movie-discovery-experience.md)) —
Discover, Browse, movie detail, Library and Quick Picks behind one shared shell
— with the ML evidence (the serving policy, the prediction audit, the online
feature values) behind progressive disclosure rather than on the page by
default. `/` serves that product to a signed-in viewer; the pre-redesign
dashboard survives at `/legacy` as a documented rollback until the finish gate
records a participant-backed pass.

The boundaries are the load-bearing part. The browser never holds an API token:
Auth.js runs the real authorization-code-plus-PKCE flow and keeps tokens in an
encrypted HttpOnly server session, and seventeen BFF route handlers are the only
things that talk to FastAPI. Behind them, `web/lib/resources/` is the one
server-owned client every product route reads through — per-resource timeout
budgets, `X-Request-ID` generation and echo, `private, no-store` on every
personalized response, and a caller-supplied bearer refused at the BFF edge
*and* again in the browser reader rather than silently dropped. The retained
`/legacy` dashboard's rating route predates that boundary and still calls the
API directly, which is one of the things retiring `/legacy` closes. Its eight-state model renders through one region
component, so a resource that fails never blanks the regions around it. Writes
go through exactly one path, `web/lib/movie-state/`: the transition table
written once, an idempotency key bound to the intent, `expected_revision` on
every request, a 409 that triggers a canonical re-read and one replay at that
revision, a 422 with `code: transition_refused` that earns the same re-read but
no replay — a rule about state, so being refused proves the control was stale,
and asking again would only ask the same rule — and a rollback that announces the
restore and walks focus back. That
consolidation was not tidiness — the previous copies had already diverged, and
only one of them turned a conflict into a correction rather than telling the
viewer to reload. The route map is in
[`frontend/frontend-system.md`](frontend/frontend-system.md).

---

## Delivery

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/ci-cd-pipeline.dark.svg">
  <img alt="CI and deployment: the twelve CI jobs and what each gates, the GHCR publish, the deploy gate that re-asserts every job by name, the release sequence on the box, and the automatic rollback." src="diagrams/ci-cd-pipeline.svg" width="100%">
</picture>

Twelve CI jobs. Beyond the usual lint, type-check and unit suite, the ones that
carry weight are `feature-parity` (offline equals online against live stores),
`tenant-isolation` (23 cross-tenant canaries on the real Compose stack),
`synthetic-load-smoke` (60 seconds of k6 at 55 arrivals per second against a
bypass-disabled service, with Postgres on tmpfs so the runner's disk is out of
the measurement, and evidence uploaded), `demo-compose` (every Compose model
resolves, the API image is under 400 MB, the rendered production model contains
no dev bypass), `browser-auth-e2e` (bypass-disabled browser journeys plus real
browser timing) and `serving-artifacts` (rebuild the bundle, diff it against the
committed one).

Only after all eleven are green does `publish-images` push seven images to GHCR
for `linux/amd64`, tagged with the commit SHA. The box never builds.

Deployment then re-checks that work rather than trusting it: the deploy
workflow's gate job **re-asserts each CI job by name on that SHA** — ten
required, and two path-gated jobs whose skip is accepted with a warning — before
it opens an SSH connection with a `known_hosts` pinned from a secret.
`infra/deploy/deploy.sh` records the current release as the previous one, pulls
at the SHA, runs the release jobs, brings the stack up and runs `make prod-verify`.
**If verification fails it redeploys the previous release and verifies again**,
and reports `ROLLBACK-OK` — while still failing the job, because that commit did
not ship.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/production-topology.dark.svg">
  <img alt="The production topology: one Hetzner CX22 with only the Caddy edge publishing ports and key-only SSH from GitHub Actions, ten long-lived services on a private network, the jobs profile, and the systemd units for boot, nightly backup and weekly prune." src="diagrams/production-topology.svg" width="100%">
</picture>

The target is one Hetzner CX22 ([ADR 0013](adr/0013-production-deployment-target.md)),
and cost is the deciding factor and says so: **≈€4.50/month against a measured
≈$27 on a PaaS** for the same ≈2.0 GB idle footprint. The single-host
consequences are stated rather than glossed — one failure domain, a deploy is a
brief outage, patching is the owner's, and off-box backups are the recovery
story.

Exactly two arrows cross the machine: 443/80 to the Caddy edge, which routes
precisely two public hostnames, and key-only SSH on 22 from GitHub Actions.
Everything else — both Postgres servers, Redis, pgBouncer, Keycloak, the API,
the sidecar, the feature server and the web app — lives on the host's private
Docker network and publishes nothing. `infra/host/bootstrap.sh` turns a stock
Ubuntu box into that host idempotently, and three systemd units run the stack at
boot, a nightly encrypted backup to a bucket at a different provider, and a
weekly image prune.

A scheduled canary runs `make prod-verify` every thirty minutes. It records p99
without a verdict, deliberately: k6 there would share two vCPUs with the service
it is measuring, so **the CI gate remains the SLO's only authority**. The canary
is a green no-op until a host is configured. The full sequence is in the
[deployment runbook](deployment-runbook.md).

---

## What is deliberately not built yet

This is scope, not apology. Each of these has a place in the plan and none of
them is drawn on a diagram as though it exists.

- **Per-tenant champion *routing*.** The registry can now express it: migration
  0016 put the champion-model coordinates, the rate-limit overrides and the A/B
  bucketing seed on `public.tenants`, the tenant router resolves all of them,
  the coordinator sends the champion with every rank call, and the sidecar
  refuses a bundle that is not the one the tenant is registered on. What does
  not exist is the routing layer that would make use of a *second* value: the
  sidecar still loads exactly one bundle for one tenant (`MODEL_TENANT_ID`), so
  a second serving tenant, or a challenger beside a champion, is still a second
  process. Splitting traffic between them — and the shadow path that logs a
  challenger's predictions without shipping them — is Phase 6's work.
- **Orchestration.** The offline path is a set of entrypoints run by hand.
  The evaluation gates exist as commands — `make gate` (ADR 0001's NDCG@10
  gate) and `make gate-retrieval` (ADR 0004's recall@500 gate) — and
  `make promote` / `make promote-revert` (`src/release/promote.py`, #175/#177)
  is the manual champion repoint: it verifies a bundle, snapshots the tenant's
  current champion columns, then moves them. Nothing calls one from the other.
  Prefect flows, a gate wired into promotion, and idempotent retraining are
  Phase 4.
- **Drift detection.** Evidently, the feature-distribution dashboards, and the
  synthetic drift cohort that proves an alert fires are Phase 5.
- **A `/metrics` endpoint.** Prometheus and Grafana are in the dev stack and are
  deliberately absent from production; nothing exposes a scrape target, and
  ADR 0013 records that as a decision rather than an omission. The production
  health signal today is the verify matrix and the audit-table SLI it prints,
  plus an external uptime check that is still owed.
- **Structured JSON logging.** The convention is written down; the serving path
  does not yet emit it.
- **A staging *host*.** The environments themselves are done: dev is
  `docker-compose.yml` + `docker-compose.demo.yml` behind `make up-dev` (there
  is deliberately no third file), and staging is `docker-compose.staging.yml`, a
  thin overlay on the production stack behind `make up-staging`. Neither is
  deployed anywhere — staging runs on a laptop with Caddy's own CA, has no
  deploy workflow and no canary, and pointing it at a second box is two
  hostnames and `EDGE_TLS=acme` away.
- **Audit retention, and a tenant-wide audit view.** Every authenticated request
  now writes a durable row — `recommendation_audits` for the recommendation
  route, `request_audits` for everything else — but nothing prunes either table,
  and the only API read is persona-scoped
  (`GET /users/{user_id}/request-audits`). A tenant-wide operator view belongs
  to Phase 5's Grafana rather than to a second endpoint here, and the retention
  question sits with the same one `feature_store.*` has.
- **A cold-start cohort for sequence models.** The
  [ADR 0011](adr/0011-cold-start-coverage.md) cohort is built
  (`synthetic/cold_start/`, migration 0015) and every trainer logs its
  per-bucket recall ([`results.md`](results.md#cold-start-coverage-adr-0011)).
  Its h10 bucket stamps all ten events at one timestamp, which is valid for
  routing and for order-insensitive retrievers but defines no sequence, so a
  SASRec h10 number is an out-of-distribution probe rather than a measure of
  sequential quality. A separate cohort with strictly increasing timestamps is
  owed before one is claimed.
- **Feast-backed training rows.** Training still builds features with the
  point-in-time `FeatureIndex` rather than Feast's historical retrieval. What
  is proven is the boundary: feature-parity CI checks all eight values agree
  across Python, Feast historical and Feast online at a materialization
  timestamp. Which source training should use is deferred by the owner as
  D-009 (`model-planning/memos/feature-source-boundary.md`). Training-time
  exclusions are no longer a gap: since #126 the ranker's negative pool drops
  each user's history before the target timestamp, as serving does.
- **Serving the models the modeling track has produced.** Parked as of
  2026-10-05: the served bundle is unpublished, the first deploy has not run,
  and the SASRec champion swap waits behind the modeling brief.

---

## Where to read next

- [`adr/README.md`](adr/README.md) — every decision, with its alternatives and
  the signals that would reopen it. Twenty backend ADRs and two frontend ones.
- [`deployment-runbook.md`](deployment-runbook.md) — the machine, DNS, host
  bootstrap, secrets, the one-time SQL, the first deploy, verify, rollback,
  backups and the restore drill.
- [`production-readiness-review.md`](production-readiness-review.md) — the
  pre-deployment gap review and the 14-step rehearsal record, including the
  defects the first non-dev boot of this codebase found.
- [`demo-runbook.md`](demo-runbook.md) — running the whole stack from a clean
  checkout.
- [`api/README.md`](api/README.md) — the generated OpenAPI contract for the
  authenticated surface.
- [`diagrams/README.md`](diagrams/README.md) — how the diagrams above are
  produced, and the rule that the code settles any disagreement with them.
