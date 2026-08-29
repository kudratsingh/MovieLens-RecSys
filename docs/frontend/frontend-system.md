# Frontend system

**Status:** Current. The Bundle 4 visual system plus the shared movie-state
controls and write path converged in Bundle 7 (PR #61), and the body below
carries the product round through PR #85 — the shared `RatingStars` control, the
featured queue, and the Seen surface.

**Last updated:** 2026-08-29

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
| `/ui-preview/movies/[movieId]` | movie lookup and metadata | poster failure, state controls, rating, trailer plate, evidence drawer | detail fixture |
| `/ui-preview/quick-picks` | queue item selection | equal button and keyboard preview paths | queue fixture |

Pages and layouts remain Server Components by default. Client boundaries are
placed at navigation highlighting, image-error recovery, local controls,
drawers, filter/tab state, and keyboard behavior.

**Every product route renders `AppShell`**, `/quick-picks` included. It was the
last route running its own composition — no `<main>`, no skip link, neither
navigation, no way to sign out, and a `Demo persona {id}` placeholder where the
other four name the persona. Wrapping it changed nothing about the deck: the
design contract lets Quick Picks stay a Discover entry point rather than a
fourth navigation slot, and the deck's full-height composition already reserved
the header's `5rem`. What it gained is the four landmarks and the one exit every
other route has.

## Visual contract

Semantic CSS variables in `web/app/globals.css` cover:

- canvas, base, raised, overlay, and inverse surfaces;
- primary, secondary, muted, and inverse text;
- accent and high-visibility focus;
- success, warning, destructive, and degraded state;
- poster fallback and overlay;
- rating gold, its idle counterpart, and its glow; and
- 4px-derived spacing, typography roles, radii, elevation, motion, and easing.

Motion is `--motion-fast`, `--motion-standard`, and the acknowledgement trio
`--motion-stagger` / `--motion-pop` / `--motion-collapse`. `--ease-out` is the
general easing; `--ease-pop` is the only one that leaves 0–1 and exists for the
single overshoot in the product, the star that lands when a rating commits.

Dark-first is intentional. Every interactive control receives a visible
`:focus-visible` ring. Primary mobile targets are at least 44 CSS pixels.
Forced-color and reduced-motion media queries preserve meaning and operation.

### Elevation

A cast shadow does very little work on a `#0b0a09` canvas — a dark poster on a
dark ground reads as a hole rather than as an object — so the scale is a pair:
a shadow that says how far off the page something sits, and a hairline
highlight along its top edge that says where the object starts.

| Token | Where it belongs |
|---|---|
| `--elevation-1` | A resting card: poster frames, rail control chips. |
| `--elevation-2` | A control under the pointer. |
| `--elevation-3` | The pointer lift on a poster. |
| `--elevation-spill` | A low-alpha accent glow, composed *alongside* an elevation step so a lifted poster changes temperature as well as depth. Never used alone. |
| `--edge-highlight` | The hairline top edge. Applied over the element's own content — the poster frame paints it on the scrim above the artwork, because an inset shadow on a frame that clips an image would be hidden behind the image. |
| `--shadow-raised` | Predates the scale, and stays with the overlay surfaces (drawer, auth menu) that want a soft, wide shadow rather than a card's tight one. |

The lift itself (`translateY`) sits behind `prefers-reduced-motion:
no-preference`; the shadow and border response does not, so a reduced-motion
viewer still gets the full hover feedback without the movement.

## Posters and titles

Two rules, each stated once and consumed by every surface:

- **One display title.** `displayTitle(title, releaseYear)` in
  `web/lib/movie-types.ts` strips MovieLens's trailing parenthetical year only
  when it matches the structured `release_year`. The year is then printed once,
  on the metadata line, so a card no longer reads `Babe (1995)` above
  `1995 · Children`. Discover, Browse, movie detail, Library, and Quick Picks
  all call it; nothing re-implements it.
- **One fallback mark.** When artwork is missing or fails to load, every surface
  renders `PosterFallbackMark` from `components/movie/poster-card.tsx` over
  `posterInitials(displayTitle)` — uppercase, at most two letters, stop-words
  dropped, `?` when nothing survives. That rule replaced four hand-rolled
  `slice(0, 2)` calls that produced `B(`, `T(`, `A(` and `QU`. A failure is
  remembered per movie, not per slot: the next movie to occupy the same frame
  starts from its own artwork.

A poster card is **one link** wrapping the artwork and the caption together. It
used to be two anchors to the same href, which cost a keyboard reader an extra
tab stop on every card in a rail.

**The caption reserves two title lines,** whether the title needs one or two, so
a card's caption — and therefore anything placed under it — is the same height
across a row. `Toy Story` beside `To Kill a Mockingbird` otherwise set two
different baselines and left neighbouring cards offering their decisions at
different heights. The clamp keeps the full name reachable: it is on the link's
accessible name and on the title's `title` attribute. The metadata line is a
single ellipsised line for the same reason.

**A loading grid is built from those same boxes,** and reserves a full page of
them. Browse's placeholder (`CatalogGridSkeleton` in
`components/browse/browse-explorer.tsx`) renders `CATALOG_PAGE_LIMIT` cells —
the page size the request itself asks for — out of `poster-frame`,
`poster-card-copy` and `catalog-cell-actions`, so no cell height is restated
anywhere and the placeholder cannot drift out of step with the card. It stands
in for the first page *and* for a cursor continuation. Reserving only the first
page was the gap: pressing `Load more` near the foot of the document left the
shell footer on screen with nothing holding the incoming page's space, and when
the response outran the browser's window for attributing a shift to the click
that caused it, the footer's move down the page was scored as instability the
reader had not asked for — `browse cumulative layout shift 0.354` against a
budget of `0.1`, on the runs slow enough to produce that ordering.

**A ranked card hangs its rank in the caption gutter,** set in the display serif
that section titles and the featured movie use and nothing else inside a card
does. It was a cream disc pinned inside the poster's top-left, which covered
whatever that poster's own designer had put there and all but vanished on a pale
sheet. In the gutter it costs no vertical space, never touches artwork, and
reads as what it is: a position in an edited list rather than a badge stuck on a
tile. It is `aria-hidden` — the link already names the movie, and a screen
reader should not be handed a bare numeral before every title.

## Independent failure contract

Recorded resources return the discriminated `ResourceResult<T>` union. Expected
failures render within the owning resource instead of throwing away the page.
Reviewers can exercise the contract without a backend:

```text
/ui-preview/discover?fail=recommendations
/ui-preview/discover?fail=evidence
/ui-preview/browse?fail=catalog
/ui-preview/library?fail=library
/ui-preview/movies/101?fail=movie-detail
```

Movie detail's enrichment states are addresses rather than scripted
interactions, because the recorded catalog carries one title per branch: `101`
is fully enriched with a trailer and a backdrop, `103` is enriched with neither
and already rated, `104` is enriched and rated over a backdrop, and everything
from `111` up has no `details` at all. The preview's writes are answered by an
in-memory `MovieStateClient` (`lib/fixtures/movie-state-preview.ts`) so the
states a commit *produces* — the rating chip, and the reopened row behind it —
are reachable without a backend. It applies ADR 0012's transitions by calling
the same `applyActionToDisplay` the live surfaces use, so a preview screenshot
cannot show a combination the product would never commit.

Two properties of the shared blocks in `components/ui/resource-states.tsx`:
a failure region is named by its **headline** rather than by the transport
status enum, so a screen reader hears `Recommendations could not be loaded`
rather than `Recommendations upstream-error`; and `Try again` is offered only
when the caller passed an `onRetry` it can actually run. Both blocks collapse to
a single column at ≤480px with their actions in one full-span row, in the order
they were offered.

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
| `MovieRatingControl` | The compact rating editor, in whole stars or the stored half-star precision. Discover, Browse, the Library, and Quick Picks use it. |
| `RatingStars` | Movie detail's large rating control (`components/movie/rating-stars.tsx`). Same intent, same write path, more room — see below. |
| `MovieStatePanel` | Movie detail's composition: the family wired to the write path, with its own status and error regions. |

Surfaces differ by *declaration*, not by forking the component. A control set is
an ordered list, because which controls a surface offers and in what order is
its documented hierarchy:

| Surface | Control set | Why |
|---|---|---|
| Discover card, featured movie | `watchlist: toggle`, `watched: final`, `dismissal: toggle` | Discover ranks unseen movies. `Watched` is a one-way statement there; undoing it is a destructive edit that belongs elsewhere. **All three decisions advance the featured slot, and only on commit** — see below. |
| Browse card | `watchlist: toggle`, `watched: final` | A grid is a passing surface, so it offers the reversible organizational choice first and states the recorded one second. `final` rather than `mark` because `mark` has no branch for an already-watched movie and would offer `Mark watched` beside a card that already says `Watched`. Declared in `components/browse/catalog-grid.tsx`; the grid carries **one** `aria-live` region for all its cards rather than forty. |
| Movie detail | `watchlist: toggle`, `watched: confirm`, `dismissal: toggle` | Detail is where a title is managed. `Watchlist` leads while the movie is unseen, and removing history is confirmed. |
| Library — Rated | `dismissal: undo` + half-star rating editor | Editing or clearing a star value is the collection's job. |
| Library — Watchlist | `watched: mark`, `watchlist: remove`, `dismissal: toggle` | The row leads with the action that moves the movie forward. |
| Library — Seen (`history`) | `watched: confirm`, `dismissal: undo` + rating editor | Seen owns the one destructive action in the route, and `Remove rating` stays visibly different from `Remove from history`. The spotlight above the list declares the same set through the same `libraryControlSet("history", watched)` call, so the two surfaces cannot offer different actions for one movie. |
| Quick Picks | Its own three decision buttons + the shared star editor | The deck's buttons are queue decisions with keyboard hints, gesture parity, and an `undo-dismiss` that has no card or row equivalent, so they stay with the machine that drives them. The rating is the same control everywhere else uses. |
| `/ui-preview` | `watchlist: toggle`, `watched: toggle`, no writes | Recorded surfaces toggle locally and announce `Preview only`. |

**The Discover featured slot is a queue position, not a card.** The route holds
a queue (24 deep, extended in the background at three remaining) and the
featured slot shows its head. Every decision — watchlist included — advances it,
and only after the API has committed: a slot that advanced optimistically and
rolled back would re-show a title the viewer had just dismissed. The outgoing
decision travels in the direction map `lib/movie-state/actions.ts` shares with
Quick Picks' swipes (watchlist right, watched up, dismissal left), and under
`prefers-reduced-motion` the advance collapses to an instant swap with the
announcement unchanged. Watchlist and dismissal leave a time-boxed `Undo` beside
the status line that restores both the server row and the cursor; watched does
not, because `watched: final` on this surface means what it says, and a bare
`Undo` there would quietly become the destructive edit the control set refuses.

### The Seen spotlight

`/library?tab=history` is the Seen experience: the tab's identity is still
`history` in the URL, in the API, and in the `LibraryTab` type — only its label
changed — and above the list it now presents one watched title at a time, in the
same visual family as Discover's featured slot.

It is a sibling of that presentation rather than a second copy of it.
`components/library/library-spotlight.tsx` composes the shared primitives
(`PosterCard`, `RatingStars`, `MovieStateControls`) and carries its own layout,
because the featured slot hands its children to the page grid on a phone
(`display: contents`) and the spotlight's column has a pager, a rating fieldset,
and a confirmation panel that grid knows nothing about.

Four rules make it a view of the list rather than a second list:

- **Its queue is the loaded window.** Same rows, same order, same filters, same
  sort; a position is an index into what is on screen. The pure reducer in
  `lib/library/spotlight.ts` owns clamping (it never wraps), following a movie
  through an appended page or a departed row, and advancing past a title the
  reader removes.
- **It extends through the same cursor.** Within three of the end of the window
  it runs the `Load more` the list's own button runs — one window, one cursor,
  appended once. A failed append stops it until the reader retries.
- **The base layer never waits.** Poster, title, year, genres, the seen-on date,
  the rating control and the actions all come from the row. The enriched
  fields — backdrop, runtime, crowd score with its vote count, cast — are read
  from the detail resource for the current title only and *added* on `ready`. A
  failed, timed-out or `not-found` read is silent, and none of it can blank the
  list: the detail read is not one of the list's regions.
- **It announces navigation, and nothing else.** One `aria-live` region for
  `Psycho, 1960. 4 of 42 in Seen.`; mutations keep announcing through the
  route's existing region, because two live regions in one panel is how one of
  them stops being read. Moving the spotlight does not move focus, so a repeated
  `Next` keeps working, and `ArrowLeft`/`ArrowRight` are ignored inside an
  input, a select, or `.rating-stars` — the star row documents that binding
  first.

The list beside it gains a title search (already there), one genre, a release-
year range, and five orderings (`recent`, `title`, `rating`, `release`, `tmdb`),
all owned by the URL through `lib/library/url-state.ts` and all of them dropping
the cursor when they change, because the endpoint binds a cursor to the
fingerprint of exactly that set. `page.matched` is what the position readout
counts against; without it the loaded window is the only honest denominator.

### `RatingStars`: two sizes of one control, not two controls

The two rating editors are a deliberate split, and the line between them is
whether rating is *the* decision on the surface or an edit alongside others. A
Library row is editing a value it already has; a movie's own page is where
somebody decides what they thought of it. So detail gets the larger stars (32px
at desktop, 28px at 390, in a target that never drops below 44px), a preview
fill from the left on hover and keyboard focus, a roving tab stop with arrow
selection, and an acknowledgement.

Everything that could make the two disagree is shared: both report a
`MovieStateAction` to `useMovieState`, both write through `lib/movie-state/`,
and both carry the same ADR 0012 sentence about what a star does and does not
commit to. `RatingStars` adds no policy of its own; it never writes.

Three properties are worth naming because they are what the acknowledgement is
*for*, and each of them is a decision rather than an effect:

- **The celebration follows the commit, never the press.** The optimistic frame
  fills the row immediately so a press has an answer, but the stagger, the pop,
  and the collapse are driven by the committed rating arriving. A rating that
  fails and rolls back is never celebrated — the same commit-before-acknowledge
  rule the auth middleware keeps for durability, applied to what the viewer
  sees. Re-confirming the value already stored is acknowledged too, because that
  write succeeds and changes nothing: an effect watching the value alone would
  never fire and the row would sit open after a perfectly good press.
- **The sequence is timers, not `animationend`.** One set of durations lives in
  `globals.css` (`--motion-stagger`, `--motion-pop`, `--motion-collapse`,
  `--ease-pop`) and the same numbers are exported from `rating-stars.tsx`, so
  the phases and the CSS cannot drift apart and the whole thing is assertable
  under fake timers. `prefers-reduced-motion` skips to the collapsed chip and
  the result is identical.
- **Focus is placed, not dropped.** The star that was pressed unmounts when the
  row collapses, so focus moves to `Change rating`; reopening puts it back on
  the recorded value. A polite `aria-live` region — deliberately *not* a second
  `role="status"`, since the panel already owns one — says the one thing the
  panel's sentence cannot: that the control changed shape.

The new colour tokens are `--rating-star` (a brighter gold than `--warning`,
which has to read as caution on a status line), `--rating-star-idle`, and
`--rating-star-glow`. The same gold carries TMDB's aggregate score in the detail
hero, so one colour on that page means "a score exists" and the words beside it
say whose.

**`Skip` is not a control-set member, deliberately.** Discover's featured card
offers it beside the family when — and only when — the route knows the title is
watchlisted, and it is rendered by the route rather than declared in the set
because every member of that set produces a `MovieStateAction` and this produces
nothing. It advances the queue cursor and writes no request at all: not a
watched, not a dismissal, not a rating, and never a training negative (ADR
0012). Admitting it to the control set would mean widening the write
vocabulary to carry an action that never writes, which is exactly the drift the
one family exists to prevent. `web/lib/discover/featured-preference.ts` holds
the rule and the copy in one assertable place, `queue.ts` holds the pass-over
mechanism, and the unit test asserts the guarantee the only way it can be
asserted — that the press produces no request.

The `Featured picks` setting behind it is per persona and lives at
`GET|PUT /users/{id}/preferences`. It is presentation state, written through
`lib/discover/preference-client.ts` on the same terms as a movie-state
mutation — absolute full-object PUT, `expected_revision`, one conflict re-read
and replay, the committed answer adopted outright — and deliberately without an
idempotency key, because repeating a full-object write *is* the same request and
the API reports it as `no_change`.

Three further properties are load-bearing:

Three further properties of the control family are load-bearing:

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
  what a rollback has to do. The one control whose `aria-disabled` is permanent
  rather than in-flight — `watched: final` on a movie already watched — carries
  `movie-state-recorded` so it looks recorded instead of greyed out and waiting.
- **`compact` shortens the word, never the action.** At a rail card's width a
  pill holds one line, and `Mark watched` needed two. The compact rendering
  prints `Watched` and `Watchlist` and moves the full state into `aria-label`,
  so `Mark watched` and `In watchlist` remain the accessible names — what a
  screen reader announces, what speech input reaches the control by, and what
  every journey still asks for. Because the visible word does not change with
  the state, a pill cannot change width and the row cannot jiggle under the
  viewer; the recorded state is carried instead by `aria-pressed`, a filled
  mark, and — on the rail — an accent tint at low alpha rather than a solid
  coral fill that would out-shout nine posters. The family stops at the
  one-line promise; how wide a compact pill is, where it sits, and what a
  recorded one is painted in stay with the surface, so the Discover rail's
  density lives in `movie-rail.css` and the Browse grid's in
  `catalog-grid.css`. The rail's row is
  `repeat(auto-fit, minmax(5.25rem, 1fr))`, so its two pills sit side by side
  wherever a labelled pill fits and stack where one does not, without a
  viewport breakpoint deciding for them.

## The write path

Every watched, rating, watchlist, and dismissal change from every surface —
Discover, Browse, movie detail, Library, and Quick Picks — goes through
`lib/movie-state/`:

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
- **A conflict is corrected, not reported — and the intent is replayed once.**
  `409` triggers a canonical re-read through `MovieStateClient.readState`, and
  the same intent is then replayed against the revision that came back. Exactly
  one replay: a second `409` is a genuine conflict and is reported as one, with
  the canonical record attached. Re-reading and stopping there was the P0 —
  three surfaces had each built that half, so a viewer's *first* press on any
  title carrying a revision above zero was silently discarded and only a second
  one committed. Replaying is safe in both directions because the revision
  conflict is raised before any feedback event is written, and because both
  attempts carry the same idempotency key, so a lost response replays the stored
  result rather than applying it twice.
- **One intent, one key.** A retry of the same intent replays the original
  commit rather than writing a second feedback event. `Try again` in the Library
  and a re-pressed control in `useMovieState` both rely on that.
- **Failure rolls back and returns focus.** Dropping the pending patch restores
  the last committed truth; `lib/movie-state/focus.ts` walks control → row →
  collection so a keyboard reader is never dropped on `<body>`. The same walk
  has a `restoreFocusInPlace` variant for a caller that has just scrolled
  deliberately: `focus()` scrolls its element into view by default, and that
  instant scroll resolves against the position a smooth one has not reached yet,
  so a page that meant to move somewhere lands somewhere else. Discover's
  rating follow-through is its only caller today.
- **The relay feeds every surface.** Because recording the committed answer
  happens in the write path rather than in one route, a watchlist set on detail
  shows on the restored Browse card and on the Discover rail. It is per tab, per
  persona, capped, expiring, and never authoritative: a fresh read always wins.

`RecommendationItem` now carries a nullable `state` — the caller's own record
for that title, overlaid by one bounded keyed read on the request connection
rather than by a per-card fan-out, which is exactly what the local catalog
snapshot exists to prevent. A ranked title is never watched or dismissed
(serving excludes both before ranking) but it can be watchlisted, and it can
carry a revision above zero with every flag null — the row an added-then-undone
write leaves behind, which is the state the P0 above was about. Discover still
seeds from the tab-local relay after hydration and still corrects through the
conflict path; the field is what lets the *first* press assert a revision the
server issued instead of guessing zero.

## Returning from a movie

A movie is reachable from Browse, Library, and Discover, and each of those keeps
state worth returning to. `lib/navigation.ts` owns the allow-list and the back
link's label, so `/movies/[movieId]?returnTo=` honours all three and the link
names where it actually goes. Anything outside the allow-list is discarded
rather than followed.
