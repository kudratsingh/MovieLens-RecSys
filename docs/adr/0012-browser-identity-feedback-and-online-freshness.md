# ADR 0012 — Browser Identity, Feedback State, and Online Freshness

**Status:** Accepted

**Date:** 2026-08-21

**Implementation:** Browser identity and durability decisions are implemented:
Auth.js owns authorization code + PKCE, encrypted HttpOnly sessions,
refresh/logout, and CSRF/origin enforcement; FastAPI pins issuer, audience,
calling client, registered tenant, and persona role; bypass-disabled Playwright
and refresh-path tests provide the accepted evidence. Durable multi-state
feedback and `/me` ownership continue in their dependent bundles.

## Context

Phase 3 now has the essential authenticated serving path: Keycloak issuers map
to tenants, PostgreSQL RLS scopes each request transaction, the model sidecar
serves versioned item-item plus LightGBM results, Feast/Redis provides online
features, predictions are audited, and k6 enforces the direct API latency SLO.

The baseline frontend deliberately uses named numeric MovieLens personas so a
portfolio viewer can reproduce warm, cold, and genre-focused behavior. The new
movie-discovery design adds Browse, Library, watched state, watchlist,
dismissal, movie detail, and Quick Picks. Those concepts expose ambiguities the
rating-only API could safely avoid.

First, authentication and recommendation identity are different. The Keycloak
token `sub` names the actor, while `/users/{user_id}` names a numeric MovieLens
persona. RLS isolates tenants but intentionally does not prove that the actor
owns every row within a tenant. Today any valid tenant actor can address any
visible persona. That is useful for a demo selector but is not private account
ownership.

Second, the seeded browser client and API disagree about tokens. Keycloak's
public PKCE client is `movielens-web`, while FastAPI currently requires the
authorized-party claim to equal `movielens-api`. A real browser token therefore
cannot satisfy the current check. The browser also needs a server-side session,
token refresh, logout, CSRF/origin protection, and a Compose-compatible
public/internal issuer arrangement.

Third, `ratings` currently carries too many meanings. It is the explicit star
value, proof the movie was watched, the current-history projection, the
seen-item exclusion set, and the positive item-item seed set. It has no unique
database identity for tenant/user/movie, update is delete-plus-insert, and
there is no representation for watched-without-rating, watchlist, dismissal, or
an immutable event timeline.

Fourth, response and model freshness had different boundaries when this
decision was proposed. Successful API transactions were committed in a response
background task, so a client could receive success and immediately read the old
state. After commit, history IDs
can immediately change seen-item filtering and item-item seeds, but star values
are not passed to the deployed model. Feast features are materialized snapshots
and LightGBM receives neither rating magnitude nor an on-demand update.

Finally, the Bundle 0 online router sent any non-empty history to learned
serving. That conflicted with ADR 0011 and the shared
`COLD_START_THRESHOLD = 5`, which
define histories of 0, 1, and 3 as fallback cohorts and a history of 10 as
learned.

The frontend needs one explicit contract for what the signed-in actor may do,
what each feedback action means, when a success is durable, and which parts of
the recommendation can change immediately.

## Decision

### 1. Support production-style ownership and portfolio persona mode explicitly

Normal browser product resources use `/me/...`. A tenant-scoped mapping binds
the verified `(tenant_id, OIDC subject)` to an internal user/profile ID.

The named MovieLens persona selector remains a portfolio feature, but arbitrary
target-user access is permitted only to an actor with an explicit
`demo-impersonator` role. The UI labels both the signed-in actor and the
selected persona and does not call a persona collection the actor's private
library.

RLS remains the tenant boundary. Application authorization adds same-tenant
object ownership and role checks. A realm that is not registered in
`public.tenants` is rejected at the authenticated request boundary rather than
only on `/whoami`.

### 2. Use an authorization-code + PKCE browser flow through the BFF

The browser authenticates with Keycloak's public `movielens-web` client. The
Next.js BFF owns an HttpOnly, Secure, SameSite session, refreshes short-lived
access tokens server-side, validates Origin/CSRF on mutations, and propagates
logout. Browser JavaScript does not store or manually forward a long-lived
bearer token.

Keycloak issues an API audience to the browser client. FastAPI validates issuer,
signature, expiry, `aud=movielens-api`, and the allowed calling-client claim.
The public browser issuer and container-internal discovery/token endpoints use
a tested arrangement that preserves issuer equality without treating a
container's `localhost` as the host browser.

The primary browser E2E environment disables `DEV_AUTH_BYPASS`. The bypass may
remain only in environments already guarded by the startup safety assertion.

### 3. Introduce a current movie-state projection and an event history

A forced-RLS `user_movie_state` table becomes the product read model:

```text
tenant_id, user_id, movie_id
watched_at
rating, rating_updated_at
watchlisted_at
dismissed_at
state_version, updated_at
```

Its composite key is `(tenant_id, user_id, movie_id)`. An optional rating is
constrained at both API and database boundaries to finite half-star values from
0.5 through 5.0. Rating implies watched. State revision supports canonical
mutation responses and rejects or reconciles conflicting updates.

An append-only, forced-RLS `user_feedback_events` table records event/request
ID, tenant, actor, target user, movie, action, old/new value where appropriate,
event time, and outcome. This powers chronological activity and feedback audit;
the current projection does not pretend to be an immutable history.

Legacy imported `ratings` remain raw/source data during migration. Live serving
and demo writes move deliberately to the new projection after a tested backfill.
The raw MovieLens inputs are never rewritten.

### 4. Pin feedback transitions and model effects

- **Mark watched:** set the original `watched_at` if absent and clear watchlist.
  This is one positive implicit interaction.
- **Set or edit rating:** imply watched, update rating fields, and preserve the
  original watched timestamp. Star magnitude is explicit display feedback but
  is not a deployed-model weight.
- **Delete rating:** set rating to null; preserve watched state and unseen-item
  exclusion.
- **Remove from history:** a separate destructive action that clears watched
  and rating. Its effect on future eligibility is stated before confirmation.
- **Add or remove watchlist:** organizational only; never a positive seed,
  exclusion, feature, or training label.
- **Not for me:** set durable dismissal, clear watchlist, immediately exclude
  the title, and provide undo. It is neither positive history nor a negative
  feature/training label.
- **Undo dismissal:** clear dismissal and restore ordinary eligibility.

The model request carries positive watched history and excluded/dismissed IDs
as separate fields. A dismissed ID is removed in popularity fallback,
candidate retrieval, hydration, and final validation and is never used to
retrieve similar items.

### 5. Acknowledge mutations only after the durable boundary

No user-state mutation returns success before its transaction commits. The
successful response includes the committed canonical `MovieState`, its revision,
and a request ID. A commit failure returns failure rather than a false 2xx.

Prediction audits that are documented as fail-closed also commit before the
recommendation response. Generic read-request audits may use a best-effort or
queued policy only after that durability, loss, and retention tradeoff is
documented separately.

Optimistic UI is allowed, but it reconciles with the canonical response and
rolls back on failure. The immediate next authenticated read must observe an
acknowledged mutation.

### 6. Align cold-start routing with ADR 0011

Online serving uses popularity fallback while positive history count is below
five. Five or more positive watched interactions may use the learned path.
Tests pin histories 0, 1, and 3 to fallback and histories 5 and 10 to learned
serving when artifacts are available.

Quick Picks may show progress toward five watched signals. It must not claim a
serving-policy transition until the response policy confirms it.

### 7. Keep Phase 3 personalization claims intentionally narrow

After a committed watched/rating action, the movie can immediately:

- appear in live history;
- leave the unseen candidate set;
- contribute its ID as a seed into the already deployed item-item index; and
- affect live interaction-count popularity queries.

The action does not immediately:

- change the static item-item similarities or popularity artifact;
- update the materialized Feast snapshot;
- retrain or replace LightGBM; or
- make 1 star and 5 stars different learned inputs.

User-facing learned explanations use source evidence such as `Similar to movies
in this persona's watched history`. They do not say `Because you liked` while
star magnitude is ignored. A raw LightGBM score is not displayed as a match
percentage without a calibration decision and validation.

Prediction audits add the input state revision/hash, excluded-state hash,
feature event time, filter policy/version, candidate-source contribution, and
structured explanation required to substantiate those reasons.

### 8. Defer rating-aware learning to a separate model ADR

ADR 0002 remains authoritative for Phase 3: observed interactions are binary
positives for current training and candidate behavior. A future ranker may use
graded relevance without changing candidate retrieval, but that is a separate
decision.

The future ADR must define graded labels, low-rating negative or neutral
semantics, point-in-time rating-aware features, online freshness or overlays,
cache invalidation, offline metrics, retraining, deployment, and promotion
gates. A frontend control does not create that model behavior by itself.

## Alternatives considered

### Treat every authenticated tenant actor as every persona owner

This preserves today's minimal route shape and makes the demo selector easy.
It is rejected as the default because tenant isolation and object ownership are
different controls. It would allow one real user to mutate another same-tenant
user's library. Explicit role-gated persona mode preserves the useful demo
without making that authority accidental.

### Put access tokens in browser JavaScript and proxy them verbatim

This reduces BFF session work. It is rejected because token storage/refresh,
logout, CSRF/origin boundaries, and secret leakage become client concerns. The
Next.js server already exists as a trust boundary and should own upstream API
credentials.

### Add watchlist and dismissal columns to `ratings`

This minimizes migrations. It is rejected because a raw rating row is already
overloaded and cannot cleanly express watched-without-rating, preserved watched
time, independent state transitions, or event history. A projection plus event
log gives each concern an explicit lifecycle.

### Make low stars and dismissal online model negatives immediately

This appears intuitive in the UI but would silently change the label contract,
candidate inputs, feature freshness, cache behavior, evaluation target, and
auditing. It is rejected for Phase 3. Durable suppression produces immediate,
testable product value without pretending a new model exists.

### Keep commit in a response background task and poll from the UI

Polling can mask the read-after-write race but cannot correct a commit failure
that occurs after a reported success. It also weakens fail-closed audit claims.
Rejected for user mutations and prediction audits whose durability is part of
the contract.

### Use learned serving after the first interaction

This matches current code and creates a faster visible transition. Rejected
because it contradicts the accepted cold-start methodology and makes cohort
claims inconsistent across offline and online paths.

## Consequences

Positive consequences:

- browser identity, persona demonstration, and private ownership have clear
  meanings;
- Library and Quick Picks gain durable, independent state transitions;
- successful feedback has a real read-after-write guarantee;
- watchlist and dismissal can ship without corrupting positive history;
- cold-start copy and routing align with evaluation; and
- the frontend can explain exactly what changed now versus after retraining.

Costs and second-order effects:

- new tables, backfill, RLS policies, grants, indexes, and rollback tests are
  required;
- every affected endpoint needs same-tenant ownership tests in addition to
  existing cross-tenant canaries;
- role and subject mapping add browser-auth and demo-seeding work;
- committing before response moves durability latency inside the measured SLO;
- state revisions and input hashes expand prediction/request audits;
- BFF sessions require refresh, logout, CSRF, and issuer-networking tests; and
- the current dashboard needs a compatibility path during additive rollout.

## Risks and mitigations

### State migration changes existing demo behavior

Backfill and dual-read comparison use the clean fixtures first. The legacy route
remains until the new state projection produces identical current ratings and
seen-item exclusion for all four personas.

### Persona role-gating makes the portfolio harder to operate

Seed one explicit demo actor/role and keep persona labels visible. The runbook
tests all four personas through the same browser session.

### Commit-before-response threatens the p99 budget

Measure the durability boundary rather than excluding it. Shorten transactions,
keep external metadata/model calls outside unnecessary locks, and optimize from
pool/transaction evidence without weakening correctness.

### Dismissal leaks into positive or training inputs

Use separate request fields and enforce exclusion at every serving stage. Tests
assert a dismissed movie is neither returned nor used as a candidate seed.

### A live taste profile is mistaken for learned-model state

Label any rating-derived summary `live-ratings-v1` and expose its freshness. Do
not call it a model explanation unless it is a deployed model input with audit
evidence.

## Verification

- real `movielens-web` PKCE login, API audience, refresh, logout, expiry, role,
  unknown-tenant, and bypass-disabled browser tests;
- cross-tenant and same-tenant non-owner read/write denial;
- explicit demo-impersonator success and unprivileged failure;
- half-star/range/finite database and API constraints;
- idempotent and concurrent state mutations;
- rating edit preserves watched time; rating delete preserves watched state;
- watchlist changes no recommendation input or feature;
- dismissal and undo behavior across fallback and learned serving;
- acknowledged mutation immediately visible; commit failure never returns 2xx;
- histories 0/1/3 fallback and 5/10 learned;
- identical learned output for different star magnitudes until a rating-aware
  ADR is implemented;
- structured explanation source IDs actually contributed candidates; and
- request/prediction audits contain actor, target, state revision/freshness, and
  bounded tenant-scoped fields.

## How we will know this decision is wrong

Revisit this ADR if:

- a role-gated persona model cannot provide a usable repeatable demo;
- `/me` mapping creates more operational risk than a separately isolated demo
  tenant/application;
- the state projection cannot support required transitions without pervasive
  event replay;
- commit-before-response cannot meet the measured SLO after transaction and
  pool fixes; or
- offline evidence shows that star-aware ranker labels materially improve the
  accepted metric enough to justify the online-feature and freshness cost.
