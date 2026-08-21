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
The isolated UI Playwright configuration defaults to port 3104 so it can run
beside the normal development server. `MOVIELENS_UI_FIXTURE_MODE=1` is accepted
only outside production; normal `/ui-preview/*` requests require a real Auth.js
session.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RECOMMENDATION_API_URL` | `http://localhost:8000` | Server-side FastAPI origin. Never exposed to the client bundle. |
| `KEYCLOAK_PUBLIC_ISSUER` | `http://localhost:8080/realms/demo` | Issuer the browser is redirected to. |
| `KEYCLOAK_INTERNAL_ISSUER` | public issuer | Container-internal discovery/token endpoint. |
| `KEYCLOAK_CLIENT_ID` | `movielens-web` | Public PKCE browser client. |
| `AUTH_SECRET` | — | Encrypts the Auth.js session and backs the CSRF double submit. |
| `APP_ORIGIN` | request origin | Expected `Origin` for mutations. |
| `MOVIELENS_UI_FIXTURE_MODE` | unset | `1` enables the fixture-backed `/ui-preview/*` harness. Ignored in production builds. |
| `MOVIELENS_UI_PORT` | `3104` | Port for `npm run test:e2e:ui`, so parallel worktrees can each run the UI suite. CI leaves it unset. |
| `PLAYWRIGHT_BASE_URL` | `http://localhost:3001` | Target for the service-backed browser-auth suite. |

```bash
RECOMMENDATION_API_URL=http://api.internal:8000 make web-dev
MOVIELENS_UI_PORT=3204 npm run test:e2e:ui
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

## Live-resource boundary

`lib/resources/` is the single boundary every live product region reads
through, added in Bundle 5A:

- `server.ts` — the server-owned client. It is the only place an access token
  reaches FastAPI, applies a per-resource timeout, sends and echoes
  `X-Request-ID`, and imports no fixture.
- `browser.ts` — the same state model for client components, over the
  same-origin BFF. It refuses a caller-supplied `Authorization` header rather
  than silently dropping one.
- `bff.ts` — route-handler helpers: credential refusal, correlation ID, and the
  resource-state-to-HTTP translation, all `private, no-store`.
- `validate.ts` / `definitions.ts` — narrow runtime guards over the generated
  OpenAPI types, plus the per-resource label, timeout, and emptiness rule.
- `state.ts` — `loading`, `retry`, `ready`, `empty`, `forbidden`,
  `auth-expired`, `not-found`, and `upstream-error`, rendered by
  `components/ui/resource-region.tsx`.
- `fixture-gate.ts` — the only door recorded fixtures come through. Outside
  `MOVIELENS_UI_FIXTURE_MODE`, and always in production, asking for one throws
  instead of returning data.
