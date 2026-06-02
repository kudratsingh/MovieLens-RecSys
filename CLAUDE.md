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

**Multi-tenancy.** A tenant is a logical isolation boundary. Cross-tenant data leakage is the highest-severity bug class (see non-negotiables). Phase 3 picks the isolation mechanism (per-tenant Postgres schema, row-level security, or per-tenant FastAPI instance — decided by ADR when Phase 3 begins). Champion-model assignment, API keys, rate limits, and audit logs are all scoped per-tenant.

**Auth.** Phase 3 introduces real auth on every API endpoint except `/healthz`. Auth provider choice (OAuth2/OIDC via Auth0, Keycloak self-hosted, or a JWT-only flow against a Postgres-backed user store) is ADR'd in Phase 3. There is no unauthenticated production path.

**Synthetic users.** A synthetic-user harness lives in `synthetic/` and serves narrow jobs, not one-size-fits-all generation:
- **Load testing** (k6 or Locust, ADR'd in Phase 3) — verify the p99 < 100ms SLO under realistic concurrency.
- **Cold-start coverage** — programmatically generated new-user states (history sizes 0, 1, 3, 10) to stress the cold-start fallback path beyond what MovieLens's natural distribution provides.
- **Drift simulation** (Phase 5) — synthetic users with shifting taste distributions to verify Evidently alerts fire.
- **A/B bucketing fixtures** (Phase 6) — deterministic tenant + user combinations for champion-vs-challenger tests.
- **Demo personas** — handcrafted users for portfolio walkthroughs of the frontend.

A **Next.js frontend** consumes the API. It is not an end-user product — it's a portfolio surface that makes the ML-engineering work visible. The frontend authenticates via the same auth provider as any other client and exposes the surfaces that exercise the system's interesting parts: feature-attribution panels, model/version selection, tenant switcher (for portfolio walkthroughs), and a champion-vs-challenger comparison view. A demo-impersonation mode is retained for portfolio showings — gated behind a dev/portfolio flag, never reachable from production deployments. Catalog search and full admin dashboards remain explicit non-goals (Grafana owns admin/operator views).

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
| Auth provider | TBD via Phase 3 ADR (Auth0 vs Keycloak vs Postgres-backed JWT) | Real auth in scope as of 2026-06-02 |
| Multi-tenancy isolation | TBD via Phase 3 ADR (Postgres schema-per-tenant vs row-level security vs FastAPI-instance-per-tenant) | Real multi-tenancy in scope as of 2026-06-02 |
| Synthetic load testing | TBD via Phase 3 ADR (k6 vs Locust) | Latency SLO must be measured, not assumed |
| Frontend | Next.js + TypeScript + Tailwind | Real client against the API; makes ML-engineering work visible as a portfolio surface |
| Monitoring | Prometheus + Grafana + Evidently | System + ML-specific signals |
| Containers | Docker, docker-compose | Already fluent |
| CI/CD | GitHub Actions | Already fluent |
| Orchestration runtime | Local first; k3s/kind optional after Phase 4 | Don't let K8s block progress |

## Phased plan

Each phase earns a specific set of mid-level muscles. Don't skip ahead — the lessons compound.

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
- Frontend surface (Phase 3 baseline): user selector → top-K poster grid + watch history view
- TMDB integration via MovieLens `links.csv` → `tmdbId`; the API key lives server-side, proxied through FastAPI

**Real auth (new):**
- ADR: auth provider choice (Auth0 vs Keycloak self-hosted vs Postgres-backed JWT). Decision turns on: ease of multi-tenancy mapping, ability to rotate keys cleanly, local-dev story, and how much of the work is reusable when Phase 6's A/B routing layer comes online
- Auth middleware on FastAPI — every endpoint except `/healthz` requires a valid token
- Token claims include tenant id; downstream code never sees a request without a resolved tenant
- Frontend authenticates via the same provider; a dev/portfolio impersonation mode is gated behind an explicit flag and *never* enabled in production builds
- Audit log table — every authenticated request emits a row (`tenant_id`, `user_id`, `endpoint`, `model_version`, `latency_ms`, `outcome`)

**Multi-tenancy (new):**
- ADR: isolation mechanism (Postgres schema-per-tenant vs row-level security vs FastAPI-instance-per-tenant). Decision turns on: query complexity, blast radius of a bug, operational overhead, and whether tenants share a model registry or have their own
- Tenant router in `src/serving/tenancy/` — resolves `tenant_id` (from auth claim) to (a) the champion model version for that tenant, (b) the Redis key prefix for online features, (c) the per-tenant rate limit
- Tenant configuration in Postgres — one row per tenant, columns include API quotas, current champion model versions per stage, A/B bucketing seed
- Cross-tenant leakage test in CI — synthetic-data integration test that authenticates as tenant A, fires every endpoint, and asserts no response payload contains tenant B's data

**Synthetic-user harness (new, primary scope):**
- `synthetic/load/` — k6 or Locust scripts (ADR'd) that drive realistic concurrent traffic against the API and produce p99/p95/p50 reports. CI runs a small-scale version on every PR that touches `src/serving/`; nightly runs a larger one
- `synthetic/cold_start/` — generator for programmatically created user profiles with controlled history sizes (0, 1, 3, 10 interactions) across the genre distribution. Output flows into the eval harness as an additional slice so cold-start recall has its own metric line in MLflow
- `synthetic/personas/` — handcrafted demo users for portfolio walkthroughs (action fan, drama fan, eclectic, etc.). Loaded into a `demo` tenant
- (Deferred to later phases) `synthetic/drift/` for Phase 5, `synthetic/ab_fixtures/` for Phase 6

**Multi-environment infra (new):**
- `docker-compose.dev.yml`, `docker-compose.staging.yml`, `docker-compose.prod.yml` — distinct compose stacks per environment, with environment-specific configs (smaller dataset snapshot in dev, full in staging/prod; auth-bypass disabled in everything except dev)
- A Makefile target per environment (`make up-dev`, `make up-staging`)
- Environment-aware `Settings` in `src/config.py` — runtime asserts that production builds never have dev flags set

**ADRs that gate Phase 3 work (each in its own bundled PR with the code it justifies):**
- ADR 0006 — Two-tower retrieval architecture (history-based encoder, sampled softmax, FAISS) — *bundled with Phase 2's two-tower PR* (Phase 2 step #4, sits at the Phase 2/3 boundary because it pins FAISS as the ANN library that Phase 3 serving will inherit)
- ADR for auth provider choice (Auth0 vs Keycloak vs Postgres-backed JWT)
- ADR for multi-tenancy isolation mechanism (Postgres schema vs row-level security vs FastAPI-instance-per-tenant)
- ADR for synthetic-load tool (k6 vs Locust)
- ADR for Feast vs alternatives (custom Postgres views, hand-rolled key-value loader)
- ADR for cold-start coverage methodology (how synthetic cold users are generated and what they prove)

Numbering for the unnumbered ADRs above is assigned at write time, in roughly the order they land. ADRs are namespaced — backend ADRs use the flat top-level numeric line at `docs/adr/`, frontend ADRs use their own line under `docs/adr/frontend/`.

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
4. **Latency SLO.** p99 < 100ms. **Measured under synthetic load**, not assumed. The Phase 3 synthetic-load harness runs in CI on every serving PR.
5. **Reproducibility test.** `make train` on a fixed seed and dataset version produces the same model artifact hash. If it doesn't, something is nondeterministic — find it.
6. **ADRs (architecture decision records).** A `docs/adr/` folder explaining *why* I chose each major piece. Two namespaces: backend ADRs at the top level (flat numeric line), frontend ADRs under `docs/adr/frontend/`. One ADR per significant decision; ADRs are substantive (closer to 150 lines than 50) and explore alternatives, consequences, and "how we'd know we're wrong" rather than reading like checkboxes.
7. **Evaluation gate before promotion.** A model never goes to production without beating the incumbent on a holdout — automated, not eyeballed. Per-tenant gates are scoped per tenant.
8. **Logged predictions and features.** Every online prediction logged with the features used, the tenant, the model version, and the latency, so we can replay, debug, compute online metrics, and audit per-tenant behavior later.
9. **Tenant isolation.** No code path may return one tenant's data in response to another tenant's request. Cross-tenant leakage is the highest-severity bug class. An automated CI integration test exercises every endpoint as tenant A and asserts no tenant B data surfaces.
10. **Auth on every endpoint except `/healthz`.** No "internal" unauthenticated paths. Dev-mode bypass exists for local development only and is asserted off in staging/prod builds.
11. **Synthetic-load smoke test in CI.** Every PR that touches `src/serving/` runs a short synthetic-load script and fails if p99 exceeds the SLO threshold on a defined baseline workload.

## Repo structure (target)

```
movielens-recsys/
├── CLAUDE.md                  # this file
├── README.md
├── Makefile                   # train, serve, test, lint, env up/down, etc.
├── docker-compose.yml         # default (dev)
├── docker-compose.staging.yml # Phase 3+
├── docker-compose.prod.yml    # Phase 3+
├── .github/workflows/         # CI: lint, test, model tests, feature parity, synthetic-load smoke
├── docs/
│   ├── adr/                   # backend ADRs (flat numeric line) + cross-cutting
│   │   └── frontend/          # frontend ADRs (own numeric line)
│   ├── eda.md
│   └── progress.md            # session-level progress log (frontend agent's; not authoritative)
├── data/                      # DVC-tracked
├── src/
│   ├── data/                  # ingestion, splits, schemas
│   ├── features/              # feature definitions (Feast in Phase 3)
│   ├── models/
│   │   ├── candidates/        # candidate generator(s)
│   │   └── ranker/            # ranker(s)
│   ├── training/              # training pipelines
│   ├── evaluation/            # offline metrics, evaluation gate
│   ├── auth/                  # Phase 3 — auth middleware, token validation, claim resolution
│   ├── serving/
│   │   ├── app.py             # FastAPI entrypoint
│   │   ├── tenancy/           # Phase 3 — tenant router, per-tenant config resolution
│   │   ├── routing/           # Phase 6 — champion/challenger split, shadow routing
│   │   └── audit/             # Phase 3 — audit log writer
│   └── monitoring/            # drift, dashboards
├── pipelines/                 # Prefect flows
├── synthetic/                 # Phase 3+ — synthetic-user harnesses (scoped per job)
│   ├── load/                  # k6 / Locust scripts
│   ├── cold_start/            # programmatic new-user generation
│   ├── personas/              # handcrafted demo users
│   ├── drift/                 # Phase 5
│   └── ab_fixtures/           # Phase 6
├── web/                       # Next.js + TS + Tailwind frontend
│   ├── app/                   # routes (Next.js App Router)
│   ├── components/
│   ├── lib/                   # API client, types
│   └── public/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── feature_parity/        # offline/online consistency
│   └── tenant_isolation/      # Phase 3 — cross-tenant leakage canaries
└── infra/                     # docker, postgres init, mlflow image, terraform if/when added
```

## Conventions

- **Python:** 3.11+, type hints everywhere, ruff for lint, black for format, mypy in CI on `src/`.
- **TypeScript:** 5+, strict mode, no implicit `any`, ESLint for lint, Prettier for format, `tsc --noEmit` in CI on `web/`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). Do **not** add `Co-authored-by` trailers, "Generated with Claude Code" footers, or any attribution to Claude / Claude Code / any AI tool in commit messages, PR descriptions, code comments, docstrings, or the README. All commits are authored solely by me.
- **Branches:** trunk-based, short-lived feature branches, PRs to main. Every piece of work — no matter how small — goes on a feature branch and merges via PR. No direct pushes to `main`.
- **GitHub:** repo is **private**. Branch protection on `main` (PRs required, CI must pass, no direct pushes). Default to squash merges. MIT license. README states what the project is, the stack, and the current phase.
- **Branch naming:** `feat/<short-description>`, `fix/<short-description>`, `docs/<short-description>`, `chore/<short-description>`. Keep branches short-lived; delete after merge.
- **PR discipline:** small and reviewable, one coherent unit per PR. Bundle related work (an ADR with the code it justifies, code + the CLAUDE.md status update it triggers, multiple closely-related small docs) rather than splitting on every micro-concern — see "How to work with Claude Code" for the longer version. PR description explains *why*, not just what. Never merge a PR with failing CI.
- **No AI attribution anywhere.** No mention of Claude, Claude Code, or any AI tool in: commit messages, PR titles, PR descriptions, code comments, docstrings, ADRs, the README, or any other file in the repo. All work is attributed solely to me.
- **Comments:** natural and human-like. Write the kind of comment a thoughtful senior engineer would leave — explain the *why* when it's not obvious, not the *what*. Don't over-comment mechanical code. Don't use aggressive or robotic phrasing.
- **Testing:** pytest. Every model module has tests. Feature parity tests run in CI.
- **Logging:** structured (JSON), same approach as the Incident Platform.
- **Config:** pydantic-settings, env vars for secrets, no hardcoded paths.

## Current status

**Updated 2026-06-02.** Phase 1 complete, Phase 2 well underway, scope shifted to enterprise-grade for Phase 3+. The current concrete step (the one to take next) is at the bottom of this section.

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

### Phase 2 — in progress

Two-stage architecture (offline). The top-level choice is pinned by ADR 0003. Status:

- ✅ **ADR 0004 (item-item before two-tower)** — merged (PR #18). Pins item-item as the zero-learned-parameters baseline the two-tower has to beat.
- 📦 **ADR 0005 (LightGBM over neural ranker)** — drafted on `docs/adr-0005-lightgbm-over-neural-ranker` and parked locally. Bundles with the LightGBM ranker code when that PR lands (Phase 2 step #6).
- ✅ **Item-item similarity candidate generator** (`src/models/candidates/itemitem.py`, PR #19) — `implicit.nearest_neighbours.CosineRecommender` with `k_neighbors=200`, same embedded popularity fallback CFModel established. Runs land in the new MLflow experiment `phase-2-candidates`.
- ✅ **Per-stage evaluation in the harness** (PR #19) — `src/evaluation/protocol.py` exposes `K_CANDIDATES = 500` and an optional `k` parameter on `evaluate()`. `EvalResult.k` is stamped on every result so downstream consumers can't confuse a candidate-stage `recall@500` with a recommender-end-to-end `recall@10`.
- 🚧 **Two-tower candidate generator** (PyTorch) — **current step** (see below). Design locked: history-based user tower (mean-pool over last N=50 items, no per-user-id embedding), id-only item tower, embedding dim 64, sampled softmax with log-uniform negative correction (Yi et al. 2019), FAISS-CPU IVF-Flat ANN index over cosine-normalized item embeddings, embedded popularity fallback for zero-history users. Ships with **ADR 0006 — Two-tower retrieval architecture** in the same PR.
- ⬜ **Feature module** (`src/features/`) — engineered features used by the ranker (user history aggregates, popularity windows, genre affinities, recency). Provisional home until Phase 3 introduces Feast as the feature store.
- ⬜ **LightGBM ranker** (`src/models/ranker/lgbm.py`) — scores the surviving candidates; scored against NDCG@10 per ADR 0001. ADR 0005 ships in the same PR.

Phase 2 stays all-offline — no FastAPI, no Redis online store, no Feast yet. Those land in Phase 3.

### Phase 3+ — re-scoped 2026-06-02

The scope shift to enterprise-grade lands here, not in Phase 2. See the "Phase 3" section above for the expanded plan. Phase 1 and Phase 2 work is unaffected by the shift — both remain pure offline ML.

### Current step

**Build the two-tower candidate generator with ADR 0006.** Next backend PR is `feat/twotower-candidate-generator` off main, containing:
- `docs/adr/0006-two-tower-retrieval-architecture.md` — substantive ADR covering history-based user tower, sampled softmax + log-uniform correction, FAISS, embedding dim, history window, mean-pool over attention, with Risks / Open Questions sections per ADR-size standard.
- `src/models/candidates/twotower.py` — model + ANN index integration.
- `src/training/twotower.py` — PyTorch training loop with the negative-sampling math, MLflow logging into `phase-2-candidates`.
- `tests/unit/test_twotower.py` — contract tests + a converges-on-synthetic smoke test + a point-in-time-correctness canary for the history-masking logic.
- `Makefile` — `train-twotower` target.
- `pyproject.toml` — add `torch`, `faiss-cpu` dependencies.
- `CLAUDE.md` — Phase 2 status bump when two-tower closes.

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
