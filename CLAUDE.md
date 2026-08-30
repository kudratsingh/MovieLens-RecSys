# MovieLens Two-Stage Recommender — CLAUDE.md

## Purpose of this project

This project started as a portfolio-grade applied ML build and, **as of 2026-06-02, has been re-scoped to target enterprise-grade**. Both framings still apply:

- **Original framing (still load-bearing).** Confront the technologies and scenarios a mid-to-senior ML engineer actually deals with at an enterprise — the engineering around the model as much as the model itself. ML counterpart to my Incident & Workflow Platform project — same philosophy, same trick of building something substantial enough that production concerns force themselves on you end-to-end.
- **Expanded framing (2026-06-02 scope shift).** The system itself should meet enterprise standards on **real auth, multi-tenancy, observability, and synthetic-load realism** — not just look enterprisey in writeups. The Phase 3+ work that previously assumed "internal-only, no real auth" is replaced with the real shape: an authenticated, multi-tenant API with synthetic-traffic harnesses that exercise the latency SLO and cold-start path under load. See the Phase 3 section for the expanded scope and the "What the system does" section for the updated system description.
- **Modeling framing (2026-08-29).** The main goal is the models. Keep building recommenders that track what the industry ships today: the two-stage architecture now (item-item and two-tower retrieval, a LightGBM ranker), sequence models with transformer encoders (the SASRec / BERT4Rec family) next, and whatever follows them — each choice with its own ADR, each scored through the one evaluation harness. The harness — auth, tenancy, the feature store, the latency gate, the product — is being finished **now** so those models are usable by a real user, and that is what Phase 3 is about. Once Phase 3 closes, the remaining phases are re-prioritized by urgency, and the modeling track resumes as the primary line of work. Anything in this file or the public docs that reads as "the engineering is the point, the modeling is incidental" is out of date. The ladder itself, with its approval gate and decision log, is `docs/modeling-roadmap.md`.

The output I care about: I should be able to defend every architectural choice in a senior-level design review, debug any layer when it breaks, and articulate the tradeoffs vs. alternatives — *and* I should be able to hand the running system to an enterprise SRE without needing to apologize for what's missing.

## What the system does

A **multi-tenant, authenticated** two-stage movie recommender service:

- An authenticated request from a tenant arrives with a user identifier scoped to that tenant.
- A **candidate generator** retrieves ~500 candidates from a precomputed index (item-item similarity or two-tower embeddings).
- A **ranker** (LightGBM) scores those candidates using features pulled from a feature store.
- The service returns top-K recommendations in <100ms (p99 SLO), with the tenant's currently-promoted champion model serving the request.

Around the online path: an offline training pipeline orchestrated by Prefect, a model registry with a promotion gate, monitoring for system and model metrics, drift detection, and an A/B / shadow-deploy framework. Champion-vs-challenger routing is **tenant-aware** — different tenants can be on different model versions.

**Multi-tenancy.** A tenant is a logical isolation boundary. Cross-tenant data leakage is the highest-severity bug class (see non-negotiables). The isolation mechanism is Postgres row-level security (ADR 0008): every tenant-scoped table carries a `tenant_id`, RLS is forced, and the auth middleware sets `app.tenant_id` inside a per-request transaction so the database — not application filtering — is the enforcer of last resort. Champion-model assignment, API keys, rate limits, and audit logs are all scoped per-tenant.

**Auth.** Every API endpoint except `/healthz` requires a valid token. The provider is self-hosted Keycloak with one realm per tenant (ADR 0007); the tenant is derived from the token's issuer, never from a client-declared claim. There is no unauthenticated production path.

**Synthetic users.** A synthetic-user harness lives in `synthetic/` and serves narrow jobs, not one-size-fits-all generation:
- **Load testing** (k6, ADR 0010) — verify the p99 < 100ms SLO under realistic concurrency, against real Keycloak-issued tokens.
- **Cold-start coverage** — programmatically generated new-user states (history sizes 0, 1, 3, 10) to stress the cold-start fallback path beyond what MovieLens's natural distribution provides.
- **Drift simulation** (Phase 5) — synthetic users with shifting taste distributions to verify Evidently alerts fire.
- **A/B bucketing fixtures** (Phase 6) — deterministic tenant + user combinations for champion-vs-challenger tests.
- **Demo personas** — handcrafted users for portfolio walkthroughs of the frontend.

A **Next.js frontend** consumes the API. It is not an end-user product — it's a portfolio surface that makes the ML-engineering work visible. It is a poster-first movie-discovery product (frontend ADR 0002): Discover, Browse, movie detail, Library, and Quick Picks behind one shared shell, with the ML evidence — serving policy, prediction audit, online feature values — behind progressive disclosure rather than on the page by default. `/` serves that product to a signed-in viewer; the pre-redesign dashboard is retained at `/legacy` as a documented one-file rollback until the finish gate records a participant-backed PASS. The frontend authenticates via the same auth provider as any other client, and demo-persona impersonation is gated behind an explicit `demo-impersonator` realm role (ADR 0012) or the dev-only bypass, never reachable from production deployments. A browsable catalog is in scope; full admin dashboards remain an explicit non-goal (Grafana owns admin/operator views). The surfaces that expose later phases' work arrive with those phases — feature attribution and a model/version selector in Phase 4, a drift indicator in Phase 5, the champion-vs-challenger comparison view in Phase 6.

## Dataset

MovieLens 25M (move to 32M if needed). Real ratings, real timestamps, real cold-start, well-documented. Loaded into Postgres as the source of truth.

Starting in Phase 3, a **synthetic-user augmentation layer** sits alongside MovieLens — programmatically generated user identities the system treats as real but which are tagged `synthetic=true` in the data layer. Synthetic users live in their own tenant(s) for clean isolation; real MovieLens users live in the default tenant. The augmentation never modifies the MovieLens raw data; it adds rows in user-scoped tables that the training pipeline can optionally exclude via filter. See "What the system does" for the jobs synthetic users serve.

## Architecture

**Offline path:**
raw data + synthetic data → feature engineering → feature store (offline) → training pipeline → model registry → evaluation gate → promotion (per-tenant)

**Online path:**
authenticated request → auth middleware (resolves tenant + user) → tenant router (selects champion model version for that tenant) → candidate generator → feature store (online, Redis-backed, tenant-scoped) → ranker → top-K → response (+ structured logging of features, predictions, tenant, latency)

**Surrounding systems:**
- Prefect orchestrates retraining DAGs
- MLflow tracks experiments and hosts the model registry (model versions tagged with tenant compatibility)
- Prometheus + Grafana for system metrics (per-tenant latency, error rate, throughput); Evidently for drift (per-tenant or aggregate, depending on signal volume)
- A/B routing layer for champion/challenger and shadow deploys — tenant-aware
- GitHub Actions for CI/CD including model tests, feature parity, and synthetic-load smoke tests
- Next.js frontend as the demo/portfolio surface against the API

**Frontend path:**
browser → Next.js app → auth → FastAPI (recommendations, features, model metadata) → response renderer. Movie posters are fetched from TMDB, keyed via MovieLens `links.csv` (`movieId` → `tmdbId`); the TMDB call is proxied through the FastAPI backend so the API key stays server-side.

## Stack (locked in)

| Layer | Choice | Why |
|---|---|---|
| Models | PyTorch (two-tower), LightGBM (ranker) | Industry standard for both stages |
| Data store | Postgres | Already fluent; sufficient |
| Data versioning | DVC | Reproducibility |
| Feature store | Feast | The de facto open-source feature store |
| Tracking + registry | MLflow | Industry standard, integrates everywhere |
| Orchestration | Prefect | Modern, gentler than Airflow; revisit Airflow later |
| Serving | FastAPI + Redis | Already fluent from Incident Platform |
| Auth provider | Keycloak OIDC, realm per tenant (ADR 0007) | Local-first real auth with standard JWT validation |
| Multi-tenancy isolation | PostgreSQL row-level security (ADR 0008) | Tenant isolation remains enforced even when a query omits a filter |
| Synthetic load testing | k6 (ADR 0010) | Latency SLO must be measured, not assumed |
| Frontend | Next.js + TypeScript + Tailwind | Real client against the API; makes ML-engineering work visible as a portfolio surface |
| Monitoring | Prometheus + Grafana + Evidently | System + ML-specific signals |
| Containers | Docker, docker-compose | Already fluent |
| CI/CD | GitHub Actions | Already fluent |
| Production host | One Hetzner CX22 running `docker-compose.prod.yml` (ADR 0013) | The Compose file is already the deployment artifact; ≈€4.50/month against a measured ≈$27 on a PaaS |
| Image registry | GHCR, `linux/amd64`, tagged with the commit SHA | The box never builds; deploy and rollback are the same mechanism |
| Orchestration runtime | Local first; k3s/kind optional after Phase 4 | Don't let K8s block progress |

## Phased plan

Each phase earns a specific set of mid-level muscles. Don't skip ahead — the lessons compound.

The modeling track has its own ladder in [`docs/modeling-roadmap.md`](docs/modeling-roadmap.md) — two-tower v2, SASRec, a sequence-aware ranker, multi-objective ranking, mixing and re-ranking, bandits, and the generative/foundation end state. **Every rung needs my approval before work starts** (an ADR proposal, then an *approved* row in the roadmap's decision log), rungs can be skipped with a recorded reason, and the champion changes only through ADR 0001's gate.

The execution sequence and definition of done for the working Phase 3 demo live
in [`docs/records/demo-plan.md`](docs/records/demo-plan.md). This file remains authoritative for
architecture and phase scope; the demo plan tracks the vertical-slice milestone.

### Phase 1 — Baseline and data foundation
- Load MovieLens into Postgres
- Exploratory data analysis (sanity checks, distributions, sparsity)
- Build a popularity baseline
- Build a collaborative filtering baseline (matrix factorization via implicit or LightFM)
- Set up MLflow tracking from day one
- Set up DVC for dataset versioning
- **Time-respecting splits** (train on past, validate on future — no random splits on temporal data)

**Lessons:** experiment tracking, data versioning, why baselines matter, temporal data splits.

### Phase 2 — Two-stage architecture (offline)
- Candidate generator: item-item similarity, then upgrade to two-tower embeddings
- Ranker: LightGBM with engineered features (user history aggregates, item popularity windows, genre affinities, recency features)
- Everything still offline, structured as the real architecture
- Offline metrics: recall@k for candidates, NDCG/MAP for ranker

**Lessons:** two-stage design, feature engineering at scale, stage-specific metrics, why one model can't do both jobs well.

### Phase 3 — Feature store, serving, auth, multi-tenancy, synthetic-load harness

This is the phase that most heavily absorbs the 2026-06-02 enterprise-scope shift. The original Phase 3 work (Feast, FastAPI, Redis) lands here, *and* so do real auth, multi-tenancy, and the synthetic-user harness. This phase is correspondingly larger than the original plan; resist compressing it.

**Feature store and serving (original Phase 3 scope):**
- Introduce Feast
- Define feature views; materialize offline features for training
- Set up online features in Redis (tenant-scoped key prefixes from day one)
- Build the FastAPI service end-to-end
- Containerize the full stack
- **Feature parity test in CI** (offline-computed feature matches online-served feature for same key) — non-negotiable
- Bootstrap the Next.js + TypeScript + Tailwind app alongside the API (already partially done — see PR #20)
- Frontend surface (Phase 3 baseline): user selector → top-K poster grid + watch history view. Superseded as the end state by the movie-discovery redesign (frontend ADR 0002, cross-cutting ADR 0012, `docs/frontend/`), which is what `/` now serves; the baseline dashboard survives only at `/legacy` as the documented rollback
- TMDB integration via MovieLens `links.csv` → `tmdbId`; the API key lives server-side, proxied through FastAPI

**Real auth (new):**
- ADR 0007 — auth provider choice (Auth0 vs Keycloak self-hosted vs Postgres-backed JWT). Decided: Keycloak, realm per tenant. Decision turned on: ease of multi-tenancy mapping, ability to rotate keys cleanly, local-dev story, and how much of the work is reusable when Phase 6's A/B routing layer comes online
- Auth middleware on FastAPI — every endpoint except `/healthz` requires a valid token
- Token claims include tenant id; downstream code never sees a request without a resolved tenant
- Frontend authenticates via the same provider; a dev/portfolio impersonation mode is gated behind an explicit flag and *never* enabled in production builds
- Audit log table — every authenticated request emits a row (`tenant_id`, `user_id`, `endpoint`, `model_version`, `latency_ms`, `outcome`)

**Multi-tenancy (new):**
- ADR 0008 — isolation mechanism (Postgres schema-per-tenant vs row-level security vs FastAPI-instance-per-tenant). Decided: row-level security, with pgBouncer in transaction-pool mode so `SET LOCAL` can't leak across requests. Decision turned on: query complexity, blast radius of a bug, operational overhead, and whether tenants share a model registry or have their own
- Tenant router in `src/serving/tenancy/` — resolves `tenant_id` (from auth claim) to (a) the champion model version for that tenant, (b) the Redis key prefix for online features, (c) the per-tenant rate limit
- Tenant configuration in Postgres — one row per tenant, columns include API quotas, current champion model versions per stage, A/B bucketing seed
- Cross-tenant leakage test in CI — synthetic-data integration test that authenticates as tenant A, fires every endpoint, and asserts no response payload contains tenant B's data

**Synthetic-user harness (new, primary scope):**
- `synthetic/load/` — k6 scripts (ADR 0010) that drive realistic concurrent traffic against the API and produce p99/p95/p50 reports. CI runs a small-scale version on every PR that touches `src/serving/`; nightly runs a larger one
- `synthetic/cold_start/` — generator for programmatically created user profiles with controlled history sizes (0, 1, 3, 10 interactions) across the genre distribution. Output flows into the eval harness as an additional slice so cold-start recall has its own metric line in MLflow
- `synthetic/personas/` — handcrafted demo users for portfolio walkthroughs (action fan, drama fan, eclectic, etc.). Loaded into a `demo` tenant
- (Deferred to later phases) `synthetic/drift/` for Phase 5, `synthetic/ab_fixtures/` for Phase 6

**Multi-environment infra (new):**
- `docker-compose.dev.yml`, `docker-compose.staging.yml`, `docker-compose.prod.yml` — distinct compose stacks per environment, with environment-specific configs (smaller dataset snapshot in dev, full in staging/prod; auth-bypass disabled in everything except dev)
- A Makefile target per environment (`make up-dev`, `make up-staging`)
- Environment-aware `Settings` in `src/config.py` — runtime asserts that production builds never have dev flags set

**ADRs that gate Phase 3 work (each in its own bundled PR with the code it justifies):**
- ADR 0006 — Two-tower retrieval architecture (history-based encoder, sampled softmax, FAISS) — *bundled with Phase 2's two-tower PR* (Phase 2 step #4, sits at the Phase 2/3 boundary because it pins FAISS as the ANN library that Phase 3 serving will inherit)
- ADR 0007 — auth provider choice (Auth0 vs Keycloak vs Postgres-backed JWT) → Keycloak, realm per tenant
- ADR 0008 — multi-tenancy isolation mechanism (Postgres schema vs row-level security vs FastAPI-instance-per-tenant) → row-level security
- ADR 0009 — Feast vs alternatives (custom Postgres views, hand-rolled key-value loader) → Feast, Postgres offline + Redis online
- ADR 0010 — synthetic-load tool (k6 vs Locust) → k6, metrics via Prometheus remote-write
- ADR 0011 — cold-start coverage methodology (how synthetic cold users are generated and what they prove) → fixed-seed cohort, history buckets {0, 1, 3, 10}

All five landed as docs-only PRs (#27–#31) ahead of the code that consumes them (#32 onward). ADRs are namespaced — backend ADRs use the flat top-level numeric line at `docs/adr/`, frontend ADRs use their own line under `docs/adr/frontend/`.

**Lessons:** online/offline skew, feature freshness, feature store as source of truth, latency budgets per stage, designing an API against a real client (not a hypothetical one), the operational shape of multi-tenant ML serving, what auth touches inside an ML service (audit logs, model-version routing per tenant, key rotation), why synthetic load is not optional.

### Phase 4 — Orchestration and retraining
- Prefect DAGs for: feature materialization, training, evaluation, registry promotion
- **Evaluation gate:** a new model only gets promoted if it beats the current champion on holdout metrics by a defined threshold
- Idempotent pipelines
- Frontend surface: "why this recommendation?" panel — top contributing features per item from LightGBM, plus a model/version selector for debugging

**Lessons:** workflow orchestration, model promotion logic, what production retraining actually means, exposing explainability through the API.

### Phase 5 — Monitoring and drift
- Prometheus + Grafana for system metrics (latency, throughput, error rate) — sliced by tenant
- Evidently for data drift and prediction drift — per-tenant if signal volume supports it, otherwise aggregate with a per-tenant breakdown dashboard
- Log features and predictions to a table; dashboard feature distributions over time
- **Simulate drift** via `synthetic/drift/` generator (extends the Phase 3 harness) — programmatically shift the taste distribution of a synthetic-user cohort and verify Evidently alerts fire within a defined window
- Frontend surface: lightweight drift indicator on the recs page (e.g. "model health: ok / degraded"). Real monitoring stays in Grafana; this is just a visible signal that the system *has* drift detection.

**Lessons:** what to monitor for ML systems specifically, drift vs. performance degradation, the alerting feedback loop, per-tenant monitoring at low signal volume.

### Phase 6 — A/B testing and shadow deploys
- Tenant-aware routing layer to split traffic between champion and challenger (a tenant can be 100% champion, 100% challenger, or split — controlled via the tenant config row added in Phase 3)
- Shadow mode (challenger sees traffic; predictions logged but not shipped)
- Offline analysis comparing champion vs. challenger
- Significance testing for online experiments
- `synthetic/ab_fixtures/` — deterministic synthetic tenant + user combinations used in CI integration tests to verify the bucketing math
- Frontend surface (the centerpiece): champion vs. challenger side-by-side view — same user, two columns of top-K recs, with diff highlighting and an experiment-summary panel

**Lessons:** champion/challenger, shadow deploys, statistical significance, why offline NDCG and online CTR don't match, the operational complexity of per-tenant A/B at scale.

## Non-negotiables (what makes this not a toy)

These are the things I'll hold the project to. Every one of them maps to a real production concern.

1. **Time-respecting splits.** No random splits on temporal data. Ever.
2. **Feature parity test in CI.** A test that proves a feature computed offline matches the same feature served online for the same user/item. This catches the single bug that ruins most real recsys deployments.
3. **Cold-start handling.** Explicit answers for new users (no history) and new movies (no interactions). The synthetic cold-start harness (Phase 3) makes this measurable, not assumed.
4. **Latency SLO.** p99 < 100ms. **Measured under synthetic load**, not assumed. The Phase 3 synthetic-load harness runs in CI on every serving PR. A breached window is re-measured exactly once, and only when the host's own CPU-steal record shows the runner was preempted — a measurement-validity rule, never a relaxed threshold (ADR 0010). The measurement is of the service on a runner whose storage has been taken out of it: every request commits a durable audit row before it answers, so the CI gate runs Postgres's data directory on tmpfs (ADR 0010's 2026-08-28 note) rather than let a rented VM's disk sit inside the p99. Durability semantics are unchanged — `synchronous_commit` stays on and the commit still precedes the response. What a commit costs on a real deployment is measured in production, by the `recommendation_audits` SLI `make prod-verify` prints and the canary that runs it.
5. **Reproducibility test.** `make train` on a fixed seed and dataset version produces the same model artifact hash. If it doesn't, something is nondeterministic — find it.
6. **ADRs (architecture decision records).** A `docs/adr/` folder explaining *why* I chose each major piece. Two namespaces: backend ADRs at the top level (flat numeric line), frontend ADRs under `docs/adr/frontend/`. One ADR per significant decision; ADRs are substantive (closer to 150 lines than 50) and explore alternatives, consequences, and "how we'd know we're wrong" rather than reading like checkboxes.
7. **Evaluation gate before promotion.** A model never goes to production without beating the incumbent on a holdout — automated, not eyeballed. Per-tenant gates are scoped per tenant.
8. **Logged predictions and features.** Every online prediction logged with the features used, the tenant, the model version, and the latency, so we can replay, debug, compute online metrics, and audit per-tenant behavior later.
9. **Tenant isolation.** No code path may return one tenant's data in response to another tenant's request. Cross-tenant leakage is the highest-severity bug class. An automated CI integration test exercises every endpoint as tenant A and asserts no tenant B data surfaces.
10. **Auth on every endpoint except `/healthz`.** No "internal" unauthenticated paths. Dev-mode bypass exists for local development only and is asserted off in staging/prod builds.
11. **Synthetic-load smoke test in CI.** Every PR that touches `src/serving/` runs a short synthetic-load script and fails if p99 exceeds the SLO threshold on a defined baseline workload. The gate has to measure the service and not the runner: it quiesces what it does not measure, promotes what it does, warms every worker before the window opens, fails warm traffic that quietly degrades to the popularity fallback, and — since the audit commit puts one `fdatasync` inside every request — runs the job's Postgres data directory on tmpfs so the runner's block device is out of the measurement (`docker-compose.ci-load.yml`, applied by that job alone via `DEMO_COMPOSE_EXTRA`). The evidence directory says which medium it measured and classifies a stalled device informationally; neither that classification nor the tmpfs mount touches a threshold or the steal re-measure rule. The thresholds themselves have never moved.

## Repo structure (target)

```
movielens-recsys/
├── CLAUDE.md                  # this file
├── README.md                  # the public front door: status, quickstart, measured numbers, ADR index
├── Makefile                   # train, serving-artifacts*, serve, test, lint, db-migrate, demo-*, prod-*,
│                              #   api-contract*, web-api-types* targets
├── scripts/                   # generate_openapi.py — committed OpenAPI contract + CI drift check
├── alembic.ini
├── alembic/                   # Phase 3 — tenant roles, tenants registry, tenant_id + forced RLS, personas, feature tables, audits
├── docker-compose.yml         # default dev stack: postgres, redis, mlflow, prometheus, grafana, pgbouncer, keycloak
├── docker-compose.demo.yml    # layered on the default stack: api, web, feature-server, model-server, k6 (load profile)
├── docker-compose.ci-load.yml # CI load job only (DEMO_COMPOSE_EXTRA): postgres data directory on tmpfs,
│                              #   so the runner's disk is out of the latency measurement (ADR 0010)
├── docker-compose.staging.yml # planned — Phase 3 multi-environment infra
├── docker-compose.prod.yml    # THE production stack, and its own local rehearsal: generated secrets,
│                              #   ENVIRONMENT=production, no published data-store ports, Caddy edge
│                              #   terminating https; GHCR images on the box, build contexts on a laptop
├── .github/workflows/         # CI: lint, unit, feature parity, tenant isolation, synthetic-load smoke, serving
│                              #   artifacts, realm drift, frontend, compose validation, GHCR image publishing;
│                              #   plus the SSH deploy workflow and the scheduled production canary
├── docs/
│   ├── README.md              # the docs landing page: reading path for a visitor, map by subject
│   ├── status/                # the project ledger: per-phase detail + the long-form current step (moved out of this file)
│   ├── architecture.md        # public architecture overview (offline + online paths)
│   ├── diagrams/              # mermaid sources + rendered light/dark SVGs (`make diagrams`)
│   ├── assets/                # social-preview.png and the script that renders it
│   ├── adr/                   # backend ADRs (flat numeric line) + cross-cutting
│   │   └── frontend/          # frontend ADRs (own numeric line)
│   ├── api/                   # generated openapi.json (do not hand-edit) + regeneration notes,
│   │                          #   plus overview.md — every path/method, auth, headers, worked response
│   ├── assets/                # social preview + eda/ — the figures docs/eda.md embeds (`make eda`)
│   ├── frontend/              # movie-discovery product docs: design contracts, frontend system,
│   │   │                      #   implementation plan, readiness, surface contracts (catalog, library
│   │   │                      #   feedback, seen), testing strategy, finish-gate review
│   │   ├── evidence/          # per-bundle + per-surface screenshot matrices with per-file provenance;
│   │   │                      #   README.md indexes all 13 sets and says which describe the current build
│   │   └── records/           # not maintained: product discovery, bundles 5–7 handoff, baseline
│   │                          #   evidence, and the four dated finish-gate passes verbatim
│   ├── records/               # not maintained: demo plan, MVP/deployment handoff, serving-fix handoff
│   ├── eda.md
│   ├── results.md             # the measured offline numbers — baselines, candidate stage, ranker,
│   │                          #   ADR 0011 cold-start coverage; each carries its run, date and machine
│   ├── demo-runbook.md        # clean-checkout demo startup, seeding, smoke, reset, troubleshooting
│   ├── deployment-runbook.md  # production: the machine, DNS, host bootstrap, secrets, one-time SQL, the
│   │                          #   first deploy, verify, rollback, backups + restore drill, housekeeping
│   └── production-readiness-review.md  # the pre-deployment gap review and the 14-step rehearsal record
├── data/                      # DVC-tracked
├── notebooks/                 # EDA script (`make eda`); metrics still go through src/evaluation/
├── src/
│   ├── config.py              # pydantic-settings; refuses to construct with dev-only flags outside dev
│   ├── feature_contract.py    # ordered ranker feature columns shared by training, the manifest, and the sidecar
│   ├── data/                  # ingestion, splits, schemas, demo schema bootstrap
│   ├── features/              # point-in-time feature module, Feast repo (feast_repo/), materialization, online reads
│   ├── models/
│   │   ├── artifacts.py       # SHA-256-pinned serving manifest + deterministic item-item index
│   │   ├── candidates/        # popularity, CF/ALS, item-item, two-tower
│   │   └── ranker/            # LightGBM LambdaRank
│   ├── training/              # offline training entrypoints + demo artifact packaging
│   ├── evaluation/            # offline metrics, evaluation gate
│   ├── auth/                  # Phase 3 — JWKS cache, auth middleware, tenant-scoped request transaction
│   ├── serving/
│   │   ├── app.py             # FastAPI entrypoint
│   │   ├── tenancy/           # Phase 3 — tenant router, per-tenant config resolution
│   │   ├── audit.py           # Phase 3 — RLS-scoped prediction audit writer
│   │   ├── model_server.py    # Phase 3 — private Feast + LightGBM sidecar
│   │   ├── policy.py          # Phase 3 — serving-policy, exclusion-filter, and audit-digest vocabulary
│   │   ├── request_id.py      # Phase 3 — X-Request-ID adoption and echo on every response
│   │   ├── routing/           # Phase 6 — champion/challenger split, shadow routing
│   │   └── ...                # recommendations, orchestration, online features, TMDB proxy, startup checks
│   ├── release/               # Phase 3 — production bootstrap (preflight, schema, seed, materialize) and the
│   │                          #   post-deploy verify matrix; the only place a migrator DSN is used
│   └── monitoring/            # Phase 5 — drift, dashboards
├── pipelines/                 # Phase 4 — Prefect flows
├── synthetic/                 # Phase 3+ — synthetic-user harnesses (scoped per job)
│   ├── README.md              # what each harness proves and the Make targets that run it
│   ├── load/                  # k6 (ADR 0010): recommendations.js + thresholds.js (the pinned p99 gate),
│   │                          #   pages.js + page_thresholds.js (page-shaped per-step budgets),
│   │                          #   run_gate.sh (run, decide, re-measure at most once), summarize.py,
│   │                          #   probe_host_cpu.py, reliability.py (non-latency serving promises)
│   ├── personas/              # handcrafted demo users, catalog manifest, idempotent seeder
│   ├── smoke/                 # demo readiness + behavioral smoke checks
│   ├── tenant_isolation/      # remote_canary.py — the deployed cross-tenant probe; here rather than
│   │                          #   under tests/ because the API image ships synthetic/ and never tests/
│   ├── cold_start/            # fixed-seed cold-start cohort (ADR 0011): config, generator + CLI,
│   │                          #   loader, and the trainer-side per-bucket metric glue
│   ├── drift/                 # Phase 5
│   └── ab_fixtures/           # Phase 6
├── web/                       # Next.js + TS + Tailwind frontend
│   ├── app/                   # App Router routes: / (front door), /discover, /browse, /movies/[movieId],
│   │                          #   /library, /quick-picks, /legacy, /ui-preview, + the BFF route handlers
│   ├── components/            # incl. movie/ — the one movie-state control family
│   ├── lib/                   # resources/ (the one server-owned client), movie-state/ (the one write path),
│   │                          #   api.generated.ts (typed from docs/api/openapi.json)
│   ├── e2e/                   # fixture-mode Playwright: responsive, accessibility, finish-gate matrices
│   ├── tests/                 # unit/ (Vitest), e2e/ (service-backed journeys), perf/ (browser LCP/CLS/ack)
│   ├── scripts/               # check-api-types.mjs (OpenAPI drift check) + the evidence-capture scripts
│   └── public/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── feature_parity/        # offline/online consistency (CI, against live Postgres + Redis)
│   ├── learned_serving/       # manifest-backed two-stage serving against live stores
│   └── tenant_isolation/      # Phase 3 — cross-tenant leakage canaries (CI, against the real compose
│                              #   stack); the deployable remote probe lives in synthetic/
└── infra/                     # images, platform config, and the operational scripts around them
    ├── README.md              # one line per directory + which compose file / Make target consumes it
    ├── api/                   # FastAPI image + entrypoint dispatching serve | bootstrap | verify
    ├── features/              # Feast + LightGBM sidecar image; bakes the serving bundle and applies the registry
    ├── model-bundle/          # committed candidate-index.json + ranker.txt + manifest.json (baked, SHA-256-pinned)
    ├── pgbouncer/             # dev config plus the production image: env-rendered, scram-sha-256, forced-user aliases
    ├── postgres/              # pgbouncer_auth SECURITY DEFINER lookup, run once during provisioning
    ├── postgres-init/         # dev-only mlflow database bootstrap
    ├── keycloak/              # production image, prod realm/client templates, idempotent provision.sh
    ├── backup/                # pg_dump + age + rclone image, nightly backup.sh, restore-drill.sh
    ├── k6/                    # loadcheck image — the box never mounts a source tree, so the scripts are baked
    ├── edge/                  # Caddyfile for both TLS modes: Let's Encrypt on the box, Caddy's CA on a laptop
    ├── host/                  # bootstrap.sh (idempotent: deploy user, sshd, ufw, unattended upgrades, Docker,
    │                          #   log rotation) + the movielens / backup / prune systemd units
    ├── deploy/                # deploy.sh (release, verify, automatic rollback, DEPLOY-OK sentinel),
    │                          #   production.env.example, provision-roles.sql (the one copy of the
    │                          #   one-time role SQL), rollback-rehearsal.sh, README
    ├── mlflow/                # MLflow image
    └── ci/                    # pinned tool versions (k6)
```

## Conventions

- **Python:** 3.11+, type hints everywhere, ruff for lint, black for format, mypy in CI on `src/`.
- **TypeScript:** 5+, strict mode, no implicit `any`, ESLint for lint, Prettier for format, `tsc --noEmit` in CI on `web/`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`).
- **Branches:** trunk-based, short-lived feature branches, PRs to main. Every piece of work — no matter how small — goes on a feature branch and merges via PR. No direct pushes to `main`.
- **GitHub:** repo is **public** (`kudratsingh/MovieLens-RecSys`), made public for portfolio visibility — so nothing secret ever lives in the tree; the dev-only credentials in compose and the realm seeds are labeled as such. Branch protection on `main` (PRs required, CI must pass, no direct pushes). Default to squash merges. No open-source license is attached — the code is published for portfolio visibility, all rights reserved. README states what the project is, the stack, and the current phase.
- **Branch naming:** `feat/<short-description>`, `fix/<short-description>`, `docs/<short-description>`, `chore/<short-description>`. Keep branches short-lived; delete after merge.
- **PR discipline:** small and reviewable, one coherent unit per PR. Bundle related work (an ADR with the code it justifies, code + the `docs/status/` ledger update it triggers, multiple closely-related small docs) rather than splitting on every micro-concern — see "How to work with Claude Code" for the longer version. PR description explains *why*, not just what. Never merge a PR with failing CI.
- **Comments:** natural and human-like. Write the kind of comment a thoughtful senior engineer would leave — explain the *why* when it's not obvious, not the *what*. Don't over-comment mechanical code. Don't use aggressive or robotic phrasing.
- **Testing:** pytest. Every model module has tests. Feature parity tests run in CI.
- **Logging:** structured (JSON), same approach as the Incident Platform.
- **Config:** pydantic-settings, env vars for secrets, no hardcoded paths.

## Current status

**Updated 2026-08-30.** Phases 1 and 2 are complete; Phase 3 is in progress. This is the short form — the full ledger (every PR, what it landed and why, the remaining items per track, and the long-form current step) lives in [`docs/status/`](docs/status/README.md).

- **On `main`:** the authenticated, RLS-isolated two-stage serving path — item-item retrieval and a LightGBM ranker in a private sidecar, Feast/Redis features, durable prediction *and* request audits, per-tenant champion/quota columns on the registry, a shared Redis rate limiter, the pinned k6 p99 gate; the movie-discovery product (Discover, Browse, movie detail, Library with Seen, Quick Picks) cut over at `/`; the ADR 0011 cold-start cohort; measured offline results in `docs/results.md`; the production deployment specified and rehearsed (ADR 0013) but **not provisioned — nothing is deployed**; dev and staging Compose beside it.
- **Frontend finish gate:** every criterion a reviewer can settle passes; HOLD only on moderated sessions with real participants.
- **Open decisions, mine:** which slice the promotion gate reads; the two-tower's fate after its full-dataset run; which rung of `docs/modeling-roadmap.md` is approved next. (The cold-start routing policy was settled on 2026-08-30 — the threshold is 10 and the offline models route on it, like the deployed path.)
- **Remaining Phase 3 work** is itemised in [`docs/status/phase-3.md`](docs/status/phase-3.md): product track — moderated sessions then `/legacy` retirement, `/me` ownership, N6; platform track — Feast-backed ranker training, training-time candidate exclusions, and the two decisions above.

### Current step

Create the Hetzner box and run the first deploy (`docs/deployment-runbook.md` §1–§7). Run the moderated sessions. Take the three modeling decisions from the memo in progress, then approve the next roadmap rung — it starts as an ADR proposal, not code. Long form: [`docs/status/README.md`](docs/status/README.md#current-step).

## How to work with Claude Code on this

- **PR shape: small enough to review, large enough to be one coherent unit.** Bundle related work (an ADR with the code it justifies, multiple closely-related small docs, code + the `docs/status/` ledger update that captures it). Don't open a separate PR for every micro-concern — review overhead is real. The original "one concern per PR" wording was over-applied; the intent ("reviewable, focused") still holds.
- When introducing a new technology (Feast, Prefect, Evidently, FAISS, etc.), include a substantive ADR in the same PR explaining the choice and the alternatives considered.
- **ADRs are substantive, not checkbox.** Recent ADRs ran ~50 lines; the standard going forward is more like 120–180 lines with depth on rationale, alternatives (each treated with analysis, not a single sentence), consequences (including second-order effects), and where relevant a Risks section and a "How we'd know we're wrong" section. ADRs are the artifact a future me reads to remember why this choice was right.
- Before writing code for a new phase, re-read the relevant phase section above and confirm scope.
- Don't skip the non-negotiables to save time. They are the project.
- When in doubt about a design choice, ask me — the explanation is the point of the project.
- **Watch for leakage in feature engineering.** Any feature that uses future information silently inflates offline metrics. Point-in-time correctness is the standard — features must only use data available at the time of prediction. This binds especially tight on the two-tower's history input (user history at training time must only contain items consumed strictly before the positive's timestamp).
- **Get an end-to-end path working early.** Even a janky popularity baseline served via FastAPI is more valuable than a perfect offline model with no serving layer. Discover serving assumptions early.
- **Never compute metrics ad-hoc in notebooks.** Every model run goes through `src/evaluation/`. This is how the protocol stays honest across weeks of work.
- **Real auth, multi-tenancy, and synthetic-load harnesses arrive in Phase 3.** Before then, no Phase 2 code should assume their existence (no tenant id threading, no per-tenant model registries). After Phase 3, every new endpoint is authenticated by default and every code path is tenant-aware.
- **Multi-agent etiquette (backend + frontend agents sharing one working tree).** Backend owns the flat top-level ADRs, `src/`, `pipelines/`, `tests/` (except frontend-specific), `Makefile`, `infra/`, and most of CLAUDE.md. Frontend owns `web/` and `docs/adr/frontend/`. Both touch `CLAUDE.md` occasionally; coordinate. **Critical:** `git branch --show-current` before every commit — HEAD is shared across the working tree and either agent's branch switch moves it for both.
