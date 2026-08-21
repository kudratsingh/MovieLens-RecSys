# API contracts

`openapi.json` is the committed, generated contract for the authenticated
FastAPI surface. Do not edit it by hand.

Regenerate after changing a route or Pydantic model:

```bash
make api-contract
make web-api-types
```

CI and local verification use:

```bash
make api-contract-check
make web-api-types-check
```

The artifact includes stable operation IDs, the Keycloak bearer-security
scheme, shared error responses, and request/response constraints. Generated
frontend types must consume this file rather than importing Python models or
maintaining a separate handwritten interpretation.

Bundle 2 adds the selected-persona Library and feedback resources:

- `GET /users/{user_id}/library` with bounded, filter-bound keyset cursors;
- `GET /users/{user_id}/taste-profile` labeled `live-ratings-v1`;
- independent `watched`, `rating`, `watchlist`, and `dismissal` PUT/DELETE
  resources; and
- canonical mutation responses containing a revision and idempotency request
  ID. Rating deletion preserves watched state; deleting watched state is the
  separate history-removal resource.

Bundle 3 adds `listDemoCatalog` and `getMovieDetail`. Catalog cursors are
opaque, versioned, and bound to the active filter/sort query. Browse and detail
metadata come from the persisted local read model, overlay the complete durable
movie state, and never trigger live per-card TMDB calls.

The Bundle 6 serving prerequisite extends two operations additively:

- `recommendMovies` gains `serving_policy` — `name`, `learned`,
  `positive_signal_count`, `threshold`, `reason`, `score_scale`,
  `filter_policy`, and `excluded_count`. The flat `policy` string is retained
  for existing clients and always equals `serving_policy.name`. `score_scale`
  states what `items[].score` is (`lightgbm-rank-score` or
  `tenant-interaction-count`); it is an ordering, not a probability, and must
  not be rendered as a match percentage.
- `listRecommendationAudits` gains `correlation_id`, `input_state_revision`,
  `input_state_hash`, `exclusion_hash`, `positive_signal_count`,
  `excluded_count`, `filter_policy`, `feature_event_time`, `candidate_sources`,
  and `reason`, and each prediction gains `candidate_source` and
  `seed_movie_id`. Audits written before this change report `unknown`
  attribution rather than a fabricated source.

Request correlation applies to the whole surface rather than one operation, so
it is not modelled in the schema: every response carries `X-Request-ID`. A
caller-supplied value is adopted when it is 1–128 printable ASCII characters
with no whitespace, and otherwise replaced with a minted UUID — a malformed
header never fails the request. Recommendation audits store the echoed value
as `correlation_id`; `request_id` remains the audit row's own UUID identity so
a replayed correlation header cannot collide with an existing row.
