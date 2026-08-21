# web — MovieLens Recsys Frontend

Next.js 16 + TypeScript + Tailwind v4. The movie-first route system consumes
the recommendation API while keeping truthful ML evidence within deliberate
reach.

See [ADR 0001](../docs/adr/frontend/0001-frontend-framework.md) for the framework choice and [CLAUDE.md](../CLAUDE.md) for the broader plan.

## Local development

```bash
make web-install
make web-dev       # http://localhost:3001 (3000 is Grafana)
make web-build
make web-lint
make web-typecheck
```

The live authenticated routes remain `/`, `/browse`, `/library`, and
`/movies/[movieId]`. The same Phase 3 dashboard is also preserved at `/legacy`.
Bundle 4's authenticated `/ui-preview/*` namespace uses typed recorded fixtures
for visual-system review; its local state controls do not claim persistence.

Frontend checks from `web/`:

```bash
npm run api:types:check
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e:ui
npm run test:e2e # real Keycloak stack only
```

Install the pinned browser runtime once with `npx playwright install chromium`.
The isolated UI Playwright configuration uses port 3104 so it can run beside
the normal development server. `MOVIELENS_UI_FIXTURE_MODE=1` is accepted only
outside production; normal `/ui-preview/*` requests require a real Auth.js
session.

The server-side route handler proxies browser requests to FastAPI at
`http://localhost:8000` by default. Override that without exposing the URL to
the client bundle:

```bash
RECOMMENDATION_API_URL=http://api.internal:8000 make web-dev
```

## Status

The Phase 3 product uses a real Keycloak authorization-code + PKCE login,
encrypted HttpOnly server session, server-side token refresh and logout,
mutation Origin/CSRF checks, and role-gated demo-persona access. It includes
the recommendation loop, durable Rated/Watchlist/History Library, and a
searchable, filterable, cursor-paginated Browse route with local movie detail.
Grid metadata comes from FastAPI's persisted snapshot and overlays canonical
movie state, so Browse never fans out to TMDB or exposes access tokens to the
browser.

Bundle 4 adds the tested semantic visual system and an authenticated,
fixture-isolated `/ui-preview` route family for review. The live root, Browse,
Library, and movie-detail routes continue to use the real session-protected BFF
and canonical Bundle 2–3 APIs until Bundle 5 integrates the new primitives.
