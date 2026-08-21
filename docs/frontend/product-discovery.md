# Movie-discovery frontend: product discovery

**Status:** Bundle 0 baseline

**Last updated:** 2026-08-21

## Objective

Turn the Phase 3 portfolio surface into a credible movie-discovery experience
without hiding the engineering that makes the recommendations defensible.

The product should let a person discover, inspect, save, mark watched, and rate
movies through an image-led interface. A technical reviewer should still be
able to inspect serving policy, model versions, features, fallbacks, audits,
and latency, but those details should not displace the movie decision in the
first viewport.

This is a product-specific redesign, not a request to make the existing page
generically more modern.

## Product lens

### Primary user and job

A movie viewer wants to find something worth watching, capture what they have
watched, and improve the next set of recommendations without navigating a
spreadsheet-like rating form.

### Secondary user and job

An ML-engineering reviewer wants to verify that the recommendations are
personalized, tenant-scoped, versioned, auditable, and produced by the claimed
serving policy.

### Highest-frequency workflow

1. Open personalized discovery.
2. Scan posters and reasons.
3. Open or save an interesting movie.
4. Mark a movie watched and optionally rate it.
5. Receive a refreshed recommendation set.
6. Revisit ratings, watchlist, or history in the library.

### First-read object

The first-read object is a movie: poster, title, compact metadata, and a clear
reason it belongs in the current recommendation set.

The first-read object is not the project phase, architecture, model checksum,
or latency budget. Those remain available as technical evidence.

## Current-state audit

### What already works and should be preserved

- Real recommendation responses include optimized TMDB posters, titles,
  genres, years, scores, reasons, and graceful metadata fallbacks.
- Four stable demo personas demonstrate different history and cold-start
  states.
- Ratings are written through the authenticated, RLS-scoped API transaction.
- After the write commits, a rated movie appears in history, is excluded from
  unseen results, and can seed the static item-item candidate index.
- Warm recommendations expose the candidate/ranker policy and model versions.
- Loading, empty, API error, and failed-poster states already exist.
- The demo is repeatable from a clean checkout and its serving behavior is
  covered by backend, tenant-isolation, feature-parity, and load tests.

### Problems visible in the current implementation

1. **The page is system-first.** The architecture hero and serving-contract
   panel appear before a movie. The contract also says `Popularity baseline`
   even when warm traffic is served by item-item candidates plus LightGBM.
2. **The rating wall owns the hierarchy.** Eighteen equal-weight, text-only
   rating cards appear before the recommendations. They have no poster imagery,
   browsing context, progressive disclosure, or dedicated library model.
3. **The content ceiling is too low.** The browser-facing route requests eight
   recommendations and eight history entries. The clean demo fixture contains
   only 24 movies.
4. **The catalog contract is not a discovery contract.** It has no poster,
   overview, year, search, genre filter, sort, pagination, status, or movie
   detail. The backend query returns at most 100 rows in movie-ID order.
5. **Library concepts are missing.** There is no watchlist, per-rating delete,
   durable rejection, or paginated history.
6. **The frontend is monolithic.** One client component owns persona loading,
   dashboard fetching, recommendation display, history, catalog ratings,
   mutations, and all UI states.
7. **Failure is unnecessarily coupled.** Recommendations, history, and catalog
   are fetched as one dashboard; one failing dependency makes the whole screen
   fail instead of allowing independent surfaces to recover.
8. **Frontend verification is shallow.** CI runs ESLint, TypeScript, and the
   production build, but has no component, accessibility, browser-flow, or
   visual-regression tests.
9. **The framework ADR contains stale scope assumptions.** It describes catalog
   search and real browser authentication as non-goals even though the current
   project plan requires browser-side Keycloak authentication and this redesign
   requires a real browsing surface.
10. **Mutation acknowledgement was ahead of durability at Bundle 0.** FastAPI
    sent a successful response before its transaction committed. Bundle 1 now
    commits before returning success and rejects commit failure.
11. **Cold-start routing disagreed with the accepted threshold at Bundle 0.**
    The coordinator used learned serving after any non-empty history. Bundle 1
    now keeps fewer than five unique watched movies on fallback.
12. **Tenant isolation is not profile ownership.** RLS prevents cross-tenant
    access, but a valid tenant actor may address any numeric demo persona. The
    signed-in Keycloak subject and selected recommendation persona are different
    concepts.
13. **Catalog breadth and prediction breadth are separate.** Adding catalog
    rows makes more movies browsable, but unobserved movies do not enter the
    learned or popularity candidate pool until seed interactions and serving
    artifacts are deliberately expanded.
14. **Live TMDB fan-out cannot power Browse.** The current bounded in-process
    cache is suitable for a small recommendation response, not a poster grid.
    Browse needs locally persisted or pre-enriched card metadata.

## Feedback-semantics discovery

The UI and the model currently use the word `rating` at different levels of
meaning.

- A stored rating is an explicit 0.5–5 score and a watched interaction.
- The learned online coordinator passes history movie IDs to the model sidecar;
  it does not pass star values.
- Item-item candidate generation therefore treats a 1-star and a 5-star movie
  as watched history.
- The newly rated movie is excluded from future results immediately.
- That immediacy begins only after the rating transaction has committed. Bundle
  1 now waits for commit before acknowledging success; the immediate-read
  regression test remains part of the contract.
- Feast user genre affinity is materialized from interaction membership rather
  than the explicit rating value, so a fresh star value is not an immediate
  rating-aware online feature.
- The older genre-affinity fallback code can use centered star values, but that
  is not the learned item-item plus LightGBM path shown in the current demo.
- A history of one to four unique watched movies remains on popularity
  fallback. Bundle 1 aligned the online coordinator with that accepted
  threshold; Quick Picks still verifies the response policy before claiming a
  transition.

### Product rule

The frontend may truthfully say that marking/rating a movie updates history,
removes that movie from unseen recommendations, and refreshes the result set.
It must not say that lower or higher stars immediately teach the learned model
different preference strength until a rating-aware serving contract is
implemented and tested.

Before adding `Not for me`, the project must decide whether it is:

1. a durable title-suppression preference only;
2. a negative online ranker feature; or
3. a negative training label that changes ADR 0002's implicit-feedback
   contract.

The first option is the safe initial product behavior. The other options are
model decisions and require a cross-cutting ADR.

Watchlist is initially organizational only. It must not seed candidates,
exclude watched titles, or change features. `Watched` is the positive implicit
interaction; a star rating is optional explicit display state attached to it.

## Platform discovery

The proposed product also requires the following boundaries to be visible in
the design rather than hidden inside implementation:

- **Actor versus subject:** the browser actor authenticates with Keycloak; the
  selected numeric MovieLens persona is the recommendation subject. Persona
  switching is an explicit role-gated demo capability until `/me` ownership is
  implemented.
- **Browser auth:** the API audience/calling-client contract now accepts the
  intended `movielens-web` PKCE token while rejecting unrelated clients.
  Bundle 1 now provides the server-owned encrypted session, refresh/logout,
  CSRF/origin checks, pinned public issuer, internal Compose routing, and a
  bypass-disabled browser proof. `/me` ownership remains separate from the
  explicitly role-gated persona mode.
- **Independent loading:** Discover, Browse, Library, and technical evidence
  need separate BFF resources and error boundaries. Page-shaped fan-out must be
  load tested because each authenticated API request currently holds a database
  transaction and several async handlers run synchronous SQL.
- **Contract ownership:** FastAPI's OpenAPI and the frontend types are now
  generated and checked for drift in CI. Runtime validation is still needed for
  malformed upstream bodies at the BFF boundary.
- **Movie metadata:** poster/year data for grids should come from a shared local
  read model or reviewed fixture. Overview and richer metadata belong on the
  detail request; live per-card TMDB calls are outside the Browse critical path.

## Research questions

1. Can a viewer identify a promising movie in under 60 seconds?
2. Does the first viewport communicate both the movie and the primary action?
3. Can a viewer mark watched, rate, and continue browsing without losing their
   place?
4. Can a viewer find and edit an earlier rating?
5. Do viewers understand the difference between watched history, star rating,
   watchlist, and rejection?
6. Do viewers correctly understand what changes immediately after feedback?
7. Can a cold-start user provide enough signal to reach a learned result?
8. Can a technical reviewer find model, feature, audit, and latency evidence
   without those details distracting the movie viewer?
9. Does the mobile layout preserve movie discovery and feedback rather than
   merely stacking desktop sections?
10. Which failures cause abandonment: missing posters, API errors, slow refresh,
    empty rails, authentication expiry, or lost scroll position?

## Reference-pattern study

References are evidence for a transferable interaction pattern, not visual
templates to copy.

| Reference | Pattern to study | Transferable lesson | Do not copy |
|---|---|---|---|
| Netflix | Personalized rows, ranked titles, fresh interaction signals | Use multiple scan-friendly recommendation surfaces with strongest items first | Streaming playback chrome or opaque row labels unsupported by our models |
| Letterboxd | Poster-level watched, rating, diary, and watchlist actions | Keep movie state close to the poster and give ratings/history their own library | Social feeds, reviews, followers, or community mechanics |
| JustWatch | Large-catalog filters and saved-title tracking | Use simple filters and visible active state to make a large catalog manageable | Provider availability until the data exists |
| Tinder | Focused sequential decisions and direct feedback | Offer an optional Quick Picks mode for rapid classification | Making swipe the only interaction or hiding movie context |
| Current MovieLens demo | Policy/version evidence and durable personas | Preserve honest ML traceability and demonstrable warm/cold behavior | Permanent architecture panels ahead of the movie workflow |

Primary public references:

- Netflix, [How the recommendations system works](https://help.netflix.com/en/node/100639)
- Letterboxd, [Frequently asked questions](https://letterboxd.com/about/faq/)
- JustWatch, [About](https://www.justwatch.com/us/about)

## Research method

### Participants

- Four to five movie-focused participants who regularly use a movie or
  streaming discovery product.
- Three to four technical reviewers familiar with software or ML portfolios.
- Include keyboard-only and small-screen coverage in the participant or
  expert-review mix.

Persona walkthroughs may be used before recruitment to generate hypotheses,
but their findings are qualitative simulations rather than user evidence.

### Tasks

1. Find a movie you would consider watching tonight.
2. Mark three familiar movies watched and rate them.
3. Find and change one of those ratings.
4. Save a movie and retrieve it from the watchlist.
5. Explain why one recommendation appeared.
6. Start with the Cold Start persona and create a useful personalized state.
7. Find the serving policy and model version for a recommendation.

### Measures

- Task success and abandonment.
- Time to first meaningful movie.
- Movies inspected before a decision.
- Time and errors for watched/rating/watchlist actions.
- Comprehension of feedback semantics.
- Ability to locate ML evidence.
- Confidence and satisfaction after each task.
- Keyboard, focus, and small-screen friction.

## Discovery exit criteria

Discovery is complete when:

- the primary and secondary jobs are accepted;
- the route-level design contracts are written;
- feedback semantics are honest and have named backend dependencies;
- the reference-pattern matrix records lessons rather than copied layouts;
- baseline evidence exists for desktop, tablet, and mobile;
- the first implementation bundle has explicit API and test dependencies; and
- unresolved product/model questions are recorded as decisions, not buried in
  component code.

## Method references

This document adapts the evidence-led workflow from the following public Agency
agent definitions:

- [UX Researcher](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ux-researcher.md)
- [Persona Walkthrough Specialist](https://github.com/msitarzewski/agency-agents/blob/main/design/design-persona-walkthrough.md)
- [UI Finish-Gate Reviewer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-finish-gate-reviewer.md)
