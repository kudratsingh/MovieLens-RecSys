# MovieLens Two-Stage Recommender

A production-grade two-stage movie recommendation system built on MovieLens 25M.

## What it does

1. A **candidate generator** (item-item similarity → two-tower embeddings) retrieves ~500 candidates from a precomputed index.
2. A **ranker** (LightGBM) scores those candidates using features from a feature store.
3. A **FastAPI service** returns top-K recommendations with p99 < 100ms.

## Stack

| Layer | Choice |
|---|---|
| Models | PyTorch (two-tower), LightGBM (ranker) |
| Data store | PostgreSQL |
| Data versioning | DVC |
| Feature store | Feast |
| Tracking + registry | MLflow |
| Orchestration | Prefect |
| Serving | FastAPI + Redis |
| Monitoring | Prometheus + Grafana + Evidently |
| Containers | Docker + docker-compose |
| CI/CD | GitHub Actions |

## Current phase

**Phase 1 — Baseline and data foundation**

See [CLAUDE.md](CLAUDE.md) for the full phased plan and architecture.

## Quick start

```bash
make install   # install dependencies
make lint      # ruff + mypy
make test      # pytest
make train     # run training pipeline
make serve     # start FastAPI service
```

## License

MIT
