# ADR 0002 — Movie-Discovery Experience and Progressive ML Disclosure

**Status:** Accepted

**Date:** 2026-08-21

## Context

[ADR 0001](0001-frontend-framework.md) selected Next.js, TypeScript, and
Tailwind for a portfolio surface that would make the ML system visible. The
Phase 3 baseline proved the integration: a user/persona selector, poster-backed
recommendations, history, ratings, policy/version metadata, and a server-side
proxy to the authenticated API.

The baseline does not yet provide a credible movie-discovery experience. Its
first viewport leads with project/architecture copy. A large text-only rating
studio precedes recommendations. Catalog, history, and ratings are presented as
one dashboard instead of distinct movie jobs. The frontend fetches eight
recommendations and eight history entries and exposes no scalable browse or
library contract.

This matters to the portfolio goal. A technically correct recommender shown
through an interchangeable dashboard does not demonstrate that the API and ML
contracts can support a real client. The frontend should make the recommender's
behavior visible by letting someone perform the actual loop: discover a movie,
capture feedback, and observe a new result.

The project also has two audiences:

1. a movie viewer evaluating a recommendation; and
2. a technical reviewer evaluating the system behind it.

Treating both audiences as equal-weight content in every viewport makes the
movie workflow weak and the technical evidence noisy. The information
architecture needs a deliberate primary/secondary relationship.

Finally, current documentation contains assumptions overtaken by the expanded
Phase 3 scope. ADR 0001 calls catalog search and real auth non-goals. Browser-side
Keycloak authentication is now remaining Phase 3 work, and a scalable catalog
is required by the accepted redesign. ADR 0001 remains authoritative for the
framework; this ADR supersedes those product-scope assumptions.

## Decision

### 1. Make movie discovery the primary product surface

The frontend is a movie-discovery experience with inspectable ML evidence, not
an ML dashboard decorated with posters.

The first meaningful viewport shows a movie or movie collection and an action
the viewer can take. Architecture, isolation, latency, model version, and audit
evidence remain available but do not precede the movie decision.

The shared product loop is:

```text
discover → open/save → mark watched → rate → refresh → revisit in library
```

### 2. Adopt a route-based information architecture

The persistent application shell owns authentication, selected demo persona,
primary navigation, and compact environment/model-health state.

The route model is:

- `/discover` — primary recommendation and ranked movie surfaces;
- `/browse` — searchable/filterable/paginated catalog;
- `/library` — rated, watchlist, and history collections;
- `/movies/[movieId]` — movie detail and state management; and
- `/quick-picks` — optional rapid preference collection after the core routes
  pass their finish gates.

This structure uses the nested-layout capabilities selected in ADR 0001 and
prevents one client component or all-or-nothing dashboard response from owning
the entire product.

### 3. Use a poster-first interaction model appropriate to each job

Discover uses a ranked feed/rails. Browse uses a filterable poster grid.
Library uses a compact, sortable collection. Movie detail uses a focused detail
surface. Quick Picks uses a single-decision queue with buttons and optional
gestures.

Swipe is an enhancement, never the only interaction. `Not for me`, `Watchlist`,
and `Watched` remain explicit, keyboard-accessible actions. `Watched` may reveal
a rating control.

FastAPI remains the owner of TMDB secrets and external metadata enrichment.
Poster/year fields required by grids come from the API's local metadata read
path or reviewed fixture; Browse never adds a second Next.js-owned TMDB proxy
or makes a live upstream request for every visible card.

Multiple recommendation rails are allowed only when each label maps to a real
selection rule. The frontend must not slice one ranked list into invented
personalization stories.

### 4. Put ML evidence behind progressive disclosure

Every recommendation can expose `Why this?` and `Model details`.

The compact layer contains an honest user-facing reason. The technical layer
may contain:

- serving policy and fallback reason;
- candidate, feature, ranker, and model versions;
- request ID and latency;
- prediction score; and
- the exact feature snapshot from the durable audit when requested.

Technical data should load on disclosure when it is not already present. It
must not block the first movie.

### 5. Keep feedback copy within tested model semantics

Current explicit ratings are stored, but the learned candidate path consumes
history movie IDs rather than star values. The UI may say that a rating records
history, excludes a seen movie, and requests refreshed recommendations. It must
not say a 1-star versus 5-star rating immediately teaches the learned model
different preference strength until a rating-aware backend/model contract is
accepted and tested.

`Not for me` initially means durable title suppression. Treating it as a
negative ranker feature or training label requires a cross-cutting ADR because
ADR 0002 at the backend level treats ratings as implicit positive interactions.

Watchlist state is organizational. It does not influence the model unless a
later decision explicitly changes that contract.

### 6. Keep authenticated actor and recommendation subject distinct

The Phase 3 portfolio demo uses authenticated access plus named synthetic
personas. The selected numeric MovieLens persona is the recommendation subject;
it is not automatically the Keycloak token subject.

Until a subject-to-profile mapping and object-level authorization contract
exist, the UI must label persona impersonation clearly and must not imply that
the selected persona library is a private account owned by the signed-in human.
Any route that permits persona switching in a non-development environment must
be explicitly role-gated.

### 7. Formalize a dark-first, accessible design system

The existing dark/amber MovieLens identity becomes a semantic token system for
surface, text, accent, focus, success, warning, destructive, degraded, poster
overlay, spacing, typography, and motion.

Dark-first is intentional for poster-led discovery. A light-theme toggle is not
required for the first delivery. Accessible contrast, forced-color resilience,
visible focus, reduced motion, and complete keyboard operation are required.

Motion explains state change; it is not evidence of polish on its own.

### 8. Require a written design contract and a hard finish gate

Every route has a contract naming:

- user and job;
- first-read object;
- primary action;
- density and hierarchy;
- interaction model;
- responsive priority;
- forbidden defaults; and
- required finish evidence.

The implementation receives PASS or HOLD at mobile and desktop sizes. Loading,
empty, error, focus, disabled, auth, and metadata-fallback states are part of
the gate.

## Alternatives considered

### Keep the baseline and apply visual polish

This would preserve the large architecture hero, rating wall, and single-page
dashboard while improving color, motion, and spacing. Rejected because the
problem is information architecture and task hierarchy, not decoration. It
would remain a generic portfolio dashboard with movie content.

### Keep one page but reorder its sections

Moving recommendations above ratings would be an improvement and is a valid
short-term patch. Rejected as the target architecture because Browse, Library,
movie detail, independent loading, URL-preserved filters, and future Phase 4–6
surfaces need real route boundaries.

### Make swipe the primary product

Focused classification can be useful for cold-start onboarding, but it hides
catalog breadth, limits comparison, and creates accessibility problems when
treated as the contract. Rejected as the primary experience. Accepted as an
optional Quick Picks mode with equivalent buttons and keyboard support.

### Clone a streaming-service homepage

Poster rails are appropriate for scanning ranked content, but a direct clone
would imply playback, provider inventory, editorial rows, and behavioral
signals the project does not own. Rejected. The project adopts the ranked-row
lesson only where a real policy supports the label.

### Separate the technical portfolio into another application

This would keep the movie UI clean, but it would weaken the project's core
claim that a real client can expose the engineering behind its predictions.
Rejected. Progressive disclosure preserves both audiences in one product.

### Build private end-user accounts now

Mapping Keycloak subjects to persistent MovieLens-style profiles, ownership,
and per-user authorization would produce a more consumer-like product. Rejected
for this Phase 3 redesign because the project's immediate goal is a repeatable
persona-based portfolio demo. The distinction is documented so this option can
be added deliberately later.

## Consequences

### Positive

- Movie discovery becomes legible in the first viewport.
- Rating/history no longer obscure predictions.
- Routes match distinct user jobs and can fail/load independently.
- ML evidence becomes more credible because it is attached to a prediction.
- API gaps become explicit instead of being hidden by a fixed 24-movie form.
- The design can grow into Phase 4 explanations, Phase 5 health, and Phase 6
  champion/challenger views without returning to one dashboard.
- PASS/HOLD evidence reduces subjective polish debates.

### Costs

- The backend needs new catalog, detail, library, and feedback contracts.
- The demo catalog, interaction graph, features, and serving artifacts need
  coordinated expansion; adding visible titles alone does not make them model
  candidates.
- Poster-heavy Browse cannot synchronously fetch TMDB metadata per card and
  therefore needs a local/persisted metadata read path.
- Browser-side authentication and persona impersonation need a clear role and
  ownership boundary.
- Frontend component, contract, browser, accessibility, and visual tests become
  required dependencies.
- The final recorded walkthrough should wait for the redesigned core routes to
  pass rather than documenting a transient UI.

## Risks and mitigations

### The frontend overpromises personalization

**Mitigation:** Copy and tests follow the accepted feedback semantics. Any
rating-aware or negative-feedback model behavior gets a cross-cutting ADR.

### A larger catalog remains disconnected from predictions

**Mitigation:** Separate Browse coverage from prediction coverage. Expand
background interaction fixtures and regenerate serving artifacts with measured
candidate coverage.

### TMDB makes Browse slow or fragile

**Mitigation:** Poster/year data for card lists comes from a local read model or
reviewed fixture. Rich detail may refresh separately. Browse never fans out to
TMDB once per card on its critical path.

### The portfolio loses its technical differentiation

**Mitigation:** Preserve `Why this?`, model versions, audits, feature snapshots,
fallbacks, and latency as contextual evidence attached to recommendations.

### Persona impersonation looks like account ownership

**Mitigation:** Show the selected persona in the persistent shell, use explicit
demo language, and require role-gating outside development until real ownership
exists.

### Quick Picks adds delight before core flows work

**Mitigation:** Quick Picks is sequenced after Discover, Browse, Library, movie
detail, and their tests.

## How we will know this decision is wrong

Revisit this ADR if:

- viewers find a useful movie more slowly than in the baseline;
- separate routes make the demo harder to understand or operate;
- technical reviewers cannot locate model/audit evidence;
- most users prefer a compact rating-first workflow over poster-led discovery;
- the catalog/data cost overwhelms the value of a portfolio frontend;
- mobile navigation makes the core loop require more steps without improving
  comprehension; or
- the project changes from a persona-based portfolio demo into a private
  consumer service with real account ownership.

## Validation

The governing evidence and PASS/HOLD criteria live in:

- [Product discovery](../../frontend/product-discovery.md)
- [Design contracts](../../frontend/design-contracts.md)
- [Testing strategy](../../frontend/testing-strategy.md)
- [Baseline evidence](../../frontend/baseline-evidence.md)
- [Implementation plan](../../frontend/implementation-plan.md)
