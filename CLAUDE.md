# MovieLens Two-Stage Recommender — CLAUDE.md

## Purpose of this project

This project started as a portfolio-grade applied ML build and, **as of 2026-06-02, has been re-scoped to target enterprise-grade**. Both framings still apply:

- **Original framing (still load-bearing).** Confront the technologies and scenarios a mid-to-senior ML engineer actually deals with at an enterprise. Modeling is the easy part; the engineering around the model is the point. ML counterpart to my Incident & Workflow Platform project — same philosophy, same trick of building something substantial enough that production concerns force themselves on you end-to-end.
- **Expanded framing (2026-06-02 scope shift).** The system itself should meet enterprise standards on **real auth, multi-tenancy, observability, and synthetic-load realism** — not just look enterprisey in writeups. The Phase 3+ work that previously assumed "internal-only, no real auth" is replaced with the real shape: an authenticated, multi-tenant API with synthetic-traffic harnesses that exercise the latency SLO and cold-start path under load. See the Phase 3 section for the expanded scope and the "What the system does" section for the updated system description.

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
| Orchestration runtime | Local first; k3s/kind optional after Phase 4 | Don't let K8s block progress |

## Phased plan

Each phase earns a specific set of mid-level muscles. Don't skip ahead — the lessons compound.

The execution sequence and definition of done for the working Phase 3 demo live
in [`docs/demo-plan.md`](docs/demo-plan.md). This file remains authoritative for
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
4. **Latency SLO.** p99 < 100ms. **Measured under synthetic load**, not assumed. The Phase 3 synthetic-load harness runs in CI on every serving PR. A breached window is re-measured exactly once, and only when the host's own CPU-steal record shows the runner was preempted — a measurement-validity rule, never a relaxed threshold (ADR 0010).
5. **Reproducibility test.** `make train` on a fixed seed and dataset version produces the same model artifact hash. If it doesn't, something is nondeterministic — find it.
6. **ADRs (architecture decision records).** A `docs/adr/` folder explaining *why* I chose each major piece. Two namespaces: backend ADRs at the top level (flat numeric line), frontend ADRs under `docs/adr/frontend/`. One ADR per significant decision; ADRs are substantive (closer to 150 lines than 50) and explore alternatives, consequences, and "how we'd know we're wrong" rather than reading like checkboxes.
7. **Evaluation gate before promotion.** A model never goes to production without beating the incumbent on a holdout — automated, not eyeballed. Per-tenant gates are scoped per tenant.
8. **Logged predictions and features.** Every online prediction logged with the features used, the tenant, the model version, and the latency, so we can replay, debug, compute online metrics, and audit per-tenant behavior later.
9. **Tenant isolation.** No code path may return one tenant's data in response to another tenant's request. Cross-tenant leakage is the highest-severity bug class. An automated CI integration test exercises every endpoint as tenant A and asserts no tenant B data surfaces.
10. **Auth on every endpoint except `/healthz`.** No "internal" unauthenticated paths. Dev-mode bypass exists for local development only and is asserted off in staging/prod builds.
11. **Synthetic-load smoke test in CI.** Every PR that touches `src/serving/` runs a short synthetic-load script and fails if p99 exceeds the SLO threshold on a defined baseline workload. The gate has to measure the service and not the runner: it quiesces what it does not measure, warms every worker before the window opens, and fails warm traffic that quietly degrades to the popularity fallback. The thresholds themselves have never moved.

## Repo structure (target)

```
movielens-recsys/
├── CLAUDE.md                  # this file
├── README.md
├── Makefile                   # train, serving-artifacts*, serve, test, lint, db-migrate, demo-*, prod-*,
│                              #   api-contract*, web-api-types* targets
├── scripts/                   # generate_openapi.py — committed OpenAPI contract + CI drift check
├── alembic.ini
├── alembic/                   # Phase 3 — tenant roles, tenants registry, tenant_id + forced RLS, personas, feature tables, audits
├── docker-compose.yml         # default dev stack: postgres, redis, mlflow, prometheus, grafana, pgbouncer, keycloak
├── docker-compose.demo.yml    # layered on the default stack: api, web, feature-server, model-server, k6 (load profile)
├── docker-compose.staging.yml # planned — Phase 3 multi-environment infra
├── docker-compose.prod.yml    # production-mode rehearsal stack: generated secrets, ENVIRONMENT=production,
│                              #   no published data-store ports, Caddy edge terminating https
├── .github/workflows/         # CI: lint, unit, feature parity, tenant isolation, synthetic-load smoke, serving
│                              #   artifacts, realm drift, frontend, compose validation; plus the production
│                              #   deploy workflow and the scheduled production canary
├── docs/
│   ├── adr/                   # backend ADRs (flat numeric line) + cross-cutting
│   │   └── frontend/          # frontend ADRs (own numeric line)
│   ├── api/                   # generated openapi.json (do not hand-edit) + regeneration notes
│   ├── frontend/              # movie-discovery product docs: discovery, design contracts, implementation
│   │   │                      #   plan, readiness, testing strategy, finish-gate review
│   │   └── evidence/          # per-bundle screenshot matrices with per-file provenance
│   ├── eda.md
│   ├── demo-plan.md           # Phase 3 vertical-slice milestone: bundles, definition of done, walkthrough
│   ├── demo-runbook.md        # clean-checkout demo startup, seeding, smoke, reset, troubleshooting
│   ├── deployment-runbook.md  # production: identities, secrets, one-time SQL, release, rollback, restore drill
│   └── progress.md            # session-level progress log (frontend agent's; not authoritative)
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
│   ├── load/                  # k6 (ADR 0010): recommendations.js + thresholds.js (the pinned p99 gate),
│   │                          #   pages.js + page_thresholds.js (page-shaped per-step budgets),
│   │                          #   run_gate.sh (run, decide, re-measure at most once), summarize.py,
│   │                          #   probe_host_cpu.py, reliability.py (non-latency serving promises)
│   ├── personas/              # handcrafted demo users, catalog manifest, idempotent seeder
│   ├── smoke/                 # demo readiness + behavioral smoke checks
│   ├── tenant_isolation/      # remote_canary.py — the deployed cross-tenant probe; here rather than
│   │                          #   under tests/ because the API image ships synthetic/ and never tests/
│   ├── cold_start/            # planned — programmatic new-user cohorts (ADR 0011)
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
    ├── api/                   # FastAPI image + entrypoint dispatching serve | bootstrap | verify
    ├── features/              # Feast + LightGBM sidecar image; bakes the serving bundle and applies the registry
    ├── model-bundle/          # committed candidate-index.json + ranker.txt + manifest.json (baked, SHA-256-pinned)
    ├── pgbouncer/             # dev config plus the production image: env-rendered, scram-sha-256, forced-user aliases
    ├── postgres/              # pgbouncer_auth SECURITY DEFINER lookup, run once during provisioning
    ├── postgres-init/         # dev-only mlflow database bootstrap
    ├── keycloak/              # production image, prod realm/client templates, idempotent provision.sh
    ├── backup/                # pg_dump + age + rclone image, nightly backup.sh, restore-drill.sh
    ├── railway/               # one config-as-code JSON per deployed service
    ├── k6/                    # loadcheck image — a PaaS service has no repo to bind-mount the load scripts from
    ├── edge/                  # Caddyfile for the local https rehearsal (real issuer behind a proxy)
    ├── deploy/                # production.env.example, provision-roles.sql (the one copy of the
    │                          #   one-time role SQL), rollback-rehearsal.sh
    ├── mlflow/                # MLflow image
    └── ci/                    # pinned tool versions (k6)
```

## Conventions

- **Python:** 3.11+, type hints everywhere, ruff for lint, black for format, mypy in CI on `src/`.
- **TypeScript:** 5+, strict mode, no implicit `any`, ESLint for lint, Prettier for format, `tsc --noEmit` in CI on `web/`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). Do **not** add `Co-authored-by` trailers, "Generated with Claude Code" footers, or any attribution to Claude / Claude Code / any AI tool in commit messages, PR descriptions, code comments, docstrings, or the README. All commits are authored solely by me.
- **Branches:** trunk-based, short-lived feature branches, PRs to main. Every piece of work — no matter how small — goes on a feature branch and merges via PR. No direct pushes to `main`.
- **GitHub:** repo is **public** (`kudratsingh/MovieLens-RecSys`), made public for portfolio visibility — so nothing secret ever lives in the tree; the dev-only credentials in compose and the realm seeds are labeled as such. Branch protection on `main` (PRs required, CI must pass, no direct pushes). Default to squash merges. No open-source license is attached — the code is published for portfolio visibility, all rights reserved. README states what the project is, the stack, and the current phase.
- **Branch naming:** `feat/<short-description>`, `fix/<short-description>`, `docs/<short-description>`, `chore/<short-description>`. Keep branches short-lived; delete after merge.
- **PR discipline:** small and reviewable, one coherent unit per PR. Bundle related work (an ADR with the code it justifies, code + the CLAUDE.md status update it triggers, multiple closely-related small docs) rather than splitting on every micro-concern — see "How to work with Claude Code" for the longer version. PR description explains *why*, not just what. Never merge a PR with failing CI.
- **No AI attribution anywhere.** No mention of Claude, Claude Code, or any AI tool in: commit messages, PR titles, PR descriptions, code comments, docstrings, ADRs, the README, or any other file in the repo. All work is attributed solely to me.
- **Comments:** natural and human-like. Write the kind of comment a thoughtful senior engineer would leave — explain the *why* when it's not obvious, not the *what*. Don't over-comment mechanical code. Don't use aggressive or robotic phrasing.
- **Testing:** pytest. Every model module has tests. Feature parity tests run in CI.
- **Logging:** structured (JSON), same approach as the Incident Platform.
- **Config:** pydantic-settings, env vars for secrets, no hardcoded paths.

## Current status

**Updated 2026-08-27.** Phase 1 and Phase 2 are complete. Phase 3 is underway: its architecture ADRs (0007–0012), the auth/tenancy foundation, the Feast-backed learned online recommendation path, durable demo personas, prediction audits, the measured k6 latency gate, and the whole movie-discovery frontend — Discover, Browse, movie detail, Library, and Quick Picks behind one shell, with `/` cut over to the product — are all on `main` (PRs #27–#69). The frontend finish gate has now been run twice, and every criterion a reviewer can settle passes; the remaining HOLD is moderated research with real participants, which is mine to run and not a reviewer's to substitute for. On top of that, the production deployment now exists as an artifact rather than an intention — ADR 0013 pins Railway and the topology, ADR 0014 closes the rate-limiting question, and `docs/deployment-runbook.md` is the operational document — but it has not been deployed, and the local production-mode rehearsal comes first. The current concrete step (the one to take next) is at the bottom of this section.

### Phase 1 — complete

Baselines, data foundation, and the evaluation harness all landed:

- ADR 0001 (evaluation protocol) and ADR 0002 (implicit-feedback labeling) pin the contracts every model trains and is scored against.
- `src/evaluation/` is the single source of truth for metrics — warm/cold user slicing per ADR 0001, used by every model run; no ad-hoc metric computation anywhere else (non-negotiable #5).
- MovieLens 25M ingested into Postgres (`movielens` DB, 25 000 095 ratings) and versioned with DVC. Stack runs via docker-compose: Postgres, Redis, MLflow (psycopg2-enabled), Prometheus, Grafana.
- Temporal train/holdout/test split (`src/data/split.py`) implementing ADR 0001's `T = percentile_disc(0.8)` cutoff. Train hits exactly 80.00% of rows; holdout = 28 days × 129 683 interactions × 2 641 users (~26.6% cold-start).
- EDA writeup in `docs/eda.md` (2026-05-31 snapshot) characterizes scale, sparsity, rating distribution, item popularity tail, the temporal split as applied to real data, and cold-start sizing.
- Popularity baseline (`PopularityModel`, PR #12) — first MLflow run logged into experiment `phase-1-baselines`.
- CF/ALS baseline (`CFModel` via `implicit`, PR #14) — second run in the same experiment; embeds popularity fallback for cold users per ADR 0001.
- Per-policy attribution metrics (PR #17) — `CFModel.was_served_by_als(user_id)` predicate + per-policy MLflow metrics partition holdout by ALS-served vs popularity-fallback-served users.

### Phase 2 — code-complete

Two-stage architecture (offline). The top-level choice is pinned by ADR 0003. Status:

- ✅ **ADR 0004 (item-item before two-tower)** — merged (PR #18). Pins item-item as the zero-learned-parameters baseline the two-tower has to beat.
- ✅ **ADR 0005 (LightGBM over neural ranker)** — pins ranker family, LambdaRank objective, and the training-data construction rule (positives from train's trailing window, candidate-model-sampled negatives, per-(user,timestamp) LambdaRank groups). Bundled with the ranker code.
- ✅ **Item-item similarity candidate generator** (`src/models/candidates/itemitem.py`, PR #19) — `implicit.nearest_neighbours.CosineRecommender` with `k_neighbors=200`, same embedded popularity fallback CFModel established. Runs land in the new MLflow experiment `phase-2-candidates`.
- ✅ **Per-stage evaluation in the harness** (PR #19) — `src/evaluation/protocol.py` exposes `K_CANDIDATES = 500` and an optional `k` parameter on `evaluate()`. `EvalResult.k` is stamped on every result so downstream consumers can't confuse a candidate-stage `recall@500` with a recommender-end-to-end `recall@10`.
- ✅ **Two-tower candidate generator** (PyTorch, PR #24) — history-based user tower (mean-pool over last N=50 items, no per-user-id embedding), id-only item tower, embedding dim 64, sampled softmax with log-uniform negative correction (Yi et al. 2019), FAISS-CPU IVF-Flat ANN index over cosine-normalized item embeddings, embedded popularity fallback for zero-history users. Ships with **ADR 0006 — Two-tower retrieval architecture** in the same PR. Runs land in `phase-2-candidates` alongside item-item so ADR 0004's promotion gate can compare them directly.
- ✅ **Feature module** (`src/features/`) — point-in-time-correct user / item / user×item features (interaction count, days-active, popularity windows, item age, genre affinity). `FeatureIndex` precomputes per-user and per-item sorted timestamps so per-query lookup is O(log n) via `bisect`. Point-in-time correctness enforced by a strict-equality canary test on a hand-built fixture. Provisional home until Phase 3 introduces Feast.
- ✅ **LightGBM ranker** (`src/models/ranker/lgbm.py`) — LambdaRank booster scored against NDCG@10 per ADR 0001. `LGBMRanker.rank_candidates(...)` is the end-to-end re-ranking shape Phase 3's serving handler will call. Runs land in a new `phase-2-ranker` MLflow experiment; per-feature importances logged for a Phase 4 SHAP explainer to build on.

Phase 2 stayed all-offline — no FastAPI, no Redis online store, no Feast. Those open with Phase 3.

### Phase 3 — in progress

Serving, auth, multi-tenancy, feature store, and the synthetic-load harness. The platform decisions are pinned by ADRs 0007–0011 (PRs #27–#31) and the serving platform landed in PRs #32–#44; ADR 0012 and frontend ADR 0002 (PR #45) pin the browser-identity and movie-discovery contracts, and the product itself landed as Bundles 1–7 in PRs #47–#65. The vertical-slice milestone and its definition of done are tracked in `docs/demo-plan.md`; the frontend redesign bundles in `docs/frontend/implementation-plan.md`, with the written gate in `docs/frontend/finish-gate-review.md`. Bullets are ordered by the PR that landed them. Status:

- ✅ **ADR 0007 + auth foundation** (PRs #27, #32, #33) — Keycloak self-hosted, realm per tenant, with `default` and `demo` realms seeded from `infra/keycloak/realms/`. `src/auth/middleware.py` validates tokens against a TTL-cached JWKS (`src/auth/jwks.py`), derives the tenant from the token issuer, and attaches a `RequestPrincipal` to `request.state`. A dev-only bypass is refused by `Settings.__init__` outside `environment == "dev"`.
- ✅ **ADR 0008 + tenancy foundation** (PRs #28, #32, #33) — Alembic migrations 0001–0009 under `alembic/versions/` create the `app_user` / `admin_user` roles, the `public.tenants` registry, `tenant_id` on every scoped table, and forced RLS policies. The middleware opens a per-request transaction with `SET LOCAL app.tenant_id`; pgBouncer (`infra/pgbouncer/`) runs in transaction-pool mode so the setting can't leak. `src/serving/tenancy/router.py` resolves a tenant to its config and Redis key prefix; `src/serving/startup_checks.py` refuses to boot if the app engine can bypass RLS or pgBouncer is in session mode. `tests/tenant_isolation/` runs in CI against the real compose stack.
- ✅ **ADRs 0009–0011** (PRs #29–#31) — Feast (Postgres offline + Redis online), k6 with Prometheus remote-write, and the fixed-seed cold-start cohort methodology are pinned ahead of their implementation bundles.
- ✅ **FastAPI serving skeleton** (PR #33) — `src/serving/app.py` with `/healthz` and authenticated `/whoami`, startup safety checks, and tenant-scoped database transactions.
- ✅ **Online recommendation path** (PRs #34, #43, #45) — `src/serving/recommendations.py` + `src/serving/orchestration.py`: authenticated warm requests retrieve item-item candidates, batch-read Feast/Redis features, rank with LightGBM, and filter live RLS-scoped history; cold or unavailable model paths fall back explicitly to popularity. Routing uses unique watched movie IDs against ADR 0011's shared `COLD_START_THRESHOLD = 5` — histories of 0/1/3 are fallback, 5+ may take the learned path.
- ✅ **Phase 3 baseline frontend** (PR #35) — Next.js user selector, recommendation grid, watch-history panel, serving-policy metadata, and a server-side FastAPI proxy. Frontend lint, strict type checking, and production build run in CI. Superseded by the movie-discovery product; retained only at `/legacy` (PR #65).
- ✅ **Durable demo personas** (PR #37) — `synthetic/personas/`: four named, tenant-scoped synthetic users (Action Fan, Drama Fan, Eclectic Viewer, and Cold Start), checked-in catalog/history fixtures, an idempotent `make demo-seed` path, authenticated persona discovery (`/personas`), and RLS isolation coverage. Migration 0005 tags them `synthetic=true`.
- ✅ **TMDB metadata and posters** (PR #38) — `src/serving/tmdb.py` resolves MovieLens `tmdbId` values server-side through a bounded TTL/LRU cache, degrades to MovieLens metadata when the token or upstream is unavailable, and the web app renders optimized posters with accessible visual fallbacks and required attribution. The token never leaves the backend.
- ✅ **Repeatable demo environment** (PRs #39, #40) — `docker-compose.demo.yml` layered on `docker-compose.yml` builds the FastAPI (`infra/api/`) and standalone Next.js images, bootstraps base tables plus Alembic migrations (`src/data/demo_setup.py`), loads a self-contained reviewed catalog/persona fixture, verifies readiness and warm/cold behavior (`synthetic/smoke/demo.py`), and supports isolated down/reset/log operations via `make demo-*`. Runbook in `docs/demo-runbook.md`.
- ✅ **Interactive rating loop** (PR #41) — `PUT /users/{id}/ratings/{movie_id}` and `DELETE /users/{id}/ratings` write through the RLS-bound request connection (migration 0006); history and recommendations are re-requested after the write commits. The star value is stored for display — the deployed learned path consumes the watched movie ID, not the rating magnitude (ADR 0012).
- ✅ **Feast/Redis feature path** (PR #42) — `src/features/feast_repo/` declares `tenant` / `user` / `item` entities and three feature views over `feature_store.*` Postgres tables; `src/features/materialize.py` publishes tenant-keyed snapshots into Redis; `src/features/online.py` exposes no unscoped read. A dedicated feature-server container (`infra/features/`) serves online reads. `tests/feature_parity/` runs in CI against live Postgres + Redis (non-negotiable #2).
- ✅ **Versioned learned serving** (PR #43) — `src/models/artifacts.py` defines a SHA-256-pinned `ServingManifest` binding the item-item index, the LightGBM booster, the tenant, and the ordered feature contract (`src/feature_contract.py`). `src/training/demo_artifacts.py` trains and publishes the bundle; `src/serving/model_server.py` is a private, token-protected sidecar that loads it once at startup so the slim API image never imports Feast, pandas, or LightGBM. `tests/learned_serving/` covers the manifest-backed path.
- ✅ **Prediction audit + k6 gate** (PR #44) — `src/serving/audit.py` + migrations 0008/0009 persist every recommendation's exact predictions, feature values, model versions, fallback reason, and per-stage timings into forced-RLS `recommendation_audits`, readable via `GET /users/{id}/audits` (non-negotiable #8). `synthetic/load/` k6 scripts (version pinned in `infra/ci/k6-version`) drive real-Keycloak warm/cold/mixed traffic against a bypass-disabled `api-load` service and gate p99 < 100 ms, zero errors, correct responses, and >50 requests/second; the `synthetic-load-smoke` CI job runs the 60-second profile (non-negotiables #4, #11). Accepted 2026-08-20 baseline: p50 6.31 ms, p95 14.27 ms, p99 41.30 ms. Hardened 2026-08-21 (PR #59; ADR 0010, "measuring the service rather than the runner") so the gate measures the service and not CI contention — the unmeasured services are quiesced, `setup()` primes every worker × persona, the VU ceiling no longer drops slow arrivals, and warm traffic must prove `serving_policy.learned` rather than merely returning 200. The same note covers the second round: promotion-only CFS priority for the measured services (nothing is demoted except Prometheus, so the sibling browser-auth job is unaffected), no Prometheus in the 60-second profile, per-second latency buckets joined against host CPU steal, an uploaded evidence artifact, and a documented re-measure-once rule for windows breached under hypervisor preemption. No threshold, arrival rate, or workload changed in either round.
- ✅ **Movie-discovery product contract** (PR #45) — `docs/frontend/` (product discovery, route-level design contracts, backend-readiness matrix, implementation plan, testing strategy and finish gate, baseline evidence) and frontend ADR 0002 define a poster-first Discover / Browse / Library / detail experience with progressive disclosure of ML evidence. Cross-cutting ADR 0012 pins browser identity (actor vs. persona, `/me/...` resources, PKCE via a Next.js BFF session), the `user_movie_state` + `user_feedback_events` read model, feedback-transition semantics, commit-before-acknowledge durability, and what a rating does and does not change online.
- ✅ **Bundle 1 correctness and contract foundation** (PR #45) — the auth middleware commits the request transaction *before* returning a successful response (no 2xx for a mutation or fail-closed audit that can still fail to become durable); tokens must carry `aud=movielens-api` and an `azp` in the explicit allow-list (`movielens-api`, `movielens-web`); arbitrary persona selection requires the confidential service client or the `demo-impersonator` realm role; a verified realm with no `public.tenants` row is rejected at the authenticated boundary. `scripts/generate_openapi.py` commits `docs/api/openapi.json`, `web/lib/api.generated.ts` is generated from it, and both drift checks run in CI (`make api-contract-check`, `make web-api-types-check`).
- ✅ **Bundle 1 browser session** (PR #47) — Auth.js runs the real Keycloak authorization-code + PKCE flow, keeps API tokens in an encrypted HttpOnly server session, rotates access tokens, propagates logout, enforces Origin/CSRF on BFF mutations, pins the public issuer separately from internal Compose routing, and passes bypass-disabled Playwright against a freshly imported realm. The pre-redesign responsive/error/fallback screenshot matrix is committed for finish-gate comparison.
- ✅ **Bundle 2 durable feedback and Library foundation** (PR #48) — migration 0010 creates forced-RLS current movie state (`user_movie_state`) and append-only actor-attributed feedback events without rewriting imported ratings. Idempotent revisioned watched/rating/watchlist/dismissal mutations, cursor Library APIs, truthful `live-ratings-v1` taste summaries, and the authenticated selected-persona `/library` route consume the server-owned Auth.js session boundary.
- ✅ **Bundle 3 scalable catalog and movie detail** (PR #50) — migration 0011 persists a local movie-metadata read model so a poster-rich grid never fans out to TMDB per card. `GET /users/{id}/catalog` serves deterministic search, genre and year filters, three sorts, and opaque cursors bound to the normalized query fingerprint (a cursor reused against a different query is a 400, and no total is invented); `GET /users/{id}/movies/{movieId}` serves detail. Both overlay the durable Bundle 2 movie state, and the reviewed demo fixture grew to 120 titles with 24 enriched posters. Contract in `docs/frontend/catalog-contract.md`.
- ✅ **Bundle 4 movie-discovery frontend system** (PR #52) — semantic visual and motion tokens, responsive desktop/mobile navigation, and the reusable poster, rail, collection, state, rating, drawer, loading, empty, and error primitives every product route is built from, with authenticated preview shells at `/ui-preview` and typed fixtures that fail closed in production. Vitest, RTL, jest-axe, and a responsive Playwright matrix at 390/768/1440 arrive with it. Documented in `docs/frontend/frontend-system.md`.
- ✅ **Bundle 5A live resource boundary** (PR #53) — `web/lib/resources/` is the one server-owned client that reaches FastAPI: per-resource timeout budgets, narrow hand-written validators over the generated OpenAPI types, `X-Request-ID` generation/adoption/echo, `private, no-store` on personalized BFF responses, caller-supplied bearer tokens refused at the BFF edge *and* in the browser reader, and a fixture lockout asserted structurally by reading the module's own source. Its `loading`/`retry`/`ready`/`empty`/`forbidden`/`auth-expired`/`not-found`/`upstream-error` state model renders through one region component, so a failed resource never blanks the regions around it.
- ✅ **Bundle 6 backend — positive history separated from exclusions** (PR #54) — `src/serving/recommendations.py` reads positives (watched, not dismissed) and exclusions (dismissals plus already-seen) from `user_movie_state` in one round trip; `src/serving/orchestration.py` passes them to the sidecar as distinct inputs and re-applies the exclusion set at the popularity fallback, candidate retrieval (`src/models/artifacts.py`), metadata hydration, and a final fail-closed check. A dismissed title never seeds item-item retrieval and is never written back as a rating or training negative (ADR 0012). `src/serving/policy.py` pins the shared filter-policy, score-scale, and input-digest vocabulary; the recommendation response gains an additive `serving_policy` object (name, learned, positive-signal count, threshold 5, structured reason, score scale, filter policy) and migration 0012 adds the input-state revision/hash, exclusion hash, feature event time, filter policy, candidate-source contributions, and structured reason to `recommendation_audits`, all readable via `GET /users/{id}/audits`.
- ✅ **Request correlation across the whole surface** (PR #54) — `src/serving/request_id.py` adopts a well-formed inbound `X-Request-ID` (1–128 printable ASCII characters, no whitespace) and echoes it on every response, minting a UUID otherwise; `recommendation_audits.correlation_id` stores the echoed value while `request_id` stays the row's own identity so a replayed header cannot collide. Tenant-isolation canaries now also cover `/users/{id}/features`, `/users/{id}/catalog`, and `DELETE /users/{id}/ratings`.
- ✅ **Bundle 5B–5D — the movie-discovery route slices** (PRs #55, #56, #57 — 5D, 5B, and 5C in the order they merged) — every route reads through the 5A boundary and claims only what the response reports. `/discover` loads recommendations and watch history as independent server-owned regions, with the technical evidence (`Why this?` → prediction audit + online features) reachable in two deliberate actions so it can never delay the first movie; the policy label follows the reported `serving_policy.learned` flag rather than an inference, and the rank score appears only inside the disclosure, labelled as an uncalibrated ordering. `/browse` and `/movies/[movieId]` sit on the Bundle 3 catalog contract with URL-owned filters, de-duplicated cursor pages over the endpoint's total ordering, a stale cursor restarted from the top behind a plain notice, and per-tab restoration of the loaded window and scroll position after a detail visit. `/library` reads Rated, Watchlist, and History independently per tab with URL-owned state, optimistic reconciliation against the committed revision, and `Remove rating` kept quiet and distinct from a confirmed `Remove from history`.
- ✅ **Bundle 6 Quick Picks** (PR #58) — `/quick-picks` is one decision at a time on the exclusion-aware serving contract. A pure reducer owns the queue, so buttons, `J`/`K`/`L`/`U`, and pointer swipes dispatch the same action — parity is the mechanism, not a convention — and the card advances only after the API commits, because a one-card queue that advanced optimistically and rolled back would re-show a title the viewer already dismissed. Progress toward the five-signal threshold is read from `serving_policy.positive_signal_count` and learned copy waits for a returned `learned === true`. `web/lib/quick-picks/contract.ts` is one assertable statement of what each action means: watchlist is organizational, watched is one positive interaction, dismissal is an undoable exclusion and never a training negative (ADR 0012).
- ✅ **Serialized service-backed browser journeys** (PR #60) — `web/playwright.config.ts` pins `workers: 1` for the specs that run against the seeded Compose stack, and each journey owns one persona and reverses its own writes (Action Fan for movie state and Library, Drama Fan for Discover, Eclectic Viewer for Browse, Cold Start for the PKCE reset and then Quick Picks, which needs the zero-signal state the reset leaves behind). Three journeys had been writing the same persona's revision, producing 409s and cross-test flakes; serialized, the whole set runs faster than the contended parallel one. Sign-in moved to a shared `web/tests/e2e/keycloak.ts` helper. The fixture-mode harness (`playwright.ui.config.ts`) is untouched and still fully parallel.
- ✅ **Bundle 7c — one control family, one write path** (PR #61) — `web/components/movie/movie-state-controls.tsx` replaces the three per-slice control sets with a declared, ordered control set per surface, and `web/lib/movie-state/` becomes the only way any surface commits: the ADR 0012 transition table written once, the intent-bound idempotency key, `expected_revision`, the conflict re-read, rollback with an announced restore, the focus walk, and the committed-state relay. The copies had already diverged — one optimistic table left a watchlist entry standing after a movie was marked watched, and only one of the two mutation clients turned a `409` into a correction rather than telling the viewer to reload. Behaviour-preserving; `docs/frontend/frontend-system.md` is the resulting system of record.
- ✅ **Bundle 7b page-shaped load budgets and browser timing** (PR #62) — `synthetic/load/pages.js` models each route's real fan-out, cursor continuation, Library read, mutation-plus-immediate-read, and Quick Picks action sequence as tagged per-step k6 workloads with measured p95/p99 budgets in a separate thresholds module (the pinned recommendation gate is untouched); `web/tests/perf/` measures LCP, CLS, and time-to-visible-acknowledgement separately in a real browser on the pinned mobile profile and asserts the structural layout promises; `synthetic/load/reliability.py` proves request-id traceability into the audit row, the auth boundary, dependency provenance, bounded pages, cursor rejection, and degraded-metadata operation, and records that rate limiting is not implemented. Both suites follow the persona ownership table the browser journeys keep in `web/tests/e2e/browser-auth.spec.ts`, undo every write, and refuse to run — or to finish — if anything touched the cold-start persona. Page correctness, browser CLS, LCP, and the structural claims are enforced in CI; the per-step latency budgets and the acknowledgement budget are advisory until runner data exists, with the promotion rule written into ADR 0010's 2026-08-21 page-shaped note.
- ✅ **Bundle 7a frontend finish gate — HOLD** (PR #63, `docs/frontend/finish-gate-review.md`) — the handoff's ten-step journey now runs end to end against the seeded Compose stack with `DEV_AUTH_BYPASS=false` (`web/tests/e2e/finish-gate-journey.spec.ts`, in the serialized `browser-auth-e2e` set) and the visual/accessibility gate covers the named state matrix at 390/768/1440 plus a 320px sweep (`web/e2e/finish-gate.spec.ts`, in the `frontend` job); the written review records **HOLD** on three cutover items — `/` still serves the pre-redesign dashboard and its static `Candidate policy: Popularity baseline` panel contradicts the `item-item-cosine+lightgbm` policy the same session's API reports, `/discover` has no inbound link from any surface, and Browse and movie detail run a second shell without the contracted mobile bottom navigation — while every criterion applied to the five product routes themselves passes.
- ✅ **Item-item retrieval seeds from watched history again** (PR #64) — the exclusion set the coordinator sends the sidecar necessarily contains the user's own watched titles, and `CandidateIndex.retrieve` was using it to filter the seed set, so every warm persona was in fact served the index's popularity fill scored by LightGBM while the response reported `learned: true` over "0 positive seeds" (finish-gate finding N2); dismissals now travel on their own `dismissed_movie_ids` input as the only signal that may drop a seed, `seed_count` reports the seeds retrieval actually used rather than the ones offered, and a retrieval no seed reached is reported as `unseeded-retrieval` / `popularity-fill+lightgbm` with `learned: false` instead of borrowing the learned label.
- ✅ **Bundle 7d cutover — the product is the front door** (PR #65) — `/` serves the movie-discovery product to a signed-in viewer and the sign-in door to everyone else; the pre-redesign dashboard lives only at `/legacy`, labelled as the retained rollback, and its serving-contract panel reports the `serving_policy` the response carried instead of the `Popularity baseline` constant that contradicted it; every primary navigation points at `/discover`; and Browse, movie detail, and Library render the one shared `AppShell`, so the two parallel headers are deleted and the contracted mobile navigation and resolved persona name belong to the product rather than to three routes out of five. The re-run gate (`docs/frontend/finish-gate-review.md` §10) — run twice, once on the branch's own base and once against a tree carrying PR #64, since that landed mid-review and changed which titles every persona is served — records **all seven criteria passing and the three blocking items cleared** — the verdict stays **HOLD only because moderated sessions with real participants (4–5 viewers, 3–4 technical reviewers) are still owed**, which is not something a reviewer can substitute for. Putting `/` into the accessibility gate for the first time found three defects on the signed-out door — a 1.31:1 primary-action label, a 2.42:1 note, and a 320px overflow — all fixed here; the label was structural, since `button { color: inherit }` is unlayered and out-ranks every Tailwind utility.
- ✅ **Model-server native parallelism pinned, and the load gate instrumented** (PR #69, merged at `c2db933`) — each of the four model-server uvicorn workers was letting LightGBM/OpenMP and BLAS size a native thread team to the whole host, so process parallelism multiplied by native parallelism into a periodic CPU backlog: p50 81.57 ms / p95 551.37 ms / p99 903.64 ms at 0% host steal. With only `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS` set to `1`, the same unchanged gate passed at p50 7.24 ms / p95 12.50 ms / p99 48.99 ms. No threshold, timeout, worker count or durability rule moved. ADR 0010 carries the evidence, and the gate now also records the server-side handler latency next to k6's, per-second `srv_p99`, WAL/IO counter deltas and an `fdatasync` baseline on the Postgres volume — the instrumentation that distinguishes a slow service from a slow runner, which is what the two red CI runs on this fix's own branch turned out to need.
- ✅ **Production deployment — Railway (ADR 0013, `docs/deployment-runbook.md`)** — the system now has a deployment manifest instead of a plan for one. **ADR 0013** pins Railway, one `production` environment, and a topology of nine long-lived services (`postgres-app`, `postgres-keycloak`, `redis`, `pgbouncer`, `keycloak`, `api`, `model-server`, `feature-server`, `web`) plus five run-to-completion jobs (`release`, `keycloak-provision`, `verify`, `backup`, `loadcheck`), with **exactly two public hostnames** — `app.<domain>` and `auth.<domain>`. Everything else, the API included, is private on the WireGuard mesh and the deploy workflow asserts it (`no_public_sidecar`) before it deploys anything, because `feature-server` has no authentication at all. Post-deploy verification therefore runs from inside as a `movielens-verify` confidential client rather than from a GitHub runner. The cold-worker defect that blocked the MVP release is closed in the shape the deployment needs: `src/serving/model_server.py` now warms each worker **inside `lifespan`** — a real online feature read and a real booster predict, asserted to be non-degenerate — and `/healthz` answers **503 until warm**, so "healthy" is a statement about the first request rather than about whether artifacts loaded. It also means the sidecar refuses to boot against an unmaterialized Redis, which makes deploy ordering enforced by code rather than by convention. The sub-decisions ride in the same ADR: the 247 KB serving bundle and the Feast registry are **baked into the sidecar image**, so rolling back the model is rolling back the image; `/readyz` becomes a second unauthenticated path (no tenant data, no user data, deploy-probe only, private service) and the widening of non-negotiable #10's literal wording is recorded rather than quietly done; `/metrics` is deliberately **not** added, because there is no auth story for a scraper yet and Phase 5 owns it; Postgres traffic stays on the private mesh with `server_tls_sslmode = prefer` on the pooler leg, since every DSN is f-string-concatenated with no query hook and Feast hardcodes `sslmode: disable`; and production is **additive-migrations-only**, paired with a release step that exits 0 when the database is ahead of the image so a rollback's pre-deploy cannot fail on "Can't locate revision". Two gates changed shape: **non-negotiable #5 finally has a mechanical form** — `make train` exists, `ARTIFACT_AS_OF` is a pinned literal, and `make serving-artifacts-check` rebuilds the bundle and fails on any manifest drift — and **`synthetic/load/thresholds.js` in CI remains the SLO's only authority**, with the production canary (`LOAD_PROFILE=prod-canary`, ~5 arrivals/s) enforcing correctness and the warm-traffic `learned` assertion while recording p99 **with no verdict**, because it has no cgroup evidence and no CPU-steal join and so cannot claim ADR 0010's measurement-validity rule. Rollback turned out to be a capability finding rather than a command: the pinned Railway CLI cannot reach a specific previous deployment (`railway redeploy` takes no deployment id; `railway down` *deletes* the latest deployment), so both workflows drive Railway's GraphQL API — `serviceInstanceDeployV2` to ship the exact commit CI proved green, `deploymentRollback(id)` to revert — and `.github/workflows/deploy-production.yml` records every service's current deployment id as a `rollback-target` artifact **before it changes anything**. `docs/deployment-runbook.md` is the operational half: the four Postgres identities, the secret inventory and its generation rule, a pointer to the single copy of the one-time provisioning SQL (`infra/deploy/provision-roles.sql`), the release order, the rollback mutation, the restore drill, and a symptom table whose first row is that *recommendations look uniformly wrong* means `bootstrap materialize` before it means anything else. CI grew the two gates the deployment depends on — `serving-artifacts` rebuilds the committed bundle and compares the whole manifest, `realm-drift` exports the live dev realms and compares them semantically against the seeds — both behind a `changed-paths` filter, which is why `.github/workflows/deploy-production.yml` treats them as `success`-or-`skipped` while requiring every other `ci.yml` job outright. `.github/workflows/production-canary.yml` is the recurring half, and needs its own `production-canary` GitHub environment with no protection rules: a required reviewer on a job that fires every thirty minutes is no canary at all. **ADR 0014** rides along, closing product-track item (c) with the per-`(tenant, subject)` token bucket in `src/serving/ratelimit.py` — on by default outside `dev`, so no deployment depends on remembering a variable.
- ⏳ **Remaining Phase 3 — product track (ADR 0012, `docs/frontend/implementation-plan.md`, `docs/frontend/finish-gate-review.md`)** — (a) the moderated sessions the finish gate is holding on, and then retiring `/legacy` in a dedicated PR against the rollback diff in `docs/frontend/README.md`; (b) `/me` subject-to-profile ownership beyond the explicit portfolio persona mode; (c) a rate-limiting decision — 7b's reliability check measured its absence (60 rapid authenticated requests, all 200, no `X-RateLimit-*`), so it needs either per-tenant limits against the tenant-config row or an ADR that records the omission deliberately — **closed by ADR 0014 and `src/serving/ratelimit.py`**, a per-`(tenant, subject)` token bucket keyed on the verified token rather than on a client address (behind an edge every request comes from a proxy), which also turns that reliability check from a recorded absence into a required gate. Two limits are deliberate and written down rather than hidden: the bucket lives in the worker process, so a four-worker service admits `workers × limit` for one subject and a Redis-backed shared bucket is the named upgrade path; and the limits are global, because the per-tenant quota column belongs with the Phase 6 tenant-config work on `public.tenants` (platform track, item (d)); (d) the two non-blocking findings the cutover left open — **N6**, the product has no persona picker now that the dashboard is off the front door (the design contract requires a labelled persona, not a picker, and the chips remain on `/legacy`), and **N7**, `button, input, select { color: inherit }` sits outside any cascade layer in `globals.css` and so out-ranks every Tailwind colour utility app-wide, which is worth fixing with `@layer base` somewhere that is not a cutover PR.
- ⏳ **Remaining Phase 3 — platform track** (the production deployment work closes none of these except the `prod` half of (f); it deploys what exists rather than extending it) — (a) `synthetic/cold_start/` cohorts per ADR 0011, plus the `EvalResult.synthetic_cold_slices` harness extension and per-bucket MLflow metrics; (b) the ranker training path still builds features with `FeatureIndex` rather than Feast's `get_historical_features` as ADR 0009 specifies — offline/online parity is proven on the served snapshot, not yet on training rows; (c) training-time candidate generation still calls `CandidateIndex.retrieve` with no exclusions at all (noted in PR #64), so the ranker learns over a candidate mix serving no longer produces — either apply the serving filter offline or write down why the difference is acceptable; (d) per-tenant champion model version, quotas, and A/B seed columns on `public.tenants` — the tenant router currently resolves only id, display name, and Redis prefix, and the model sidecar is pinned to one tenant via `MODEL_TENANT_ID`; (e) generic request-audit coverage for authenticated non-prediction endpoints (the audit middleware matches only `/users/{id}/recommendations`; ADR 0012 allows a best-effort/queued policy there only once the durability tradeoff is written down); (f) `docker-compose.{dev,staging}.yml` with `make up-<env>` targets — the `prod` file landed with the deployment work as the production-mode rehearsal stack (`make up-prod`, `prod-seed`, `prod-keycloak-provision`, `prod-verify`, `prod-load`, `prod-backup`, `prod-rollback-rehearsal`, `prod-edge-ca`, `prod-logs`, `prod-reset`, `prod-down`, all behind a `prod-env-guard` prerequisite that refuses to run without `.env.prod`), so dev and staging are what remain. Item (e) in particular survives the deployment unchanged: audit coverage is recommendations-only in production too, and `docs/deployment-runbook.md` says so plainly rather than letting non-negotiable #8's wording read as a description of what is running.

### Current step

**Rehearse the production deployment, then create the project.** The deployment work is in the tree — ADR 0013, the production images, the release and verify entrypoints, the baked bundle, the Railway config files, the runbook — but nothing is deployed, and the point of the rehearsal is that every decision in it gets exercised somewhere a mistake is free. **Nothing is created on Railway until `make prod-reset && make prod-seed && make prod-verify` runs clean twice from a cold start.** The rehearsal steps that are actually load-bearing, because each one is something this codebase has never done: the first boot with `ENVIRONMENT != dev` and no `DEV_AUTH_BYPASS` variable at all; a full https OIDC round trip through the Caddy edge on a real hostname, since every issuer in the tree today is `http://localhost:8080`; pgBouncer doing server-side SCRAM through the forced-user aliases in **both** `auth_query` and `userlist` modes; Alembic and the seeder running as `migrator` rather than the superuser; `feast apply` at image-build time with only dummy connection `ARG`s; the four deliberate breaks each refusing to boot; an empty Redis failing the sidecar's boot and `materialize` repairing it; a rollback across a migration proving the DB-ahead no-op; and the restore drill with the seed step deliberately skipped. Then the Railway work is genuinely small: create the project with the service names the deploy workflow asserts, attach the two domains, create the four volumes, enable the Backups tab, create the `production` and `production-canary` GitHub environments, set the variables, run `infra/deploy/provision-roles.sql` once, deploy `keycloak` and run `keycloak-provision`, and run the release workflow. `docs/deployment-runbook.md` is the step-by-step; ADR 0013 is the why. The two tracks that were the current step before this are unchanged and still owed.

**Run the moderated sessions.** Bundles 0–7 are delivered and the cutover is done, so the only thing between the frontend and a recorded PASS is validation data — and it is mine to gather, not a reviewer's to substitute for. `docs/frontend/finish-gate-review.md` §10.8 names exactly what to run: 4–5 movie-focused participants and 3–4 technical reviewers, keyboard-only and small-screen coverage present in the mix, over the seven discovery tasks in §4.2 against the cutover build. Capture what the review deliberately left blank — completion and abandonment, time on task, errors and recovery, movie scan count before a decision, feedback-semantics comprehension, and whether the ML evidence is discoverable without being disruptive. Then replace §4.2 with the observed data and re-record the verdict; if nothing new surfaces it becomes PASS, and retiring `/legacy` becomes eligible as its own PR against the rollback diff in `docs/frontend/README.md`.

In parallel on the platform track, the next backend unit is **closing ADR 0011 — `synthetic/cold_start/`**: the fixed-seed 2 000-user cohort (500 per history bucket {0, 1, 3, 10}, popularity-weighted items, `synth_cold` tenant), `synthetic_cold_slices` on `EvalResult`, and per-bucket recall + fallback-attribution metrics in MLflow, shipped with the DVC-tracked parquet and a cohort-determinism test. After that, in order: per-tenant champion columns on `public.tenants` (unblocks Phase 6 routing), the Feast-backed ranker training refactor and the training-time candidate-exclusion question PR #64 surfaced alongside it, generic request audits, and the multi-environment compose split. Every new endpoint stays authenticated and uses the RLS-bound request connection.

## How to work with Claude Code on this

- **PR shape: small enough to review, large enough to be one coherent unit.** Bundle related work (an ADR with the code it justifies, multiple closely-related small docs, code + the CLAUDE.md status update that captures it). Don't open a separate PR for every micro-concern — review overhead is real. The original "one concern per PR" wording was over-applied; the intent ("reviewable, focused") still holds.
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
