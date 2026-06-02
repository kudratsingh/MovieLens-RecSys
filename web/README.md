# web — MovieLens Recsys Frontend

Next.js 16 + TypeScript + Tailwind v4. The portfolio surface that consumes the recommendation API and makes the ML-engineering work visible (explainability, model versioning, champion-vs-challenger comparison).

See [ADR 0001](../docs/adr/frontend/0001-frontend-framework.md) for the framework choice and [CLAUDE.md](../CLAUDE.md) for the broader plan.

## Local development

```bash
cd web
npm install
npm run dev       # http://localhost:3001 (3000 is Grafana)
npm run build
npm run lint
npm run typecheck
```

## Status

PR 1: scaffold + ADR (this PR). No real surfaces yet.
Next: Makefile + CI integration, then the Phase 3 baseline UI (user selector → poster grid).
