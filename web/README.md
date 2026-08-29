# web — MovieLens Recsys Frontend

Next.js 16 + TypeScript + Tailwind v4. A poster-first movie-discovery product
that consumes the recommendation API while keeping truthful ML evidence within
deliberate reach.

See [ADR 0001](../docs/adr/frontend/0001-frontend-framework.md) for the framework
choice, [frontend ADR 0002](../docs/adr/frontend/0002-movie-discovery-experience.md)
for the product, and [`docs/frontend/`](../docs/frontend/README.md) for the
design contracts, the frontend system, and the finish gate.

## Status

Bundles 0–7 are delivered and the cutover is done: `/` serves the product to a
signed-in viewer and the sign-in door to everyone else, and the pre-redesign
dashboard is retained at `/legacy` as a documented one-file rollback. Every route
renders the one shared `AppShell`.

Authentication is a real Keycloak authorization-code + PKCE flow with an
encrypted HttpOnly server session, server-side token refresh and logout,
Origin/CSRF checks on mutations, and role-gated demo-persona access. Two
boundaries are worth knowing before changing anything:

- **`lib/resources/` is the only way a token reaches FastAPI** (Bundle 5A), and
- **`lib/movie-state/` is the only way any surface commits a write** (Bundle 7c).

Both are described below. The [finish-gate review](../docs/frontend/finish-gate-review.md)
records HOLD pending moderated participant sessions; every criterion a reviewer
can settle passes.

## Local development

```bash
make web-install
make web-dev       # http://localhost:3001 (3000 is Grafana)
make web-build
make web-lint
make web-typecheck
```

Install the pinned browser runtime once with `npx playwright install chromium`.

## Routes

| Route | What it renders |
|---|---|
| `/` | The front door. Signed out, the Keycloak sign-in door and nothing else; signed in, a redirect to the product. Carries `?next=` so a bounced deep link survives sign-in, validated against the app's own routes on the way in *and* again inside the sign-in action |
| `/discover` | The persona-scoped recommendation route: a featured movie, the ranked rail, and watch history as independent regions. `?userId=` selects the persona |
| `/browse` | The catalog: search, genre and year filters, three sorts, cursor paging. Server does session and persona work; the grid loads through the BFF as an accumulating window |
| `/movies/[movieId]` | Movie detail — backdrop hero, tagline, runtime, TMDB score, cast, click-to-load trailer, and the movie-state controls. `generateMetadata` and the body share one per-request memoised read, so a detail view costs one upstream call |
| `/library` | Rated, Watchlist and **Seen** as independent per-tab reads with URL-owned state |
| `/quick-picks` | One decision at a time: buttons, `J`/`K`/`L`/`U`, and swipes dispatch the same action |
| `/legacy` | The pre-redesign Phase 3 dashboard, kept as the cutover rollback and labelled as such. `robots: { index: false }`, in no navigation |
| `/ui-preview/{discover,browse,library,quick-picks,movies/[movieId]}` | Fixture-backed preview shells for visual review. `/ui-preview` itself has no page |

The **Seen** tab is a visible label only — `/library?tab=history` is still the
address and `history` is still the API value.

Under `app/api/` are 17 BFF route handlers. They are the browser's only path to
FastAPI: they refuse a caller-supplied bearer, mint or adopt a correlation ID,
and mark personalized responses `private, no-store`.

## Boundaries

### `lib/resources/` — the live-resource boundary

The single boundary every live product region reads through.

- `server.ts` — the server-owned client, and the only place an access token
  reaches FastAPI. Per-resource timeout, `X-Request-ID` sent and echoed, imports
  no fixture.
- `browser.ts` — the same state model for client components, over the
  same-origin BFF. Refuses a caller-supplied `Authorization` header rather than
  silently dropping one.
- `bff.ts` — route-handler helpers: credential refusal, correlation ID, and the
  resource-state-to-HTTP translation, all `private, no-store`.
- `validate.ts` / `definitions.ts` — narrow runtime guards over the generated
  OpenAPI types, plus the per-resource label, timeout and emptiness rule.
- `mapping.ts` — the outcome-to-state mapping shared by `server.ts` and
  `browser.ts`, so the same upstream 403 reads identically on both.
- `request-id.ts` — the header name and the sanitiser that decides whether an
  inbound value is adopted or replaced.
- `state.ts` — `loading`, `retry`, `ready`, `empty`, `forbidden`,
  `auth-expired`, `not-found`, `upstream-error`, rendered by
  `components/ui/resource-region.tsx` so a failed resource never blanks the
  regions around it.
- `fixture-gate.ts` — the only door recorded fixtures come through. Outside
  `MOVIELENS_UI_FIXTURE_MODE`, and always in production, asking for one throws.

### `lib/movie-state/` — the one write path

Every watched, rating, watchlist and dismissal write on every surface goes
through here. Before Bundle 7c there were copies, and they had already diverged.

- `actions.ts` — the four states and the ADR 0012 transition table, written once,
  with the optimistic projections derived from it.
- `mutate.ts` — the browser side of a canonical write: intent-scoped idempotency
  key, `expected_revision`, double-submit CSRF and same-origin, and the committed
  response treated as truth.
- `client.ts` — the seam. On a `409` it re-reads the canonical record and
  **replays the same intent with the same key**, which is why a first press on a
  title that already carries state now commits instead of being discarded.
  A `409` that is a *transition refusal* rather than a conflict is rendered
  verbatim as a note, with no retry.
- `committed-store.ts` — a tab-local relay of committed states, so a write on
  movie detail is the revision Discover's next write asserts.
- `announce.ts`, `focus.ts` — one vocabulary for what a write did, and where
  focus goes once it settles.

`components/movie/` is the matching control family — one
`movie-state-controls.tsx` with a declared, ordered control set per surface,
plus the shared `poster-card`, `movie-rail`, `movie-collection`, `rating-stars`,
`movie-detail-view`, `movie-credits` and `movie-trailer` (which issues no request
to any YouTube host until the poster frame is pressed).

`lib/api.generated.ts` is generated from `docs/api/openapi.json` by
`npm run api:types` — do not hand-edit it. `lib/api.ts` re-exports the narrow
named types the app imports.

## Tests

```bash
npm run lint
npm run typecheck
npm run api:types:check     # OpenAPI drift
npm run test                # Vitest
npm run build
npm run test:e2e:ui         # fixture-mode Playwright
npm run test:e2e            # service-backed — needs the seeded demo stack
npm run test:perf           # browser timing — needs the seeded demo stack
```

| Layer | Config | What it covers |
|---|---|---|
| Unit | `vitest.config.ts` | `tests/unit/**` **and** `components/**/*.test.tsx` — 51 unit files plus the component suites, jsdom, jest-axe |
| Fixture-mode browser | `playwright.ui.config.ts` | `e2e/` at 390/768/1440, fully parallel, its own dev server on `MOVIELENS_UI_PORT` (3104). The state matrices, the accessibility and finish-gate sweep, poster fallback, route shells, shell identity |
| Service-backed browser | `playwright.config.ts` | `tests/e2e/` against the seeded, bypass-disabled Compose stack. **`workers: 1`** — a correctness setting, not tuning: three journeys writing the same persona's revision produced 409s and cross-test flakes |
| Browser timing | `playwright.perf.config.ts` | `tests/perf/` — LCP, CLS and time-to-acknowledgement on a pinned mobile profile (390×844, DPR 3, 4× CPU throttle), plus the structural layout promises |

`npm run test:e2e` runs seven specs: `browser-auth`, `seen-journey`,
`discover-journey`, `featured-skip-journey`, `finish-gate-journey`,
`shell-and-doors`, and `persona-hygiene`. Each journey owns one persona and
reverses its own writes; `persona-hygiene` asserts the run left Cold Start at
zero positive signals, because that persona is the only proof of the fallback
path.

`scripts/` holds `check-api-types.mjs` (the drift check) and the
`capture-*-evidence.mjs` scripts behind `npm run evidence:*`, which write the
matrices indexed in [`docs/frontend/evidence/`](../docs/frontend/evidence/README.md).

## Environment variables

There is no `web/.env.example`; this table is the documentation.

| Variable | Default | Purpose |
|---|---|---|
| `RECOMMENDATION_API_URL` | `http://localhost:8000` | Server-side FastAPI origin. Never exposed to the client bundle |
| `KEYCLOAK_PUBLIC_ISSUER` | `http://localhost:8080/realms/demo` | Issuer the browser is redirected to |
| `KEYCLOAK_INTERNAL_ISSUER` | the public issuer | Container-internal discovery and token endpoint |
| `KEYCLOAK_CLIENT_ID` | `movielens-web` | The public PKCE browser client |
| `AUTH_SECRET` | — (required) | Encrypts the Auth.js session and backs the CSRF double submit |
| `APP_ORIGIN` | the request origin | Expected `Origin` for mutations |
| `MOVIELENS_UI_FIXTURE_MODE` | unset | `1` enables the fixture-backed `/ui-preview/*` harness. Requires `NODE_ENV !== "production"`; ignored otherwise |
| `MOVIELENS_UI_PORT` | `3104` | Port for `npm run test:e2e:ui`, so parallel worktrees can each run it. CI leaves it unset |
| `PLAYWRIGHT_BASE_URL` | `http://localhost:3001` | Target for the service-backed and perf suites |
| `MOVIELENS_DEMO_URL` | `http://localhost:3001` | Target for the service-backed evidence scripts |
| `EVIDENCE_BASE_URL` | per script | Target for the fixture-mode evidence scripts |
| `MODE` | `fixture` | `service` or `fixture` for `evidence:bundle7a` |
| `PERF_ENFORCE_LCP` | enforced | Set `false` to report rather than fail |
| `PERF_ENFORCE_ACK` | off | Set `true` to enforce the acknowledgement budget (advisory until runner data exists — ADR 0010) |
| `PERF_CPU_THROTTLE` | `4` | CPU throttle multiplier for the perf profile |
| `BROWSER_TIMING_OUTPUT` | `../artifacts/browser-timing/browser-timing.json` | Where the perf suite writes its report |

```bash
RECOMMENDATION_API_URL=http://api.internal:8000 make web-dev
MOVIELENS_UI_PORT=3204 npm run test:e2e:ui
```

## Driving states without a backend

```bash
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3104
# /ui-preview/discover?demo=learned | fallback | empty | loading | auth-expired
# /ui-preview/discover?demo=recommendations-error | history-error | evidence-error | poster-failure
npm run evidence:bundle5b   # writes docs/frontend/evidence/bundle-5b
```

The `demo` selector is honoured only inside `MOVIELENS_UI_FIXTURE_MODE` and never
in production; without it a route always reads live, and a failed read stays a
visible failure rather than becoming recorded data. The lockout is asserted
structurally — a unit test reads `lib/resources/server.ts`'s own source and fails
if it imports a fixture.

## One caching note

`middleware.ts` marks personalized documents `private, no-store` for `/`,
`/discover`, `/legacy`, `/library`, `/quick-picks` and `/movies/*`. Next
overwrites a `headers()` entry for dynamically rendered pages, which is why the
header is set in middleware instead — verifiable in a production build, not in
`next dev`.
