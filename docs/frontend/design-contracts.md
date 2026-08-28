# Movie-discovery frontend: design contracts

**Status:** Accepted direction; implementation pending

**Last updated:** 2026-08-21

## Shared product contract

**User and job:** A viewer wants to decide what to watch, capture movie
feedback, and receive a refreshed recommendation set. A technical reviewer
wants evidence that the result came from the claimed tenant-scoped ML path.

**Product promise:** The movie decision is primary. ML evidence is truthful,
specific, and available without becoming the page's dominant visual object.

**Brand personality:** Cinematic, analytical, fast, and honest.

**Image treatment:** Poster-first, fixed aspect ratio, optimized loading, and a
stable MovieLens fallback when TMDB is missing or unavailable.

**Motion treatment:** Motion explains state change. Use restrained lift,
selection, save, rating, and content-refresh transitions. Avoid looping motion,
decorative parallax, or movement that delays input. Respect
`prefers-reduced-motion`.

**Responsive strategy:** Desktop may show multiple rails and a detail side
panel. Mobile keeps one primary movie decision, reachable actions, and a bottom
navigation model. It must not become a long stack of desktop cards.

**Accessibility:** Every swipe/gesture has a labeled button and keyboard path.
Poster text has readable contrast, focus is visible, state is not encoded only
by color, and live mutation feedback is announced.

## Global application shell

### Navigation

- `For you` → `/discover`
- `Browse` → `/browse`
- `Library` → `/library`
- `Quick picks` → `/quick-picks` once the core routes pass testing
- Authenticated actor plus an explicitly labeled selected demo persona
- Compact model-health or environment state only when the value is real

Desktop uses a persistent top navigation. Small screens use a compact header
plus bottom navigation for the three primary routes. Quick Picks may remain a
Discover entry point on mobile rather than claiming a permanent fourth slot.

The shell must never make the selected persona look like the signed-in human's
private account. In portfolio persona mode, use copy such as
`Exploring as Action Fan` and show the actor/session separately. Persona
switching outside local development requires an explicit impersonation role.

### Shared content hierarchy

1. Movie or movie collection relevant to the route.
2. Primary user decision.
3. Supporting metadata and reason.
4. Technical evidence through `Why this?` or `Model details`.
5. Portfolio attribution and operator details.

### Shared forbidden defaults

- Architecture hero before movie content.
- Four or more equal-weight dashboard summary cards.
- Generic glass panels or gradients used only to imply polish.
- Text-only movie grids when poster metadata is available.
- Unlabeled icon-only core actions.
- Hover-only information required to decide or act.
- Horizontal rails without keyboard controls, accessible names, and an
  alternate `See all` route.
- Swipe as the only way to classify a movie.
- Model claims that exceed the observed backend behavior.

## `/discover` — For You

**User and job:** Find a promising next movie with minimal work.

**First-read object:** The strongest current recommendation: poster, title,
year, genres, compact reason, and match/rank signal with honest terminology.

**Primary action:** Open the movie. Immediate supporting actions are
`Watchlist` and `Watched`.

**Density:** Spacious first decision followed by compact, poster-led rails.

**Hierarchy:**

1. Top recommendation.
2. Open/watchlist/watched actions.
3. Reason and compact metadata.
4. Additional ranked recommendation rails.
5. `Why this?` technical disclosure.

**Interaction model:** Ranked feed with a primary recommendation and several
scan-friendly rails. A rail is allowed only when its label reflects a real
selection rule. The strongest recommendation appears first/left.

The featured slot is a **queue position the route owns**, not a fixed card. The
queue is 24 deep and extends in the background at three remaining, so a viewer
can make roughly two dozen decisions before the end state — which names Browse
and Quick Picks rather than reading as a generic empty region. All three
decisions advance the slot, watchlist included, and **only on commit**: a slot
that advanced optimistically and then rolled back would re-show a title the
viewer had already dismissed. The outgoing decision travels in the direction
vocabulary Quick Picks' swipes use — watchlist right, watched up, dismissal left
— and under `prefers-reduced-motion` the advance is an instant swap with the
announcement unchanged. Watchlist and dismissal leave a time-boxed `Undo` in the
status region that restores both the server state and the cursor; `Watched` does
not, because it is `final` on this surface and a bare `Undo` would quietly
become the destructive edit the control set refuses. It offers the rating prompt
and a route into the Library instead.

That rating prompt is **an offer with an end**. It opens under the ranked card
after a watched decision and it closes the moment a star commits: the panel is
removed rather than left standing with its stars filled in, and one sentence
takes its place in the status region — `Rated <title> 4/5. Ratings do not
reorder the list — the watch already counts.` That sentence is the settled copy
in full. Nothing about a refresh follows it, because a star cannot move a set
the watch has already excluded the title from; the re-read still happens and is
silent for the same reason. It stands for about four seconds and the region then
returns to its resting line, and any new decision replaces it at once — a
confirmation about one movie must never be on screen over another. `Manage in
Library` travels with it, because watched is `final` here and the place to
change a rating has to stay named rather than implied.

A rating also **hands the page back**. The prompt sits below the ranked card and,
at 390px, below the fold, so a commit that left the viewport where it was would
leave the viewer reading a finished decision about a title the featured slot had
moved past two presses earlier. On commit the featured section is scrolled into
view — smoothly, instantly under `prefers-reduced-motion`, and only when the
movie is genuinely off screen, so a viewer who can already see it is not scrolled
at all — and focus moves with it, to the first control the surface's control set
declares. The two are one movement: a scroll a keyboard reader cannot follow is
not a return, and a focus move the eye cannot follow is not one either.

Settled status copy reports the decision and the movie it moved to, and adds a
second sentence only when there is one to add: `Recommendations refreshed.`
after a re-read that actually changed the set, the failure when it did not
answer, and nothing at all when the set legitimately came back identical — which
is what a watchlist press does, and which the sentence in front of it has
already accounted for.

**Recommended initial surfaces:**

- `Top picks for you` — the primary model-ranked list.
- `Explore [genre]` — a filtered view derived from the user's observable taste
  profile, only when the label can be supported.
- `Popular while we learn` — cold-start: the persona is below the learned-serving threshold.
- `Popularity fallback` — a warm persona the router still sent to the fallback (the model
  server unavailable, an unseeded retrieval); it says what happened without claiming the
  system is still gathering signals it already has.

Do not manufacture separate personalized policies by slicing one list into
misleading categories. When only one ranked list is available, show one rail
plus honest genre exploration links.

**ML evidence:** `Why this?` opens a drawer or sheet that **answers in a
sentence before it answers in a table** — one plain statement built only from
values the response actually reported, with the existing tables unchanged
beneath a `Model evidence` heading. Reason, candidate/ranker/feature versions,
policy, fallback reason, request ID, and latency all remain there, and detailed
prediction features remain an advanced disclosure: the audit is still two
deliberate actions away, so it can never delay the first movie.

The sentence is bound to the reported values, not inferred from them. A
fallback note may not describe a signal count above the threshold as though it
were below it — `serving_policy` can legitimately report a high
`positive_signal_count` alongside `learned: false` (an unseeded retrieval, for
instance), and the copy has to say the true thing in that case rather than
"28 of the 5 watched signals".

**Responsive priority:** Poster, title, reason, and actions remain together.
Rails show a partial next card or clear next controls to signal continuation.
Model details become a full-width sheet on mobile.

**Finish evidence:** Desktop/mobile screenshots for learned, cold-start,
loading, empty, API-error, and failed-poster states; keyboard rail navigation;
watched/rating refresh flow, including the rating follow-through at 390/768/1440
— panel gone, confirmation shown and then cleared, movie and focus back on
screen.

## `/browse` — Catalog

**User and job:** Explore many movies without being forced through a single
recommendation at a time.

**First-read object:** Search/filter controls and a poster grid with an explicit
result count.

**Primary action:** Open a movie.

**Density:** Balanced to dense. Poster recognition is more valuable than large
descriptions at this level.

**Hierarchy:**

1. Search and active filters.
2. Result count and sort.
3. Poster, title, year, compact state, and match/rank signal when available.
4. Pagination/load-more state.

**Interaction model:** Searchable, filterable, paginated grid. Initial filters:
genre, year/decade, rated status, watched status, and watchlist status. Add
duration or provider only when data exists.

**Default ordering: `Most watched here`,** not alphabetical. An unfiltered first
visit is a browsing surface, and the first window it shows is what tells a
viewer whether this catalog is worth exploring — alphabetical order opens on
whatever titles happen to begin with a digit or an `A`, which is an arbitrary
answer to the only question the first screen is asked. The sort is spelled in
the URL when it is not the default, and it does not count as an *active filter*:
it reorders the result set rather than narrowing it, so it does not appear in the
active-filters row.

**Movie-card actions:** Opening the card is primary. Watchlist and watched are
available through a visible secondary action or accessible action menu, rendered
by the shared movie-state control family inside a group named for the movie, so
the buttons can name the action while the group names the title. Rating should
follow `Watched`, not cover every card permanently. Watched is a *statement* on
a card — undoing it deletes the one interaction the recommender observed, and
that belongs on movie detail or in the Library.

**Responsive priority:** Filters collapse into an accessible sheet; active
filters and result count remain visible. Grid column count changes without
shrinking posters below useful recognition size.

**Finish evidence:** Search/filter combinations, pagination continuity, empty
results, slow metadata, poster fallback, long titles, keyboard traversal, and
back-navigation scroll restoration.

## `/library` — Ratings, Watchlist, and History

**User and job:** Review and manage the movie state already created.

**First-read object:** The selected persona's active library collection, not a
summary dashboard. The route becomes `Your library` only after `/me` ownership
is implemented.

**Primary action:**

- Rated: edit or remove a rating.
- Watchlist: open or remove a saved movie.
- History: inspect a chronological interaction and open the movie.

**Density:** Compact and scannable. The library may use a list on narrow screens
and a compact poster grid or list on wide screens.

**Hierarchy:**

1. `Rated`, `Watchlist`, and `History` tabs with counts.
2. Sort/filter controls appropriate to the active collection.
3. Movie state and primary action.
4. Optional taste-profile summary based only on real features.
5. Destructive profile reset inside settings/disclosure.

**Interaction model:** Persistent tabbed library. URL state preserves the
selected tab, filters, and sort. Rating edits provide optimistic feedback with
rollback on failure.

Watched state, optional star value, watchlist, and dismissal are independent
backend states. Editing a star does not change the original watched timestamp;
deleting a star leaves the movie watched; removing watched history is a separate
destructive action.

**Taste profile:** Show only values that can be traced to the current feature
contract. Label the time/freshness boundary. Do not imply that a chart updates
immediately when Feast materialization does not.

**Responsive priority:** Rating/state and the next action remain visible.
Secondary metadata moves into detail. Do not transform every desktop row into
a large, equal-weight mobile card.

**Finish evidence:** Empty collections, rating edit/remove, watchlist remove,
history pagination, reset confirmation, auth expiry, long history, focus after
mutation, and tenant-isolation tests.

## `/movies/[movieId]` — Movie detail

**User and job:** Decide whether one movie is worth watching and manage its
state.

**First-read object:** Poster/backdrop, title, year, genres, and current user
state.

**Primary action:** `Watchlist` when unseen; `Rate` after watched.

**Density:** Rich but ordered. Overview and reasons are visible; technical
feature/audit details are disclosed separately.

**Required content:**

- Poster with fallback.
- Title, year, genres, overview, and metadata source.
- Watched, rating, watchlist, and rejection/suppression state when implemented.
- Recommendation reason and policy when opened from a recommendation.
- Similar or related movies only when the backend can name the policy honestly.

**Responsive priority:** Actions remain reachable without covering essential
content. A desktop side panel becomes a full page or bottom sheet on mobile.

**Finish evidence:** Direct URL, opened-from-rail state, missing TMDB metadata,
rating mutation, watchlist mutation, rejected state, and back-navigation.

### The enriched record

`MovieDetailResponse.item` carries a nullable `details` block — tagline,
runtime, backdrop, TMDB score, directors, up to six billed cast, and one
trailer. The **list** endpoint does not: a Browse page of forty titles dragging
forty cast lists and backdrops through the response is a page-size regression
nobody asked for, and the API keeps that split in the type system rather than in
a review comment.

Everything in the block is optional, so the page is written the other way round
from the usual: **the degraded page is the base case** — poster left, identity
right, exactly what this route rendered before enrichment existed — and each
field that is present upgrades one region. A record with no `details` shows no
empty frames where a backdrop or a cast row would have gone, and each individual
gap is silent rather than labelled. That is a deliberate difference from the
missing-synopsis rule: a missing synopsis is named because the reader is looking
for one, while "Runtime unavailable" beside a movie is noise.

| Field | Where it goes | When it is absent |
|---|---|---|
| `backdrop_url` | A wash behind the hero, under a veil that ends on `--surface-canvas` and is darkest on the copy side | The hero is the poster-left layout it always was |
| `tagline` | Under the title, in the display face, italic — it is the studio's own sentence about the film and belongs with the name, not with the synopsis | Nothing |
| `runtime_minutes` | Joins the existing meta line: `2016 · 2h 25m · Thriller · Drama` | The meta line reads as before |
| `tmdb_rating` | `8.1 / 10 · 4,812 ratings`, the count always travelling with the average | Nothing; a zero count is no score, not "0 ratings" |
| `trailer` | A poster-framed `Play trailer` plate, below the hero | No section at all |
| `directors`, `cast` | A `Cast and crew` block; the cast is a horizontal scroller with a monogram where a portrait is missing | No block |

**The trailer loads nothing until it is pressed.** The plate is drawn from
artwork the page already holds — the backdrop, or the poster — precisely because
the common "lite embed" pattern shows YouTube's own thumbnail, which is a
YouTube request made on behalf of every viewer of every movie page. Pressing it
builds a `youtube-nocookie.com` embed with a visible title, closable by button
or Escape, returning focus to the plate. The promise is asserted two ways in
`e2e/movie-detail.spec.ts`: no `iframe`, and no request to the embed host.

**Attribution.** A `Details from TMDB` line, with the TMDB mark and the required
non-endorsement sentence, sits with the enriched fields it covers. It is scoped
to those fields and does not stand in for shell-level attribution.

### Rating

Movie detail owns the product's one **large** rating control
(`components/movie/rating-stars.tsx`); every other surface keeps the compact
editor. The difference is that detail is where rating is the decision rather
than an incidental edit, and it earns three behaviours the small editor has no
room for.

- **It previews.** Hover and keyboard focus fill the row from the left. The row
  is one tab stop with arrow-key selection, so reaching `Clear rating` costs one
  stop rather than five.
- **It acknowledges, after the commit.** Stars fill left to right one
  `--motion-stagger` apart, the chosen star pops once over `--motion-pop` with a
  glow that fades, and then the row folds into a chip. The optimistic frame
  fills the row so a press has an immediate answer, but the celebration waits
  for the API: celebrating a write that can still roll back is exactly the lie
  the commit-before-acknowledge rule exists to prevent. Under
  `prefers-reduced-motion` the sequence is skipped and the result is identical.
- **It collapses.** `You rated 4/5 · Change rating`. Five large controls for a
  decision already made are five chances to change it by accident, and a movie
  that arrives already rated opens collapsed for the same reason.
  `Change rating` reopens the row pre-filled from what is *stored*, with focus
  on that value; `Clear rating` lives inside the reopened row, one deliberate
  step away from a value somebody recorded.

The panel keeps one honest sentence under the control, per ADR 0012: rating
records a watch, and the star value is display feedback rather than a graded
training signal.

## `/quick-picks` — Rapid preference collection

**User and job:** Classify several movies quickly to create or refine a useful
profile.

**First-read object:** One poster-led movie with enough context to make a
decision.

**Primary action:** Classify with `Not for me`, `Watchlist`, or `Watched`.

**Density:** Focused. One decision at a time with progress and an escape to
Browse.

**Interaction model:** Optional gesture-enhanced decision queue. Swiping is a
shortcut, not the contract. Buttons and keyboard commands expose identical
behavior.

`Watched` reveals the rating control. `Not for me` initially means durable
title suppression; it must not become a negative training label without an
accepted cross-cutting ADR.

Progress copy uses the accepted five-interaction cold-start threshold. Until
the online router is aligned with ADR 0011, Quick Picks may show collected
feedback but must not claim that five signals have switched serving policy.

**Responsive priority:** Touch actions stay within thumb reach; keyboard and
screen-reader order remains logical. Motion never hides the result of a failed
mutation.

**Finish evidence:** Buttons, keyboard, touch gestures, undo, failure rollback,
reduced motion, queue exhaustion, cold-start completion, and suppressed-title
exclusion.

## Visual-system foundation

The implementation should formalize the current black/amber identity into
semantic tokens rather than scatter literal colors through components:

- surfaces and elevation;
- primary/secondary/muted text;
- accent and focus;
- success, warning, destructive, and degraded states;
- poster overlay and fallback;
- typography roles;
- 4px-based spacing;
- motion durations/easing;
- narrow/tablet/desktop breakpoints.

Dark-first is a deliberate product choice. A theme toggle is not required for
the first delivery unless research establishes a user need; accessible contrast
and system-level forced-color support are required.

## Method references

- [UX Architect](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ux-architect.md)
- [UI Designer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-designer.md)
- [UI Finish-Gate Reviewer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-finish-gate-reviewer.md)
