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

**Recommended initial surfaces:**

- `Top picks for you` — the primary model-ranked list.
- `Explore [genre]` — a filtered view derived from the user's observable taste
  profile, only when the label can be supported.
- `Popular while we learn` — cold-start or explicit degraded path.

Do not manufacture separate personalized policies by slicing one list into
misleading categories. When only one ranked list is available, show one rail
plus honest genre exploration links.

**ML evidence:** `Why this?` opens a drawer or sheet containing reason,
candidate/ranker/feature versions, policy, fallback reason, request ID, and
latency. Detailed prediction features remain an advanced disclosure.

**Responsive priority:** Poster, title, reason, and actions remain together.
Rails show a partial next card or clear next controls to signal continuation.
Model details become a full-width sheet on mobile.

**Finish evidence:** Desktop/mobile screenshots for learned, cold-start,
loading, empty, API-error, and failed-poster states; keyboard rail navigation;
watched/rating refresh flow.

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

**Movie-card actions:** Opening the card is primary. Watchlist and watched are
available through a visible secondary action or accessible action menu. Rating
should follow `Watched`, not cover every card permanently.

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
