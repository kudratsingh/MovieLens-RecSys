# Frontend system

**Status:** Bundle 4 visual system, plus the shared movie-state controls and
write path converged in Bundle 7 (PR 7c)

**Last updated:** 2026-08-21

This is the system of record for the parts of the frontend that more than one
route uses: the visual language, the resource state model, the movie-state
control family, and the write path behind it.

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
[`evidence/bundle-4`](evidence/bundle-4/README.md), and the re-captured
control surfaces in [`evidence/bundle-7c`](evidence/bundle-7c/README.md).

## Movie-state controls

Watched, rating, watchlist, and dismissal are the four states a viewer can
change, and every surface that offers them renders the same component family in
`web/components/movie/movie-state-controls.tsx`:

| Export | What it is |
|---|---|
| `MovieStateControls` | The watched / watchlist / dismissal row, plus the confirmation that guards removing watched history. |
| `MovieRatingControl` | The rating editor, in whole stars or the stored half-star precision. |
| `MovieStatePanel` | Movie detail's composition: the family wired to the write path, with its own status and error regions. |

Surfaces differ by *declaration*, not by forking the component. A control set is
an ordered list, because which controls a surface offers and in what order is
its documented hierarchy:

| Surface | Control set | Why |
|---|---|---|
| Discover card, featured movie | `watchlist: toggle`, `watched: final`, `dismissal: toggle` | Discover ranks unseen movies. `Watched` is a one-way statement there; undoing it is a destructive edit that belongs elsewhere. |
| Movie detail | `watchlist: toggle`, `watched: confirm`, `dismissal: toggle` | Detail is where a title is managed. `Watchlist` leads while the movie is unseen, and removing history is confirmed. |
| Library — Rated | `dismissal: undo` + half-star rating editor | Editing or clearing a star value is the collection's job. |
| Library — Watchlist | `watched: mark`, `watchlist: remove`, `dismissal: toggle` | The row leads with the action that moves the movie forward. |
| Library — History | `watched: confirm`, `dismissal: undo` + rating editor | History owns the one destructive action in the route, and `Remove rating` stays visibly different from `Remove from history`. |
| `/ui-preview` | `watchlist: toggle`, `watched: toggle`, no writes | Recorded surfaces toggle locally and announce `Preview only`. |

Three properties are load-bearing:

- **Copy is shared, voice is declared.** Announcements come from
  `lib/movie-state/announce.ts`, one table keyed by outcome and surface. A
  passing card says the short thing; a movie's own page says what a star
  commits to; the Library names whose record changed. Keeping the three voices
  in one table is what makes it obvious if one of them starts claiming more
  than the deployed system does.
- **Layout belongs to the surface.** The family renders markup and copy; a
  `classNames` prop carries the surface's own layout hooks, so a Library grid
  column is not the control family's business.
- **In-flight controls are `aria-disabled`, not `disabled`.** A disabled element
  cannot hold focus, and returning focus to the control that failed is exactly
  what a rollback has to do.

## The write path

Every watched, rating, watchlist, and dismissal change from every surface goes
through `lib/movie-state/`:

```text
control reports intent (MovieStateAction)
  -> useMovieState / a route's own orchestration
  -> MovieStateClient.mutate  (lib/movie-state/mutate.ts)
       idempotency key bound to the intent
       expected_revision from the record that was rendered
       double-submit CSRF, same-origin, no Authorization header
  -> committed canonical state + revision
       recorded in the tab-local relay (committed-store.ts)
       adopted by the control; the optimistic frame is discarded
```

- **One transition table.** `lib/movie-state/actions.ts` holds the ADR 0012
  transitions once — watched clears watchlist and preserves the first watched
  time, a rating implies watched, deleting a rating leaves the movie watched,
  removing history takes the rating with it, watchlist changes nothing else,
  dismissal is an undoable exclusion. Both the control projection and the
  collection projection are derived from it, so a card and a Library row cannot
  disagree.
- **Nothing invents a revision.** The optimistic frame never touches
  `revision`; only a committed response replaces the record the next write
  asserts against.
- **A conflict is corrected, not reported.** `409` triggers a canonical re-read
  through `MovieStateClient.readState`; the control shows what is stored and the
  next click asserts a revision the server issued.
- **One intent, one key.** A retry of the same intent replays the original
  commit rather than writing a second feedback event. `Try again` in the Library
  and a re-pressed control in `useMovieState` both rely on that.
- **Failure rolls back and returns focus.** Dropping the pending patch restores
  the last committed truth; `lib/movie-state/focus.ts` walks control → row →
  collection so a keyboard reader is never dropped on `<body>`.
- **The relay feeds every surface.** Because recording the committed answer
  happens in the write path rather than in one route, a watchlist set on detail
  shows on the restored Browse card and on the Discover rail. It is per tab, per
  persona, capped, expiring, and never authoritative: a fresh read always wins.

`RecommendationItem` carries no `state`, so Discover seeds its cards from the
relay after hydration and corrects anything it does not know through the
conflict path. Per-card state reads are deliberately not the answer: that is the
fan-out the local catalog snapshot exists to prevent.

## Returning from a movie

A movie is reachable from Browse, Library, and Discover, and each of those keeps
state worth returning to. `lib/navigation.ts` owns the allow-list and the back
link's label, so `/movies/[movieId]?returnTo=` honours all three and the link
names where it actually goes. Anything outside the allow-list is discarded
rather than followed.
