# MovieLens Two-Stage Recommender — CLAUDE.md

## Purpose of this project

This is a portfolio-grade applied ML project. The goal is **not** to maximize NDCG on MovieLens — it's to force me to confront the technologies and scenarios a mid-to-senior ML engineer actually deals with on the job at an enterprise. Modeling is the easy part; the engineering around the model is the point.

This is the ML counterpart to my Incident & Workflow Platform project, which served the same purpose for backend/infra. Same philosophy: build something substantial enough that production concerns force themselves on you, end-to-end, and use it as the vehicle to learn the stack rather than learning each tool in isolation.

The output I care about: I should be able to defend every architectural choice in a senior-level design review, debug any layer when it breaks, and articulate the tradeoffs vs. alternatives.

## What the system does

A two-stage movie recommender service:

- A user ID comes in.
- A **candidate generator** retrieves ~500 candidates from a precomputed index (item-item similarity or two-tower embeddings).
- A **ranker** (LightGBM) scores those candidates using features pulled from a feature store.
- The service returns top-K recommendations in <100ms (p99 SLO).

Around the online path: an offline training pipeline orchestrated by Prefect, a model registry with a promotion gate, monitoring for system and model metrics, drift detection, and an A/B / shadow-deploy framework.

A **Next.js frontend** consumes the API. It is not an end-user product — it's a portfolio surface that makes the ML-engineering work visible. The frontend impersonates MovieLens user IDs (no real auth) and exposes the surfaces that exercise the system's interesting parts: feature-attribution panels, model/version selection, and a champion-vs-challenger comparison view. Catalog search, real auth, and admin dashboards are explicit non-goals (Grafana owns admin views).

## Dataset

MovieLens 25M (move to 32M if needed). Real ratings, real timestamps, real cold-start, well-documented. Loaded into Postgres as the source of truth.

## Architecture

**Offline path:**
raw data → feature engineering → feature store (offline) → training pipeline → model registry → evaluation gate → promotion

**Online path:**
request → candidate generator → feature store (online, Redis-backed) → ranker → top-K → response (+ logging of features and predictions)

**Surrounding systems:**
- Prefect orchestrates retraining DAGs
- MLflow tracks experiments and hosts the model registry
- Prometheus + Grafana for system metrics; Evidently for drift
- A/B routing layer for champion/challenger and shadow deploys
- GitHub Actions for CI/CD including model tests
- Next.js frontend as the demo/portfolio surface against the API

**Frontend path:**
browser → Next.js app → FastAPI (recommendations, features, model metadata) → response renderer. Movie posters are fetched from TMDB, keyed via MovieLens `links.csv` (`movieId` → `tmdbId`); the TMDB call is proxied through the FastAPI backend so the API key stays server-side.

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

### Phase 3 — Feature store and serving
- Introduce Feast
- Define feature views; materialize offline features for training
- Set up online features in Redis
- Build the FastAPI service end-to-end
- Containerize the full stack
- **Feature parity test in CI** (offline-computed feature matches online-served feature for same key) — non-negotiable
- Bootstrap the Next.js + TypeScript + Tailwind app alongside the API
- Frontend surface (Phase 3 baseline): user selector → top-K poster grid + watch history view
- TMDB integration via MovieLens `links.csv` → `tmdbId`; the API key lives server-side, proxied through FastAPI
- CORS policy and a simple API-key gate (the API is no longer internal-only)
- ADR: "why Next.js" (alternatives considered: Streamlit, plain React+Vite)

**Lessons:** online/offline skew, feature freshness, feature store as source of truth, latency budgets per stage, designing an API against a real client (not a hypothetical one).

### Phase 4 — Orchestration and retraining
- Prefect DAGs for: feature materialization, training, evaluation, registry promotion
- **Evaluation gate:** a new model only gets promoted if it beats the current champion on holdout metrics by a defined threshold
- Idempotent pipelines
- Frontend surface: "why this recommendation?" panel — top contributing features per item from LightGBM, plus a model/version selector for debugging

**Lessons:** workflow orchestration, model promotion logic, what production retraining actually means, exposing explainability through the API.

### Phase 5 — Monitoring and drift
- Prometheus + Grafana for system metrics (latency, throughput, error rate)
- Evidently for data drift and prediction drift
- Log features and predictions to a table; dashboard feature distributions over time
- **Simulate drift** by perturbing input data; verify alerts fire
- Frontend surface: lightweight drift indicator on the recs page (e.g. "model health: ok / degraded"). Real monitoring stays in Grafana; this is just a visible signal that the system *has* drift detection.

**Lessons:** what to monitor for ML systems specifically, drift vs. performance degradation, the alerting feedback loop.

### Phase 6 — A/B testing and shadow deploys
- Routing layer to split traffic between champion and challenger
- Shadow mode (challenger sees traffic; predictions logged but not shipped)
- Offline analysis comparing champion vs. challenger
- Significance testing for online experiments
- Frontend surface (the centerpiece): champion vs. challenger side-by-side view — same user, two columns of top-K recs, with diff highlighting and an experiment-summary panel

**Lessons:** champion/challenger, shadow deploys, statistical significance, why offline NDCG and online CTR don't match.

## Non-negotiables (what makes this not a toy)

These are the things I'll hold the project to. Every one of them maps to a real production concern.

1. **Time-respecting splits.** No random splits on temporal data. Ever.
2. **Feature parity test in CI.** A test that proves a feature computed offline matches the same feature served online for the same user/item. This catches the single bug that ruins most real recsys deployments.
3. **Cold-start handling.** Explicit answers for new users (no history) and new movies (no interactions).
4. **Latency SLO.** p99 < 100ms. Measured, not assumed.
5. **Reproducibility test.** `make train` on a fixed seed and dataset version produces the same model artifact hash. If it doesn't, something is nondeterministic — find it.
6. **ADRs (architecture decision records).** A `docs/adr/` folder explaining *why* I chose each major piece (Feast over custom, two-stage over single-stage, LightGBM over neural ranker, Prefect over Airflow, etc.). One ADR per significant decision.
7. **Evaluation gate before promotion.** A model never goes to production without beating the incumbent on a holdout — automated, not eyeballed.
8. **Logged predictions and features.** Every online prediction logged with the features used, so we can replay, debug, and compute online metrics later.

## Repo structure (target)

```
movielens-recsys/
├── CLAUDE.md                  # this file
├── README.md
├── Makefile                   # train, serve, test, lint, etc.
├── docker-compose.yml
├── .github/workflows/         # CI: lint, test, model tests, feature parity
├── docs/
│   └── adr/                   # architecture decision records
├── data/                      # DVC-tracked
├── src/
│   ├── data/                  # ingestion, splits, schemas
│   ├── features/              # feature definitions (Feast)
│   ├── models/
│   │   ├── candidates/        # candidate generator(s)
│   │   └── ranker/            # ranker(s)
│   ├── training/              # training pipelines
│   ├── evaluation/            # offline metrics, evaluation gate
│   ├── serving/               # FastAPI app, routing, shadow logic
│   └── monitoring/            # drift, dashboards
├── pipelines/                 # Prefect flows
├── web/                       # Next.js + TS + Tailwind frontend
│   ├── app/                   # routes (Next.js App Router)
│   ├── components/
│   ├── lib/                   # API client, types
│   └── public/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── feature_parity/        # offline/online consistency
└── infra/                     # docker, terraform if/when added
```

## Conventions

- **Python:** 3.11+, type hints everywhere, ruff for lint, black for format, mypy in CI on `src/`.
- **TypeScript:** 5+, strict mode, no implicit `any`, ESLint for lint, Prettier for format, `tsc --noEmit` in CI on `web/`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). Do **not** add `Co-authored-by` trailers, "Generated with Claude Code" footers, or any attribution to Claude / Claude Code / any AI tool in commit messages, PR descriptions, code comments, docstrings, or the README. All commits are authored solely by me.
- **Branches:** trunk-based, short-lived feature branches, PRs to main. Every piece of work — no matter how small — goes on a feature branch and merges via PR. No direct pushes to `main`.
- **GitHub:** repo is **private**. Branch protection on `main` (PRs required, CI must pass, no direct pushes). Default to squash merges. MIT license. README states what the project is, the stack, and the current phase.
- **Branch naming:** `feat/<short-description>`, `fix/<short-description>`, `docs/<short-description>`, `chore/<short-description>`. Keep branches short-lived; delete after merge.
- **PR discipline:** one concern per PR. Small and reviewable. PR description explains *why*, not just what. Never merge a PR with failing CI.
- **No AI attribution anywhere.** No mention of Claude, Claude Code, or any AI tool in: commit messages, PR titles, PR descriptions, code comments, docstrings, ADRs, the README, or any other file in the repo. All work is attributed solely to me.
- **Comments:** natural and human-like. Write the kind of comment a thoughtful senior engineer would leave — explain the *why* when it's not obvious, not the *what*. Don't over-comment mechanical code. Don't use aggressive or robotic phrasing.
- **Testing:** pytest. Every model module has tests. Feature parity tests run in CI.
- **Logging:** structured (JSON), same approach as the Incident Platform.
- **Config:** pydantic-settings, env vars for secrets, no hardcoded paths.

## Current status

**Phase 1 — complete.** Baselines, data foundation, and the evaluation harness all landed:

- ADR 0001 (evaluation protocol) and ADR 0002 (implicit-feedback labeling) pin the contracts every model trains and is scored against.
- `src/evaluation/` is the single source of truth for metrics — warm/cold user slicing per ADR 0001, used by every model run; no ad-hoc metric computation anywhere else (non-negotiable #5).
- MovieLens 25M ingested into Postgres (`movielens` DB, 25 000 095 ratings) and versioned with DVC. Stack runs via docker-compose: Postgres, Redis, MLflow (psycopg2-enabled), Prometheus, Grafana.
- Temporal train/holdout/test split (`src/data/split.py`) implementing ADR 0001's `T = percentile_disc(0.8)` cutoff. Train hits exactly 80.00% of rows; holdout = 28 days × 129 683 interactions × 2 641 users (~26.6% cold-start).
- EDA writeup in `docs/eda.md` (2026-05-31 snapshot) characterizes scale, sparsity, rating distribution, item popularity tail, the temporal split as applied to real data, and cold-start sizing.
- Popularity baseline (`PopularityModel`, PR #12) — first MLflow run logged into experiment `phase-1-baselines`.
- CF/ALS baseline (`CFModel` via `implicit`, PR #14) — second run in the same experiment; embeds popularity fallback for cold users per ADR 0001.

**Phase 2 — starting.** Two-stage architecture (offline). The top-level choice is pinned by ADR 0003. Next concrete steps:

1. **ADR 0004 (item-item before two-tower)** — pin the candidate-stage progression before code.
2. **ADR 0005 (LightGBM over neural ranker)** — pin the ranker choice before code.
3. **Item-item similarity candidate generator** (`src/models/candidates/itemitem.py`) — third candidate model alongside popularity and CF; trained, evaluated, and logged through the same skeleton so all three are comparable side-by-side in MLflow.
4. **Two-tower candidate generator** (PyTorch) — the candidate-stage upgrade; learned user/item embeddings, ANN index for retrieval.
5. **Feature module** (`src/features/`) — engineered features used by the ranker (user history aggregates, popularity windows, genre affinities, recency). Provisional home until Phase 3 introduces Feast as the feature store.
6. **LightGBM ranker** (`src/models/ranker/lgbm.py`) — scores the surviving candidates; scored against NDCG@10 per ADR 0001.
7. **Per-stage evaluation in the harness** — recall@K_candidates for the candidate stage, NDCG@10 for the ranker stage, in the same run, so each stage's contribution is legible.

Phase 2 stays all-offline — no FastAPI, no Redis online store, no Feast yet. Those land in Phase 3.

## How to work with Claude Code on this

- Default to small, reviewable PRs. One concern per PR.
- When introducing a new technology (Feast, Prefect, Evidently, etc.), include a short ADR in the same PR explaining the choice and the alternatives considered.
- Before writing code for a new phase, re-read the relevant phase section above and confirm scope.
- Don't skip the non-negotiables to save time. They are the project.
- When in doubt about a design choice, ask me — the explanation is the point of the project.
- **Write ADR 0001 (evaluation protocol) before any model code.** No exceptions.
- **Watch for leakage in feature engineering.** Any feature that uses future information silently inflates offline metrics. Point-in-time correctness is the standard — features must only use data available at the time of prediction.
- **Get an end-to-end path working early.** Even a janky popularity baseline served via FastAPI is more valuable than a perfect offline model with no serving layer. Discover serving assumptions early.
- **Never compute metrics ad-hoc in notebooks.** Every model run goes through `src/evaluation/`. This is how the protocol stays honest across weeks of work.
