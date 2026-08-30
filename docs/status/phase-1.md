# Phase 1 — Baseline and data foundation (complete)

> Moved out of `CLAUDE.md` on 2026-08-30 so the instruction file stays short; this folder is the full ledger and `CLAUDE.md`'s "Current status" is its summary. Update both when something lands: the ledger with the detail, the summary only when the shape of the project changes.

Baselines, data foundation, and the evaluation harness all landed:

- ADR 0001 (evaluation protocol) and ADR 0002 (implicit-feedback labeling) pin the contracts every model trains and is scored against.
- `src/evaluation/` is the single source of truth for metrics — warm/cold user slicing per ADR 0001, used by every model run; no ad-hoc metric computation anywhere else (non-negotiable #5).
- MovieLens 25M ingested into Postgres (`movielens` DB, 25 000 095 ratings) and versioned with DVC. Stack runs via docker-compose: Postgres, Redis, MLflow (psycopg2-enabled), Prometheus, Grafana.
- Temporal train/holdout/test split (`src/data/split.py`) implementing ADR 0001's `T = percentile_disc(0.8)` cutoff. Train hits exactly 80.00% of rows; holdout = 28 days × 129 683 interactions × 2 641 users (~26.6% cold-start).
- EDA writeup in `docs/eda.md` (2026-05-31 snapshot) characterizes scale, sparsity, rating distribution, item popularity tail, the temporal split as applied to real data, and cold-start sizing.
- Popularity baseline (`PopularityModel`, PR #12) — first MLflow run logged into experiment `phase-1-baselines`.
- CF/ALS baseline (`CFModel` via `implicit`, PR #14) — second run in the same experiment; embeds popularity fallback for cold users per ADR 0001.
- Per-policy attribution metrics (PR #17) — `CFModel.was_served_by_als(user_id)` predicate + per-policy MLflow metrics partition holdout by ALS-served vs popularity-fallback-served users.
