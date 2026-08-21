# Durable feedback and Library contract

**Status:** Implemented in Bundle 2

**Last updated:** 2026-08-21

## Persistence boundary

`user_movie_state` is the current tenant/user/movie projection. Its composite
primary key, forced RLS, tenant-leading indexes, half-star database constraint,
watchlist/dismissal exclusivity, and monotonically increasing `state_version`
make the canonical state explicit.

`user_feedback_events` is append-only for runtime roles. Each event records the
tenant, idempotency request ID, verified actor subject, selected persona, movie,
action, old and new canonical snapshots, resulting revision, outcome, and time.
Runtime roles have no UPDATE or DELETE grant on this table.

Migration 0010 reads the latest deterministic source rating for each
tenant/user/movie, writes it to the new projection, and emits a provenance
event. It never updates or deletes `ratings`; those imported MovieLens rows
remain reproducible offline inputs. The demo seeder writes the same projection
and deterministic provenance events for fixtures created after migration.

## State transitions

| Action | Canonical effect | Serving/model meaning now |
|---|---|---|
| Mark watched | Preserve the first watched time; clear watchlist | Positive history ID and unseen exclusion |
| Set/edit rating | Imply watched; preserve first watched time; update rating time | Same positive ID; star magnitude is display feedback only |
| Delete rating | Clear rating fields only | Movie remains watched and excluded |
| Remove history | Clear watched and rating fields | Removes the positive history signal |
| Add/remove watchlist | Save or organize an unwatched title | No positive seed, exclusion, feature, or training effect |
| Not for me / undo | Set/clear dismissal and clear watchlist on dismissal | Eligibility exclusion only; not a negative training label |

Every mutation accepts an optional UUID `Idempotency-Key` and an optional
`expected_revision`. Reusing a key for the same target/action replays the
original canonical result without another event. Reusing it for a different
mutation or writing a stale revision returns `409`. The authenticated request
transaction commits before the response is released.

## Library resources

`GET /users/{user_id}/library` provides Rated, Watchlist, and History tabs with
bounded pages, stable movie-ID tie-breakers, opaque versioned cursors, counts,
title filtering, and recent/title/rating sorts where meaningful. A cursor is
bound to its tab, sort, and query and is rejected when reused for another view.

`GET /users/{user_id}/taste-profile` calculates genre counts and averages from
the current projection on each read. Its source is `live-ratings-v1`, and its
copy explicitly says it is not a deployed-model explanation. It does not claim
Feast freshness, LightGBM attribution, or rating-aware learning.

The current routes are selected-persona portfolio resources. They require the
confidential service client or `demo-impersonator` role and the target must be a
registered synthetic persona. They are never labeled as the actor's private
library. Normal `/me` ownership remains gated on Bundle 1's subject-to-profile
mapping and browser-session E2E.

## Frontend behavior

`/library` labels the selected persona, preserves tab/sort/filter state in the
URL, loads cursor pages independently, and presents explicit empty and upstream
error recovery. Feedback is optimistic but reconciles to the canonical state;
failure restores the prior collection and announces rollback. After rows move
or disappear, focus returns to the initiating control when possible and then
to the active tab/collection heading.

## Verification

- transition, idempotency, revision, event-count, and live-read unit tests;
- watchlist-no-effect and dismissal-not-positive serving tests;
- stable/filter-bound cursor, counts, search, and taste-summary tests;
- migration source-boundary, RLS/grant/index/constraint checks;
- generated OpenAPI and TypeScript drift gates; and
- tenant integration canaries for Library read, mutation, immediate read, and
  cross-tenant denial when the Compose stack is available.
