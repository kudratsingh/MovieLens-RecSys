# API overview

A readable tour of the authenticated FastAPI surface. The authority is
[`openapi.json`](openapi.json), which is generated and committed; this page is
written from it and is here so a reader can see the shape of the service without
loading a schema browser. Where the two disagree, the schema is right — see
[`README.md`](README.md) for how it is regenerated and how CI catches drift.

**20 paths, 25 operations.** Two of them are unauthenticated.

## Authentication

Every operation except `/healthz` and `/readyz` requires
`Authorization: Bearer <token>`, where the token is a Keycloak access token
carrying `aud=movielens-api`. Three things about it are load-bearing:

- **The tenant comes from the token's issuer, never from a claim the client
  sets** (ADR 0007). One Keycloak realm per tenant; the middleware derives the
  tenant from the verified issuer and rejects a realm with no row in
  `public.tenants`.
- **The calling client must be in the allow-list.** `azp` has to be one of
  the configured authorized parties — `movielens-api` and `movielens-web` in
  the development realms, plus `movielens-verify` (the release and canary
  identity) in production.
- **Selecting an arbitrary persona is a privilege.** `{user_id}` is a MovieLens
  persona inside the caller's tenant; reaching one that is not the caller's own
  requires the confidential service client or the `demo-impersonator` realm role
  (ADR 0012).

Underneath, every authenticated request runs inside one Postgres transaction
with `SET LOCAL app.tenant_id`, so row-level security is the enforcer of last
resort rather than application filtering (ADR 0008). The transaction commits
**before** a successful response is returned, so a 2xx is never issued for a
write that could still fail to become durable.

## Cross-cutting response conventions

**Request correlation.** Every response carries `X-Request-ID`. A caller-supplied
value is adopted when it is 1–128 printable ASCII characters with no whitespace,
and otherwise replaced with a minted UUID — a malformed header never fails the
request. Recommendation audits store the echoed value as `correlation_id`, while
`request_id` stays the audit row's own identity so a replayed header cannot
collide with an existing row.

**Rate limiting** (ADR 0014). Every authenticated response carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | The bucket's **capacity** — the largest instantaneous burst, not a per-minute quota |
| `X-RateLimit-Remaining` | Tokens left, floored (a partial token cannot serve a request) |
| `X-RateLimit-Reset` | Whole seconds until the bucket is full again |

Exhausting the bucket answers `429` with `Retry-After` (whole seconds, never
`0`) and an `ErrorResponse` body naming the policy. The bucket is keyed on
`(tenant_id, sub)` from the verified token — never on a client address, because
behind the edge every request arrives from a proxy. It lives **in the worker
process**, so a service running N uvicorn workers admits up to N times the
configured rate for one subject and `Remaining` is not monotonic across a
sequence of requests from one client. Treat the headers as one worker's view of
one caller's allowance, not a cluster-wide quota. `/healthz` and `/readyz` are
exempt and carry no such headers.

**Shared error responses.** `401` missing or invalid token · `403` actor not
authorized · `404` persona or movie does not exist · `409` idempotency or state
revision conflict · `422` validation error, or a refused state transition ·
`429` rate limit exceeded · `500` request transaction failed. `400` additionally
means an invalid parameter or a cursor that does not match the query it is used
against.

**Error codes.** Every deliberate 4xx renders `ErrorResponse`: a `detail`
sentence, and — wherever one status covers more than one condition a caller has
to tell apart — a stable `code`. `detail` is prose meant for a person and may be
reworded at any time; `code` is the contract. Read the code first and fall back
to the status; where the status is unambiguous no code is sent at all.

| Status | `code` | What happened | What a client does |
|---|---|---|---|
| `409` | `revision_conflict` | The `expected_revision` sent was not the current one — something else committed first | Re-read the canonical state, replay the same intent (same `Idempotency-Key`) against it |
| `409` | `idempotency_conflict` | The `Idempotency-Key` was already used for a different mutation, or the same rating resource with a different value | Same recovery; the key belongs to a decision that is already recorded |
| `422` | `transition_refused` | The request was understood and the revision was current, and [ADR 0012](../adr/0012-browser-identity-feedback-and-online-freshness.md)'s transition table forbids the result — adding an already-watched title to the watchlist, say | Nothing was written and no retry can succeed. Show the sentence, re-read the state (the rule broken is a rule *about* state, so the client's picture of it was wrong), and correct the control |
| `422` | *(none)* | Request validation failed. `detail` is a **list** of field errors, not a sentence | Fix the request; this is the caller's own defect |

The two shapes on `422` are why the code exists rather than the status alone,
and both are declared on every mutation operation in `openapi.json`. Until
2026-08-29 a transition refusal was a third condition hiding under `409`, and
the web client told it apart by matching the sentence in `detail` with regular
expressions — so a copy edit on the server would silently have turned a product
rule into a "somebody else changed this" prompt (issue #74).

An error body deliberately does **not** carry a state snapshot. A client that
needs the current record after a refusal reads it from
`GET /users/{user_id}/movies/{movie_id}` (or `.../state`), which is the one
representation carrying a revision the next write can assert — an abbreviated
copy inside an error body would be a second source of truth for the same row.

## Unauthenticated

| Path | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness. Deliberately does not touch the database — `pool_pre_ping` covers connectivity, and a probe that fails on a transient database blip takes a healthy service out of rotation |
| `/readyz` | GET | Readiness for a deploy gate. Reports `database`, `jwks`, `model_server` and `feature_server`; **only the first two decide the status code**, because they are what a single authenticated request cannot be served without. `503` when not ready |

`/readyz` is the second unauthenticated path and was added deliberately, with
the widening of non-negotiable #10's wording recorded in ADR 0013 rather than
done quietly: it exposes no tenant data and no user data, and its port is not
published. A `/metrics` endpoint is deliberately **not** added.

## Identity

| Path | Method | Purpose |
|---|---|---|
| `/whoami` | GET | Echo of the resolved identity: tenant, subject, realm and role set. The cheapest proof that issuer-derived tenancy works |
| `/personas` | GET | The stable synthetic identities in the caller's tenant — the four demo personas |

## Recommendations and evidence

| Path | Method | Purpose |
|---|---|---|
| `/users/{user_id}/recommendations` | GET | The two-stage path: item-item retrieval, batched Feast/Redis features, LightGBM ranking — or an explicit popularity fallback. `limit` is optional |
| `/users/{user_id}/audits` | GET | Newest prediction audits visible inside the request tenant: exact ranked items and scores, online feature values, artifact versions, fallback reason, per-stage latencies, and the input-state digests |
| `/users/{user_id}/features` | GET | The tenant-keyed Redis-backed Feast read that online ranking uses — the same values, so an operator can see what the ranker saw |

## Catalog and detail

| Path | Method | Purpose |
|---|---|---|
| `/users/{user_id}/catalog` | GET | Browse: deterministic search (`q`), `genre`, `year_from`/`year_to`, three sorts, `limit`, and an opaque `cursor` bound to the normalized query fingerprint. Reused against a different query it is a `400`, and **no total is invented** |
| `/users/{user_id}/movies/{movie_id}` | GET | Movie detail: persisted metadata plus the persona's durable state. The only operation that returns the `details` payload — tagline, runtime, backdrop, TMDB score, directors, cast, trailer — so a list endpoint never fans out |

## Library

| Path | Method | Purpose |
|---|---|---|
| `/users/{user_id}/library` | GET | One bounded keyset page plus counts for all tabs. `tab` is `rated`, `watchlist` or `history`; `sort` is `recent`, `title`, `rating`, `release` or `tmdb`; plus `q`, `genre`, `year_from`, `year_to`, `limit`, `cursor`. The response's `page.matched` is an **exact** count for the tab and filters |
| `/users/{user_id}/taste-profile` | GET | A `live-ratings-v1` summary of current ratings. Labelled that way on purpose: it is a live read, not a model explanation |
| `/users/{user_id}/history` | GET | Recent interactions for the watch-history panel |

The `history` tab is labelled **Seen** in the product. The API value, the URL
value and every mutation path are unchanged — only the visible label moved.

## Movie state

The four states are independent resources rather than one blob, so a rating edit
cannot silently clear a watched date. Each takes `expected_revision` and an
`Idempotency-Key` header, and returns the canonical committed state.

| Path | Methods | Purpose |
|---|---|---|
| `/users/{user_id}/movies/{movie_id}/state` | GET | The canonical live state for one movie |
| `.../watched` | PUT, DELETE | Mark watched — one positive interaction. `DELETE` is the destructive history removal, kept separate from removing a rating |
| `.../rating` | PUT, DELETE | The star value, stored for display. `DELETE` preserves watched state |
| `.../watchlist` | PUT, DELETE | Organizational only. Seeds no candidates |
| `.../dismissal` | PUT, DELETE | A reversible exclusion. Never written back as a rating and **never a training negative** (ADR 0012) |

## Preferences

| Path | Method | Purpose |
|---|---|---|
| `/users/{user_id}/preferences` | GET | One persona's presentation preferences, or the defaults it has not left. A persona that has never written one sits at revision 0 with a null `updated_at`, because a default is not an edit |
| `/users/{user_id}/preferences` | PUT | Replace the **whole** object, asserting `expected_revision`. A full-object write is its own idempotency, so there is no key header |

Presentation only. Nothing here reaches candidate retrieval, the ranker or the
exclusion set, and a request's audit is unchanged by it.

## Compatibility

| Path | Method | Purpose |
|---|---|---|
| `/users/{user_id}/ratings/{movie_id}` | PUT | Create or replace one rating (the pre-`user_movie_state` shape) |
| `/users/{user_id}/ratings` | DELETE | Bulk rating clear. Watched history is preserved |

## `serving_policy`

Every recommendation response carries a `serving_policy` object. It exists so a
client never has to infer which path served a request — it is told, in fields it
can assert on.

| Field | Meaning |
|---|---|
| `name` | The policy that ran, e.g. `item-item-cosine+lightgbm` or `popularity` |
| `learned` | Whether both stages actually ran. A seedless retrieval reports `false` |
| `positive_signal_count` | Unique watched, non-dismissed titles |
| `threshold` | The cold-start threshold, `5` |
| `reason` | Free text after a stable prefix — group on the prefix |
| `score_scale` | What `items[].score` is: `lightgbm-rank-score` or `tenant-interaction-count`. An ordering, never a probability, and never to be shown as a match percentage |
| `filter_policy` | The exclusion rule applied, e.g. `watched-and-dismissed-excluded-v1` |
| `excluded_count` | How many titles the exclusion set removed |

The reason vocabulary — which prefix means what — is tabulated in
[`README.md`](README.md#serving_policy-reason-vocabulary).

### A worked response

`GET /users/900000101/recommendations?limit=2`, trimmed to the fields that
matter. Field names are the schema's; values are illustrative.

```json
{
  "tenant_id": "demo",
  "user_id": 900000101,
  "model_version": "demo-itemitem-v1/demo-lgbm-v1",
  "policy": "item-item-cosine+lightgbm",
  "serving_policy": {
    "name": "item-item-cosine+lightgbm",
    "learned": true,
    "positive_signal_count": 8,
    "threshold": 5,
    "reason": "learned-two-stage: item-item-cosine retrieval over 6 positive seeds, ranked by demo-lgbm-v1",
    "score_scale": "lightgbm-rank-score",
    "filter_policy": "watched-and-dismissed-excluded-v1",
    "excluded_count": 8
  },
  "items": [
    {
      "movie_id": 1210,
      "title": "Star Wars: Episode VI - Return of the Jedi",
      "genres": ["Action", "Adventure", "Sci-Fi"],
      "tmdb_id": "1892",
      "score": 1.7423,
      "reason": "similar to movies you watched",
      "poster_url": "https://image.tmdb.org/t/p/w500/...",
      "overview": "...",
      "release_year": 1983,
      "metadata_source": "tmdb-snapshot",
      "state": {
        "tenant_id": "demo",
        "user_id": 900000101,
        "movie_id": 1210,
        "watched_at": null,
        "rating": null,
        "rating_updated_at": null,
        "watchlisted_at": null,
        "dismissed_at": null,
        "revision": 0,
        "updated_at": "2026-08-28T18:04:11Z"
      }
    }
  ]
}
```

`model_version` is `<candidate version>/<ranker version>` on the learned path and
`popularity-v1` on the fallback. The four movie-state fields are **timestamps,
not booleans** — `watched_at`, `rating_updated_at`, `watchlisted_at` and
`dismissed_at` — so a client reads "is it watched" as "is `watched_at` non-null"
and gets the *when* for free.

The flat `policy` string is retained for existing clients and always equals
`serving_policy.name`. `metadata_source` is one of `reviewed-fixture`,
`tmdb-snapshot` or `movielens`, so a reader can tell enriched metadata from the
MovieLens floor rather than guessing from whether a poster rendered.
