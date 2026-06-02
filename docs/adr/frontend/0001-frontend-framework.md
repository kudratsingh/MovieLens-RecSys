# ADR 0001 — Frontend Framework

**Status:** Accepted  
**Date:** 2026-05-31

## Context

CLAUDE.md commits to shipping a frontend alongside Phase 3 to make the ML-engineering work visible — feature attribution, model versioning, champion/challenger comparison. This is a portfolio surface, not an end-user product. The app has a single deployment target (no SEO needs), no real auth (users are impersonated MovieLens IDs), and a handful of routes.

The framework choice needs pinning before scaffolding so the decision is reviewable and the alternatives are written down.

## Decisions

### Framework: Next.js (App Router)

Next.js 16 with the App Router and TypeScript.

- Server Components keep the client bundle small even as we add detail panels (feature attribution, watch history, comparison views).
- The App Router's nested layouts map naturally to the planned surfaces: a persistent header (user selector, model-version chip) wrapping per-route content.
- Built-in image optimization is non-trivial to hand-roll for a poster-heavy UI; offloading it to Next.js avoids writing our own TMDB image proxy.
- Route handlers under `app/api/*` give us a server-side place to call TMDB, keeping the API key off the client per the architecture in CLAUDE.md.
- TypeScript is a project-wide convention; Next.js's TS integration is first-class.

### Styling: Tailwind CSS v4

- The poster grids, side-by-side comparison view, and dense feature-attribution panels are utility-class territory — no value in hand-rolling CSS modules.
- Tailwind v4 ships with `@import "tailwindcss"` and CSS-first theme variables, so no `tailwind.config.js` is needed for the baseline.

### Linting: ESLint 9 (flat config)

- `eslint-config-next` for React/Next-aware rules. Prettier is added in a follow-up PR alongside CI integration to keep this PR focused on the scaffold itself.

### Dev port: 3001 (not 3000)

- `docker-compose.yml` exposes Grafana on port 3000. `next dev` defaults to 3000 and would collide.
- Both the `dev` and `start` scripts in `web/package.json` pin port `3001` so the foot-gun never reaches a developer.

## Alternatives considered

- **Streamlit.** Rejected. Fastest path to a demo, but reads as a data-science prototype rather than engineering work. No real component model, weak routing, awkward for the champion-vs-challenger layout. Exercises none of the muscles this project is built to teach.
- **Plain React + Vite.** Rejected. Lighter than Next.js but lacks built-in image optimization, server-side route handlers for the TMDB proxy, and Server Components. Would force us to reach for `react-router`, a separate API/proxy layer, and a poster-caching library — all things Next.js bundles for free.
- **Remix.** Considered. Strong story for nested layouts and forms; similar Server Components story to App Router. Tie-broken in favor of Next.js by ecosystem inertia and the portfolio signal of the more ubiquitous framework.
- **SvelteKit.** Considered briefly and rejected. Excellent framework but a learning detour without payoff for an ML-engineering portfolio project.

## Consequences

- The frontend lives in `web/` (per CLAUDE.md repo structure). Frontend branches use the `feat/frontend-*` namespace.
- Next.js's bundled `node_modules/next/dist/docs/` is the source of truth for framework-specific patterns. Next 16 has breaking changes from earlier versions; consult those docs before writing route handlers, layouts, or server actions.
- A subsequent PR will add: Makefile targets (`web-dev`, `web-build`, `web-lint`, `web-typecheck`), a `web` job in `.github/workflows/ci.yml` (typecheck, lint, build), and Prettier config.
- The API contract between the Next.js app and the FastAPI service is intentionally out of scope here. It gets its own ADR when Phase 3's serving work begins. Until then, the frontend operates against mocked responses.
