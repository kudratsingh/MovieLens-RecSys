# Bundle 4 frontend system

**Status:** Implemented against typed recorded contract fixtures

**Last updated:** 2026-08-21

## Boundary

Bundle 4 establishes the reusable frontend language and route ownership. The
durable Library and scalable catalog contracts are now real in the live
Bundle 2–3 routes; Bundle 4 deliberately does not replace them before the
Bundle 5 integration slice. Fixture-backed state controls announce `Preview
only`, the catalog labels its recorded source, and technical evidence names
itself as a recorded contract fixture.

The authenticated Phase 3 dashboard remains at `/` and `/legacy`. The visual
system is reviewable under authenticated `/ui-preview/*` routes. A
development-only `MOVIELENS_UI_FIXTURE_MODE=1` switch exists solely for
isolated screenshot and responsive-test harnesses and is ignored in production.

## Route and rendering ownership

| Route | Server-owned shell | Interactive client leaves | Independent resource |
|---|---|---|---|
| `/ui-preview/discover` | fixture resolution, first-pick hierarchy | poster failure, state controls, rail, evidence drawer | recommendations, evidence |
| `/ui-preview/browse` | catalog resource boundary | search, sort, filters/sheet, poster grid | catalog |
| `/ui-preview/library` | selected fixture collection | collection tabs and poster states | library |
| `/ui-preview/movies/[movieId]` | movie lookup and metadata | poster failure, state controls, rating, evidence drawer | detail fixture |
| `/ui-preview/quick-picks` | queue item selection | equal button and keyboard preview paths | queue fixture |

Pages and layouts remain Server Components by default. Client boundaries are
placed at navigation highlighting, image-error recovery, local controls,
drawers, filter/tab state, and keyboard behavior.

## Visual contract

Semantic CSS variables in `web/app/globals.css` cover:

- canvas, base, raised, overlay, and inverse surfaces;
- primary, secondary, muted, and inverse text;
- accent and high-visibility focus;
- success, warning, destructive, and degraded state;
- poster fallback and overlay; and
- 4px-derived spacing, typography roles, radii, shadows, motion, and easing.

Dark-first is intentional. Every interactive control receives a visible
`:focus-visible` ring. Primary mobile targets are at least 44 CSS pixels.
Forced-color and reduced-motion media queries preserve meaning and operation.

## Independent failure contract

Recorded resources return the discriminated `ResourceResult<T>` union. Expected
failures render within the owning resource instead of throwing away the page.
Reviewers can exercise the contract without a backend:

```text
/ui-preview/discover?fail=recommendations
/ui-preview/discover?fail=evidence
/ui-preview/browse?fail=catalog
/ui-preview/library?fail=library
```

This is scaffolding for the later visual integration of independent BFF
requests. The live routes retain the real Auth.js server session, BFF token
attachment, Origin/CSRF enforcement, and canonical Bundle 2–3 resources. No
browser request forwards an `Authorization` header.

## Verification

From `web/`:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e:ui
```

Vitest covers poster fallback and image failure, keyboard-operable state,
drawer focus/Escape restoration, independent resource failure, tab semantics,
and axe checks. Playwright renders all five route shells at 390×844, 768×1024, and
1440×1000, asserts their movie-first headings and named navigation, checks page
overflow, and verifies evidence failure isolation.

Visual evidence and its capture instructions live in
[`evidence/bundle-4`](evidence/bundle-4/README.md).
