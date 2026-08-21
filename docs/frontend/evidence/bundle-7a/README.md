# Bundle 7A finish-gate evidence

The named screenshot matrix for the [finish-gate review](../../finish-gate-review.md),
captured at 390x844, 768x1024, and 1440x1000.

Every capture is labelled with its provenance, because the two kinds are not
interchangeable. A **service-backed** capture is what the seeded Compose stack
produced on its own with `DEV_AUTH_BYPASS=false` — real Keycloak, real FastAPI,
real RLS, real catalog metadata, the feature and model servers, and the web
BFF. An **injected** capture is a state a healthy stack cannot be asked to hold
still in: a load in flight, an empty ranked set, a dead upstream read, a failed
poster. Those come from the isolated fixture harness, which mounts the shipped
components against recorded data through
[the fixture gate](../../../../web/lib/resources/fixture-gate.ts) — the module
that throws rather than returning data whenever fixture mode is off.

No capture is a mock-up. Both modes render the components the product ships.

## Capture commands

Service-backed, against the seeded demo stack:

```bash
make demo-up
make demo-seed
cd web
MODE=service MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7a
```

Injected, against the isolated harness:

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3113
# In a second terminal:
MODE=fixture MOVIELENS_UI_PORT=3113 npm run evidence:bundle7a
```

## Matrix

| State | Files | Provenance | What it shows |
|---|---|---|---|
| Discover — learned | `discover-learned-{mobile,tablet,desktop}` | Service-backed | Drama Fan's ranked set with the policy label the response reported |
| Discover — cold-start fallback | `discover-fallback-{mobile,tablet,desktop}` | Service-backed | Cold Start below the five-signal threshold, on `Popular while we learn`, with the routing rule stated |
| Discover — loading | `discover-loading-{mobile,tablet,desktop}` | Injected (`?demo=loading`) | Reserved poster dimensions and a polite announcement, with no alert claiming a failure |
| Discover — empty | `discover-empty-{mobile,tablet,desktop}` | Injected (`?demo=empty`) | An empty ranked set as a way forward — Browse and Quick Picks — rather than an error |
| Discover — upstream error | `discover-upstream-error-{mobile,tablet,desktop}` | Injected (`?demo=recommendations-error`) | The recommendation region failed on its own terms; watch history beside it is still readable |
| Discover — poster error | `discover-poster-error-{mobile,tablet,desktop}` | Injected (`?demo=poster-failure`) | The deterministic artwork fallback keeps the movie's identity and does not move the layout |
| Library — populated | `library-populated-{mobile,tablet,desktop}` | Service-backed | Action Fan's Rated collection, tab counts, and the live-ratings summary |
| Library — empty | `library-empty-{mobile,tablet,desktop}` | Service-backed | Cold Start's empty Watchlist with its exit to Browse |
| Movie detail | `movie-detail-{mobile,tablet,desktop}` | Service-backed | The catalog record, its provenance eyebrow, and the converged state controls |
| Quick Picks | `quick-picks-{mobile,tablet,desktop}` | Service-backed | One decision at a time, equal-weight buttons, and progress toward five watched signals |
| Auth required | `auth-required-{mobile,tablet,desktop}` | Service-backed | The signed-out door, which is the only thing an unauthenticated visitor can reach |
| Landing after sign-in | `landing-after-sign-in-{mobile,tablet,desktop}` | Service-backed | Not part of the named matrix. The first screen a signed-in viewer meets today, recorded because the five-second test needs it. See the review's product-legibility finding. |

## Automated results this evidence sits next to

- `web/e2e/finish-gate.spec.ts` — the visual and accessibility gate: zero
  critical or serious axe violations on every state above at all three widths,
  one `main` and uniquely named navigations, a heading outline with no skipped
  level, no horizontal overflow at the project width or at 320px, forced-colours
  usability, keyboard reach and operation of the core Discover actions, visible
  focus, semantic state text, the poster alternative-text policy, 44x44 mobile
  targets, and reduced motion. It runs in the `frontend` CI job through
  `npm run test:e2e:ui`.
- `web/tests/e2e/finish-gate-journey.spec.ts` — the service-backed ten-step
  journey, run against the seeded stack with `DEV_AUTH_BYPASS=false` in the
  `browser-auth-e2e` CI job.
- Earlier per-bundle evidence remains valid for the surfaces those bundles own;
  this directory does not replace it.
