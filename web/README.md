# web — MovieLens Recsys Frontend

Next.js 16 + TypeScript + Tailwind v4. The portfolio surface that consumes the recommendation API and makes the ML-engineering work visible (explainability, model versioning, champion-vs-challenger comparison).

See [ADR 0001](../docs/adr/frontend/0001-frontend-framework.md) for the framework choice and [CLAUDE.md](../CLAUDE.md) for the broader plan.

## Local development

```bash
make web-install
make web-dev       # http://localhost:3001 (3000 is Grafana)
make web-build
make web-lint
make web-typecheck
```

The server-side route handler proxies browser requests to FastAPI at
`http://localhost:8000` by default. Override that without exposing the URL to
the client bundle:

```bash
RECOMMENDATION_API_URL=http://api.internal:8000 make web-dev
```

## Status

The Phase 3 baseline UI is live: demo user selector, recommendation grid,
watch-history view, policy/model metadata, and a server-side FastAPI proxy.
TMDB poster images and Keycloak browser authentication are the next frontend
increments.
