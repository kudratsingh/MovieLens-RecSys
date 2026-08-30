# ADR 0012 — Browser Identity, Feedback State, and Online Freshness

**Status:** Accepted

**Date:** 2026-08-21

**Implementation:** Browser identity and durability decisions are implemented:
Auth.js owns authorization code + PKCE, encrypted HttpOnly sessions,
refresh/logout, and CSRF/origin enforcement; FastAPI pins issuer, audience,
calling client, registered tenant, and persona role; bypass-disabled Playwright
and refresh-path tests provide the accepted evidence.

_Updated 2026-08-29:_ durable multi-state feedback landed with Bundle 2
(PR #48) — migration 0010's forced-RLS `user_movie_state` and append-only
`user_feedback_events`, with the transition table below written once in
`web/lib/movie-state/` (PR #61) as the only write path any surface uses.
**`/me` subject-to-profile ownership remains open**: the product still selects a
persona explicitly, gated behind the confidential service client or the
`demo-impersonator` realm role, and it is carried as product-track item (b) in
CLAUDE.md's remaining-Phase-3 list. One later addition rides on this ADR's
semantics rather than changing them: the `user_preferences` row added in
PR #81 (migration 0014) is **presentation only** — it decides what the client
features, reaches neither retrieval nor the ranker nor the exclusion set, and
leaves a request's audit unchanged.

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

---

## Note — 2026-08-28: a skip is not a signal, and a preference is not a model input

Appended rather than folded into the sections above: the decisions this ADR
records have not changed, and this names where a new mechanism sits relative to
them.

Discover's featured slot may now show a title the viewer has already added to
their watchlist, with a `Skip` control beside it, and after three skips in a
session the viewer is offered a durable preference that stops watchlisted titles
being featured at all. Two things about that need to be pinned here, because
both of them are places where a presentation feature could quietly become a
feedback feature.

**A skip records nothing.** It advances the client's queue cursor and issues no
request. It is not a dismissal (which is a durable exclusion and is written), not
a watched signal, not a rating, and — as with dismissal in the section above —
never a training negative. The state-transition table is unchanged: there is no
transition for it, because it changes no state. The announcement says so in the
viewer's terms (`still on your watchlist`), the skipped title keeps its place in
the ranked rail, and its `user_movie_state` row is untouched, revision included.
The unit test asserts the guarantee the only way it can be asserted: the press
produces no HTTP request at all.

**The preference is per-persona presentation state.** It lives in its own forced-
RLS table, `user_preferences` (migration 0013), with one typed column and the
same optimistic-revision write contract the movie-state resources use. No serving
path reads it: `src/serving/orchestration.py` and `recommendations.py` do not
import it, the candidate and exclusion inputs are computed exactly as before, and
two responses for the same persona are byte-identical whether featuring is on or
off. The re-ordering happens in the client, on the response the API already sent.

That separation is deliberate and worth defending. Making the preference a
serving filter would be the obvious "improvement" and it would break the audit:
`recommendation_audits` records the exclusion digest and the excluded count for
the set the API returned, and a title dropped by a viewer's display preference
is not an exclusion in that sense. The prediction log would then describe a set
nobody was served. Keeping the preference out of the serving path keeps the
audit true, keeps the k6 gate measuring the same workload, and keeps the answer
to "why this?" a statement about the model rather than about a settings row.

The same rule bounds the copy. Neither the skip nor the setting may be described
as teaching the recommender anything, and both surfaces say what they actually
are: the setting's own note ends `This changes what you are shown, not what the
recommender learns.`

If a later ADR wants watchlist state to affect retrieval or ranking, that is a
serving decision with an offline evaluation behind it, not an extension of this
one — and it would need the audit vocabulary in `src/serving/policy.py` extended
to name the new filter rather than reusing `watched-and-dismissed-excluded-v1`.

---

## Note — 2026-08-29: the generic request audit is inline, and what that costs

Appended rather than folded into the sections above: §5's rule has not moved — a
mutation is acknowledged only after its transaction commits, and a prediction
audit still commits before the recommendation response. This is the "documented
separately" that §5 makes a precondition for auditing every *other* endpoint,
and it records what the measurement actually says, which alternative was
rejected and on what grounds, and the way back out.

**What was missing.** `recommendation_audits` (migrations 0008 and 0012) is the
replay record for one route. It exists so "why did this user see that title" has
an answer after the state has moved on, and it is deliberately heavy: exact
ranked items and scores, the online feature values behind them, artifact
versions, the input-state and exclusion digests, per-stage latencies. Every
other authenticated operation — catalog, movie detail, the Library and Seen
reads, the eight movie-state mutations, preferences, personas, features, the
audit reads themselves, `/whoami` — wrote nothing at all. The Phase 3 "Real
auth" scope and non-negotiable #8 both describe a row per authenticated request;
`docs/deployment-runbook.md` §14 and `docs/architecture.md` said plainly that
this was intent rather than description. Migration 0017 and
`src/serving/request_audit.py` make it description.

**The cost is not a second `fdatasync`; on a read it is the first one.** The
tempting argument is that the row is free because the request already commits,
and that argument is half wrong in a way worth writing down. `AuthMiddleware`
opens `self._engine.begin()` for *every* authenticated request, read included,
and awaits the commit before returning the response. But a Postgres transaction
that only reads is assigned no transaction id and writes no WAL, so its commit
flushes nothing. Adding one insert turns that free commit into a real one. So:

- On the eight movie-state mutations, preferences writes and the rating writes,
  the WAL flush is already paid for and the marginal cost of the audit is one
  ~120-byte insert into a table with two right-hand-leaf indexes.
- On every read, the marginal cost is that insert **plus** one `fdatasync` the
  request did not previously pay.

ADR 0010's 2026-08-28 note is where the second number comes from, because that
investigation had to measure exactly this: the same commit cost 3.15 ms on the
CI runner whose block device was the problem, 0.21 ms on the runner that was
not, and lands near 0.2 ms on tmpfs. On the production box it is a property of
that box's disk, which is why `make prod-verify` prints the audit-table
percentiles as an SLI and the canary runs it every thirty minutes. A read that
was 4 ms becomes a read that is 4 ms plus whatever that deployment's storage
charges for one flush; on the Hetzner CX22's NVMe that is sub-millisecond, and
if it ever stops being, the setting below is how it gets turned off in one
deploy rather than one migration.

**Alternative (b) — an in-process queue flushed off the request path — was
rejected on isolation, not on latency.** It is the obvious way to buy the
telemetry without the flush, and it loses the thing this project is least
willing to lose. A background flusher holds no request, so it holds no
principal; to write a tenant-scoped row under forced RLS it must either open one
transaction per tenant per batch and set `app.tenant_id` from something it
carried along itself, or hold a role that bypasses RLS. The second is
disqualifying on its face — non-negotiable #9 makes cross-tenant leakage the
highest-severity bug class, and an audit table written by a role RLS does not
apply to is a new way to get it wrong. The first is not much better: it
re-implements, in a task with no verified token, the one decision ADR 0008
deliberately put in exactly one place. And the queue buys real loss — a crash
takes the unflushed batch with it, so the audit an operator reaches for after an
incident is the one most likely to be missing — plus backpressure, ordering and
retention questions that a table with no readers yet has not earned. `off` is
kept as the escape hatch instead, because "write it durably or do not write it"
is a decision an operator can reason about and "we probably have most of them"
is not.

**Decision: (a), inline, on the request's own transaction, for reads and
mutations alike.** The row is written by `RequestAuditMiddleware`, registered
inside `AuthMiddleware` so it uses `request.state.db` and commits with the
request, and inside `RateLimitMiddleware` so a throttled request writes nothing
— ADR 0014's limiter must not turn a burst it is shedding into a write per
refusal. It is a `BaseHTTPMiddleware` rather than the raw ASGI form
`RequestIdMiddleware` and the limiter use, and for a reason specific to
ordering: a raw middleware forwards `http.response.start` the instant the router
emits it, which releases the outer `call_next` and lets the commit race the
insert. Holding the response until the row is written is what
`BaseHTTPMiddleware` already does correctly.

**Recommendations are skipped rather than duplicated.** A recommendation already
writes a strictly richer row, and a second insert on that route would sit inside
the one path with a latency SLO for no information gain. The alternative — a
generic row carrying a pointer to the prediction audit — costs the same insert
to store a fact the correlation id already carries, since both tables key on it.
An operator asking "what did this tenant do" unions two tables and joins them on
`correlation_id`; that is written down here and in `docs/api/overview.md` rather
than hidden behind a view.

The skip is checked two ways, because a route template is not always available.
`/users/42/recommendations/` with a trailing slash matches no route at all — the
router answers it with a redirect and never records a match — so the template
check alone sees "unmatched" and would write a row, while the prediction audit's
own path regex still matches and writes its own. Checking the path as well is
what makes "one request, one audit table" true off the canonical path too.

**A failing handler is audited out of band.** If a handler raises,
`AuthMiddleware` rolls the transaction back, and it must: a mutation that raised
mid-flight cannot be allowed to become durable because we wanted its audit. So
that one row is written on a fresh short-lived transaction, as the same
RLS-applied role, with `app.tenant_id` set from the same verified principal, and
the original exception is re-raised unchanged. The request's own semantics do
not move a millimetre; only the audit outlives the rollback. A failure of that
write is logged and swallowed, because the exception the operator needs is the
one that broke the request.

That transaction runs on a **separate one-connection pool**, not on the engine
serving requests, and the separation is load-bearing rather than tidy. The
failing request is still holding one of the request pool's connections until
`AuthMiddleware` unwinds, so borrowing from the same pool means a burst of
simultaneous failures — a bug in a shared helper, a dependency going down — can
check out every connection and then block waiting for one more that nobody can
release, for the full thirty-second default `pool_timeout`, on the shared thread
pool. A fast 500 would become a stalled worker. On its own pool with a
one-second timeout the worst case is a dropped audit row and a log line, which
is the cheaper thing to lose while the service is already failing.

**What the row does not carry, and why that is the design.** No request body, no
headers, no query string. `endpoint` is the matched route template
(`/users/{user_id}/catalog`), never the concrete path — a concrete path would
mint one endpoint value per persona and per movie id, and storing `?q=` would
put whatever a viewer typed into the search box into a durable table. What is
stored is the shape of the call, not its content: tenant, actor, persona,
template, method, status, outcome, latency, the echoed correlation id. `/healthz`
and `/readyz` are not audited — they carry no tenant to scope a forced-RLS row
to — and neither is a 401, for the same reason.

**Reversible without a migration.** `REQUEST_AUDIT_MODE=inline` (the default)
installs the middleware; `REQUEST_AUDIT_MODE=off` installs nothing and leaves
the table in place. There is deliberately no third value: the queued mode is the
one this note rejects, and offering it as a setting would be offering the
isolation problem as a runtime choice.

**What measures it, honestly.** CI's `synthetic-load-smoke` remains the SLO's
only authority, and its pinned workload is the recommendation path — which this
middleware skips. So what that gate proves about this change is narrower than it
looks: it proves the middleware's *presence* in the chain costs the measured
path nothing. The row's own cost shows up in `synthetic/load/pages.js`, whose
per-step budgets cover catalog pages, Library reads and the
mutation-plus-immediate-read sequence, and in `synthetic/load/reliability.py`,
which now has a durable row to trace on every endpoint rather than only on
recommendations. Saying so is the point: a gate that cannot see a change should
not be cited as evidence the change was free.

**What is still owed.** Retention. `request_audits` grows by one row per
authenticated request and nothing prunes it, which is the same open question
`feature_store.*` has in `docs/deployment-runbook.md` and belongs with it rather
than with this note. The `model_version` column is null on every route today,
because the routes that run a model are the ones skipped; Phase 6's per-tenant
champion routing is what fills it, and `MODEL_VERSION_STATE_KEY` is where a
handler will put it. And the read endpoint is persona-scoped
(`GET /users/{user_id}/request-audits`), so rows that address no persona —
`/whoami`, `/personas` — are written and are readable to an operator holding
`admin_user`, but are not reachable through the API. A tenant-wide operator view
is Grafana's job (ADR 0013 keeps admin dashboards out of this frontend), not a
second endpoint here.

If a later ADR wants this table on the recommendation path after all — one row
per request everywhere, with the prediction audit reduced to a detail table
hanging off it — that is a schema decision with a measured p99 behind it, not an
extension of this one.
