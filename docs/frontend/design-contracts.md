# Movie-discovery frontend: design contracts

**Status:** Accepted direction; implementation pending

**Last updated:** 2026-08-28

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

That rating prompt renders the **shared large star control** — see
[Rating](#rating) — because it exists for one decision and is headed
`Rate <title>`. It previews on hover and keyboard focus, it is one tab stop
moved with the arrow keys, and its targets stay at 44px on the mobile profile.

The prompt is **an offer with an end**. It opens under the ranked card
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

A title already on the viewer's watchlist **may still be featured**, and there
is a way past it. The featured card carries an `On your watchlist` cue and a
`Skip` control beside the three state buttons — beside them and not among them,
because those three write and this one does not. `Skip` is offered only where
the route actually knows the title is watchlisted: a recommendation can arrive
with no per-item state, and "not told" is not "not saved", so a card with
unknown state gets no cue and no control.

**A skip writes nothing.** Not a watched, not a dismissal, not a rating, and
never a training negative (ADR 0012 and its 2026-08-28 note). It moves the
featured slot on and leaves everything else exactly as it was, so the sentence
it announces says so — `Skipped <title> — still on your watchlist. Next:
<title>.` — and there is no `Undo` beside it, because nothing was undone. The
skipped title keeps its place in the ranked rail rather than disappearing: it
is still a recommendation, and being able to see it there is what makes the
skip reversible by looking rather than by remembering.

After the **third** skip of a watchlisted title in a session, the status region
asks once: `Stop featuring titles on your watchlist?`, with `Stop featuring
them` and `Keep featuring them`. Once, at the threshold — a viewer who keeps
skipping past the offer is answering it by not answering, and either explicit
answer settles it for the session including across a reload. The counter is per
tab and per persona and lives in session storage; the answer is durable and
goes to the API.

The answer also has a **permanent home**: a `Featured picks` setting at the foot
of the featured section, above the rail, governing the slot directly above it.
It is a labelled two-state button with one sentence under it, not a menu or a
popover — the shared forbidden defaults rule out hover-only information needed
to act, and a one-time question that is the only route to a setting is a setting
the viewer cannot change their mind about. With featuring off, the featured slot
passes watchlisted titles over and takes the first title it has no saved state
for; those titles stay in the ranked rail marked `In watchlist`. When every
remaining title is held back, the slot says exactly that rather than claiming
the ranked set is exhausted, and the rail stays on screen beneath it.

The preference is **presentation, not serving**. The API stores it and no
serving path reads it, so the response, its `serving_policy`, its exclusion
count, and its audit row are identical either way. Copy may never suggest
otherwise: neither the skip nor the setting changes what the recommender
learns, and both say so where a viewer is deciding.

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

The drawer also states **what never comes back**. Watched and dismissed titles
are dropped from the exclusion set before retrieval runs — true at the source
since Bundle 6 and, until now, invisible to the person it is about. One line
says it, and says the other half in the same breath: watchlisted titles do come
back, and whether they can be featured is the viewer's own `Featured picks`
setting. It sits below the plain sentence rather than inside it, because it
answers a standing question about the product rather than a question about this
one response.

**Responsive priority:** Poster, title, reason, and actions remain together.
Rails show a partial next card or clear next controls to signal continuation.
Model details become a full-width sheet on mobile.

**Finish evidence:** Desktop/mobile screenshots for learned, cold-start,
loading, empty, API-error, and failed-poster states; keyboard rail navigation;
watched/rating refresh flow, including the rating follow-through at 390/768/1440
— panel gone, confirmation shown and then cleared, movie and focus back on
screen. The `Featured picks` states join them: a watchlisted featured title with
its cue and `Skip`, the one-time question after the third skip, and the held-back
slot with the saved titles still in the rail (`?demo=watchlisted` and
`?demo=watchlist-held-back`).

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

## `/library` — Rated, Watchlist, and Seen

**User and job:** Review and manage the movie state already created.

**Tab identity:** The three collections are `rated`, `watchlist`, and `history`
in the URL, in the API, and in the `LibraryTab` type. The third is *labelled*
`Seen`, and every string derived from that label follows it. Renaming the value
is not on the table: it is a cursor fingerprint, a saved link, and a contract.

**First-read object:** The selected persona's active library collection, not a
summary dashboard. The route becomes `Your library` only after `/me` ownership
is implemented.

**Primary action:**

- Rated: edit or remove a rating.
- Watchlist: open or remove a saved movie.
- Seen: look back at one watched title at a time — re-rate it, remove it, or
  open it — and narrow the collection to the part being looked for.

**Density:** Compact and scannable. The library may use a list on narrow screens
and a compact poster grid or list on wide screens.

**Hierarchy:**

1. `Rated`, `Watchlist`, and `Seen` tabs with counts.
2. Sort/filter controls appropriate to the active collection.
3. On Seen: the spotlight — one watched title, presented in the same visual
   family as Discover's featured slot.
4. Movie state and primary action.
5. Optional taste-profile summary based only on real features.
6. Destructive profile reset inside settings/disclosure.

**Seen's spotlight:** A view of the list, never a second one. It walks the
loaded window in the list's own order under the list's own filters, extends
through the same cursor, and writes through the same path with the same
declared control set as a row. Its enriched fields (backdrop, runtime, crowd
score, cast) come from the detail resource for the current title only and are
added when they arrive; a failed read is silent and the list never blanks.
`Previous` / `Next` clamp rather than wrap, `ArrowLeft` / `ArrowRight` move it
unless the event belongs to a field or to the star row, and moving it announces
title and position without moving focus. Full contract: `docs/frontend/seen-contract.md`.

**Seen's filters and rankings:** a title search, one genre, a release-year
range, and five orderings — most recent, title, highest rated, newest release,
highest TMDB score. The URL owns all of them, only non-defaults are written, and
any change drops the cursor because the endpoint binds a cursor to the
fingerprint of exactly that set. A filtered collection with no rows says
`Nothing in Seen matches these filters.` and offers `Clear filters`; an unfiltered
empty collection still points at Browse. `release` and `tmdb` are offered on Seen
alone for now even though the endpoint accepts them on every tab.

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
Seen pagination, reset confirmation, auth expiry, long history, focus after
mutation, and tenant-isolation tests. For Seen specifically: the spotlight at
390 / 768 / 1440 and its filtered-empty state, both in the finish-gate matrix;
the stale-cursor restart; and one service-backed journey that changes a rating
from the spotlight and restores it.

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

Movie detail is the home of the product's **large** rating control
(`components/movie/rating-stars.tsx`), and it is shared with the two other
surfaces where rating is the decision rather than an incidental edit: the Seen
spotlight, and Discover's `Just marked watched` prompt, which is headed
`Rate <title>` and holds nothing else. Quick Picks and the Library rows keep the
compact editor, because a star there is one press of a queue decision or an edit
to a value that already exists. The rule is the surface's job, not its route —
applied any other way, the product ends up with two whole-star controls free to
drift apart in target size, in keyboard model, and in what a press looks like
before it commits.

The large control earns three behaviours the small editor has no room for.

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

The acknowledgement and the collapse belong to a surface the viewer stays on,
which is detail and the Seen spotlight. Discover's prompt ends itself the moment
the rating commits and answers in the status region on the way back to the
featured movie, so the sequence never gets a chance to run there — a 640ms
celebration in front of a page that is handing the viewer their next movie would
be the delay rather than the reward. The optimistic fill is what answers the
press on that surface, and it is the same optimistic fill as everywhere else.

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
