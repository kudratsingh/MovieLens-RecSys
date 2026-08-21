# Movie-discovery frontend: testing and finish-gate strategy

**Status:** Bundle 0 contract

**Last updated:** 2026-08-21

## Testing principles

1. Test user jobs, not only component rendering.
2. Keep model/feedback claims tied to backend behavior.
3. Treat loading, empty, error, fallback, focus, and narrow-screen states as
   product states rather than cleanup.
4. Test every tenant-scoped collection for cross-tenant leakage.
5. Ship tests with each implementation bundle; do not defer all browser testing
   to the polish PR.
6. Require visible evidence at named viewports before the UI finish gate passes.

## Research validation

### Five-second test

At 390×844, after the first stable render, ask:

1. What is this?
2. Is it for you?
3. What should you do first?

PASS requires a movie-discovery answer and a visible movie action without
prompting. Identifying the page only as an ML demo is a HOLD.

### Moderated task protocol

Run the tasks from [product discovery](product-discovery.md) with movie-focused
and technical-review participants. Capture:

- completion and abandonment;
- time on task;
- errors and recovery;
- movie scan count before a decision;
- feedback-semantics comprehension;
- spontaneous comments and confidence; and
- whether ML evidence is discoverable but non-disruptive.

Persona simulations may identify likely friction before testing, but they do
not count as validation data.

## Automated test layers

### Static and build checks

Retain the existing frontend checks:

```bash
npm run lint
npm run typecheck
npm run build
```

Add a formatting check only when a formatter is explicitly adopted. Do not
claim frontend behavior is covered by these three checks.

### Component and interaction tests

Adopt Vitest, React Testing Library, `@testing-library/user-event`, and
`jest-axe` or the Vitest-compatible axe integration.

Required component coverage:

- poster success, missing URL, and image-error fallback;
- long title, missing year, empty genres, and missing overview;
- watched, unrated, rated, watchlisted, and suppressed states;
- 0.5–5 rating selection if half-star input is exposed;
- optimistic mutation, pending, success, rollback, and retry;
- `Why this?` disclosure and technical data formatting;
- filter selection, clear, serialization, and keyboard operation;
- pagination/load-more continuity;
- rail controls and `See all` path;
- movie-detail open/close and focus restoration;
- Quick Picks buttons, keyboard shortcuts, gesture parity, undo, and reduced
  motion;
- authenticated, expired-token, unknown-user, and forbidden states; and
- empty collection and empty search results.

Use semantic queries. A test that only finds a Tailwind class is not a user
behavior test.

### Route-handler and contract tests

Test the Next.js backend-for-frontend boundary independently from React:

- query/cursor forwarding;
- authorization forwarding and missing-auth handling;
- upstream status/body preservation;
- independent recommendation, catalog, history, and library failures;
- timeout and malformed-body handling;
- cache policy for user-scoped and public metadata;
- response schema validation; and
- no server-only secret in the client bundle.

Prefer generated or runtime-validated types from FastAPI's OpenAPI schema. If
manual TypeScript interfaces remain, add contract fixtures that are validated
by both Python and TypeScript tests.

### Backend unit and integration tests

Required backend coverage for the proposed contracts:

- stable cursor pagination and deterministic tiebreakers;
- case-insensitive title search and genre/year/status filters;
- filter composition and empty results;
- movie detail with and without TMDB metadata;
- metadata cache/persistence behavior and upstream failure;
- rating create, update, single delete, validation, and read-after-write;
- watchlist add, list, remove, idempotency, and watched transition;
- title suppression add/remove and exclusion from results;
- history pagination and ordering;
- explanation/reason contract;
- rating-aware behavior only if an accepted model contract implements it; and
- authenticated audit events for mutations and library reads as required by
  the generic request-audit bundle.

### Tenant-isolation tests

Every new user-scoped endpoint must be added to the tenant-isolation matrix:

- catalog rating overlay;
- rated library;
- watchlist;
- history;
- suppression/preferences;
- rating mutation/delete;
- watchlist mutation; and
- movie detail fields that include user state.

Authenticate the same numerical user/movie IDs in two tenants and prove no
rating, history, watchlist, preference, audit, or model assignment crosses the
boundary. Global movie metadata may be shared only when the schema and ADR
explicitly define it as non-tenant data.

### End-to-end browser tests

Adopt Playwright and run it against the seeded Compose demo. The minimum suite:

1. Authenticate through browser-side Keycloak.
2. Open Action Fan and receive learned item-item plus LightGBM results.
3. Open a recommendation and inspect `Why this?`.
4. Mark a movie watched and rate it.
5. Observe pending/success state and fetch after the mutation is durably
   visible.
6. Verify the movie appears in Rated/History and disappears from unseen
   recommendations.
7. Add/remove a watchlist movie.
8. Search/filter Browse and preserve state after movie detail.
9. Reset Cold Start and verify the popularity fallback.
10. Add enough history to leave the intended cold-start state and verify the
    documented policy.
11. Fail TMDB metadata and prove poster fallback/layout stability.
12. Fail one backend surface and prove unrelated surfaces remain usable.
13. Expire or omit auth and verify the login/recovery path.
14. Exercise tenant-isolation canaries through the browser-facing routes.

The test must distinguish `a refresh request was issued` from `the committed
mutation is visible`. FastAPI now commits the authenticated request transaction
before returning success; retain regression coverage proving the immediate next
read observes the acknowledged state and commit failure never returns 2xx.

## Visual-regression matrix

Capture at least 390×844, 768×1024, and 1440×1000.

| Route/state | Mobile | Tablet | Desktop |
|---|---:|---:|---:|
| Discover learned result | Required | Required | Required |
| Discover cold-start fallback | Required | Optional | Required |
| Discover loading | Required | Optional | Required |
| Discover API error | Required | Optional | Required |
| Poster failure | Required | Optional | Required |
| Browse default | Required | Required | Required |
| Browse filters open/active | Required | Optional | Required |
| Browse empty results | Required | Optional | Required |
| Library Rated | Required | Required | Required |
| Library Watchlist empty/populated | Required | Optional | Required |
| Library History long list | Required | Optional | Required |
| Movie detail | Required | Required | Required |
| Quick Picks | Required | Optional | Required |
| Auth required/expired | Required | Optional | Required |

Each capture uses the same seeded persona, dataset revision, TMDB mode, and
browser font configuration. Store evidence as specified in
[baseline evidence](baseline-evidence.md).

## Accessibility gate

PASS requires:

- zero critical or serious axe violations;
- one logical heading hierarchy per route;
- named landmarks and navigation;
- visible focus in every interactive state;
- complete keyboard operation without gesture dependency;
- at least 44×44 CSS-pixel touch targets for primary mobile actions;
- state communicated by text/semantics as well as color;
- useful alternative text policy for informative posters and empty alt text for
  decorative duplicates;
- live-region mutation feedback that does not chatter during bulk loading;
- focus restoration after drawers, sheets, and optimistic mutations;
- reduced-motion support; and
- no horizontal page overflow at 320px or above.

## Performance gate

On the seeded demo with a warm application process:

- LCP ≤ 2.5 seconds at the agreed mobile profile;
- CLS ≤ 0.1;
- immediate visual acknowledgement of a user action within 100 ms;
- poster dimensions reserved before image load;
- below-fold posters lazy-load;
- catalog endpoints do not synchronously fan out to TMDB per visible card;
- pagination has a bounded maximum page size;
- technical audit/features are loaded on disclosure rather than blocking the
  first movie; and
- the existing authenticated recommendation p99 contract remains below 100 ms.

Frontend measures must not be conflated with the serving-only k6 latency
metric. Report browser and API timing separately.

## UI finish gate

Review order:

1. **Product legibility:** Is a movie and a movie decision visible first?
2. **Hierarchy:** Does visual weight follow the viewer's decision?
3. **Pattern fit:** Does every rail, grid, tab, drawer, and gesture serve a
   named job?
4. **States:** Are loading, empty, error, selection, pending, disabled, focus,
   fallback, and auth states intentional?
5. **Responsive behavior:** Does mobile preserve the job rather than stack
   desktop panels?
6. **Implementation fidelity:** Are tokens, content, types, and interactions
   consistent?
7. **Truthfulness:** Does every personalization statement match tested backend
   behavior?

### PASS criteria

- A first-time viewer identifies the product and first action in five seconds.
- The first viewport contains a movie, not an architecture panel.
- A user can browse, save, mark watched, rate, and find that state again.
- A technical reviewer can reach the policy/model/audit evidence in no more
  than two deliberate actions.
- No forbidden default from the design contracts remains without a documented
  product reason.
- All required component, contract, backend, tenant, browser, visual,
  accessibility, and performance checks pass.
- Desktop and mobile evidence receive an explicit PASS.

### HOLD conditions

- Rating or personalization copy is stronger than model behavior.
- Swipe is required to complete a task.
- Missing posters collapse movie identity or layout.
- One backend failure blanks unrelated content.
- A new user-scoped endpoint lacks tenant-isolation coverage.
- Browse is still a fixed text list rather than a scalable catalog contract.
- Critical mobile or accessibility states are undocumented or untested.

## Required PR evidence

Every frontend PR includes:

- the governing design-contract section;
- before/after captures for affected viewports;
- component/contract/e2e tests added or updated;
- accessibility result;
- performance impact where images/data volume change;
- explicit loading/empty/error behavior; and
- PASS/HOLD status with any remaining required work.

## Method reference

The gate adapts the public
[UI Finish-Gate Reviewer](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-finish-gate-reviewer.md)
method: product lens, comparable evidence, a written design contract, observable
verification, and a hard PASS/HOLD decision.
