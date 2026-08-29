# Catalog and movie-detail contract

**Status:** Live contract. Shipped in Bundle 3 (PR #50) and still what Browse and
movie detail are held to; the detail endpoint additionally returns the `details`
payload added in PR #79.

**Last updated:** 2026-08-29

## Product boundary

Browse is a poster-first catalog, not another recommendation rail. A title can
be visible without being eligible for every serving policy, and the UI never
describes catalog breadth as recommendation coverage.

The reviewed demo snapshot contains:

- **120 visible titles** in `synthetic/personas/catalog.json`;
- **120 poster-backed titles**, filled offline from TMDB by
  `synthetic/personas/enrich_posters.py`;
- **120 overview-backed titles**, so every seeded row is
  `source_status = 'complete'`. Twenty-four of those synopses are hand-written
  and reviewed; the other 96 came from the same offline TMDB pass and are only
  ever *filled*, never overwritten — an automated run does not get to replace
  editorial copy; and
- **120 titles with deterministic background interactions**, so the current
  fixture's popularity and item-item artifact inputs cover 100% of visible
  titles after artifacts are regenerated; and
- **120 titles with a `details` object** — tagline, runtime, release date,
  backdrop, TMDB score, directors, up to six billed cast members and one
  YouTube trailer — filled offline by `synthetic/personas/enrich_details.py`.
  On the current snapshot every one of the 120 resolved a trailer, a backdrop,
  a score and a six-deep cast; 119 have a tagline.

Those are separate assertions. Future fixture changes must continue to report
visible, poster-backed, and recommendation-eligible counts independently.

Poster coverage used to be 24, and the 96 gaps were described as deliberate
fallback coverage. They were not: because this table is the only poster source
on the request path, a gap here is a permanent placeholder in the product
rather than a state a viewer passes through. Overview coverage was the same
argument one layer down — a `partial` eyebrow on almost every Browse card is
not a fallback being exercised, it is a snapshot that was never finished. The
poster-absent and metadata-unavailable states keep their own coverage in the
fixture-mode preview (`/ui-preview/movies/109` and `/ui-preview/movies/130`),
where they belong.

## Keeping the fixture from rotting

A reviewed snapshot is only reviewed for as long as somebody checks it. Three
committed poster paths were 404ing upstream while every test in the repository
passed, because nothing had ever asked the image host a question. Three checks
now cover the three distinct ways this fails:

| Check | Where it runs | What it proves |
|---|---|---|
| URL shape (`poster_url_shape_error`) | `tests/unit/test_enrich_posters.py`, so CI | Every entry carries a URL on the pinned `image.tmdb.org/t/p/w500/` host and size — the shape `web/next.config.ts` will actually load |
| Detail shape (`image_url_shape_error`) | `tests/unit/test_enrich_details.py`, so CI | Every `details` object has the contract's keys in the contract's order, a backdrop on `…/w1280/`, profile images on `…/w185/`, at most six cast members and a plain YouTube trailer key |
| Liveness (`make catalog-verify`) | By hand, and on whatever nightly cadence the demo earns | Every stored URL still HEADs 200 |
| Served coverage (`synthetic/smoke/demo.py`) | `make demo-smoke` | The database is not behind the fixture — the state that put 24 posters on a 120-poster snapshot for a day. Detail is one probe rather than a count, because `details` is not on the list endpoint: a stack seeded from a pre-enrichment image serves a full poster grid and an empty detail page |

Liveness deliberately does **not** gate a pull request: it asks a third party
whether its CDN is up, and that must never decide whether a change can merge.
Enrichment stays offline, human-run, reviewed as a diff, keyed by `movie_id`
and idempotent — `--verify` needs no credentials, refilling needs a
`TMDB_READ_ACCESS_TOKEN` that lives only in the operator's environment. A poster
URL is HEADed *before* it is written, so a run cannot introduce the very rot the
verifier looks for.

## Persisted metadata read model

`movie_catalog_metadata` is a shared, non-RLS read model keyed by MovieLens
movie ID. It stores normalized sort title, release year, poster URL, overview,
source, source status, visibility, and source-update time.

`details` (migration 0013) is a nullable JSONB column on that same row: one
fixture-owned object per title, written and replaced as a unit by the offline
pass and never queried by its parts. It is one column rather than a cast/crew
schema because nothing on this path joins to it, filters on it or aggregates
it — the detail page reads the object and renders it.

The table is intentionally shared: movie facts do not vary by tenant. The full
watched/rating/watchlist/dismissal projection remains tenant-owned and is
overlaid through the request's RLS-scoped connection. The application role has
read-only access to shared metadata.

Browse, detail, and recommendation-card hydration read this table only. They do
not make live TMDB requests. TMDB can remain an offline enrichment source, but
an upstream timeout cannot multiply into one request per visible card or delay
the recommendation critical path.

## Catalog resource

```http
GET /users/{user_id}/catalog
  ?q=&genre=&year_from=&year_to=&sort=title|newest|popular
  &limit=24&cursor=
```

- Default page size is 24 and the hard maximum is 48.
- Search, genre, and year filters compose.
- `title` orders by normalized title then movie ID.
- `newest` orders by release year descending then movie ID.
- `popular` orders by the active tenant's interaction count descending then
  movie ID.
- The opaque versioned cursor contains the last sort value and movie ID and is
  bound to the normalized search/filter/sort fingerprint. Reusing it with
  another query fails with `400` instead of silently skipping rows.
- Responses contain `page.next_cursor` and `page.has_more`; they do not promise
  an expensive total count.

Each item contains local metadata, `metadata_source`, `source_status`, tenant
interaction count, and the selected persona's complete canonical movie state
with its current revision.

## Detail resource

```http
GET /users/{user_id}/movies/{movie_id}
```

Detail returns the catalog item shape plus `details`, as `MovieDetailItem`. A
hidden or absent title returns `404`. Watch providers and similar-title modules
remain deferred because no local contract owns them.

### `details`

```jsonc
{
  "tagline": "string | null",
  "runtime_minutes": "integer | null",
  "release_date": "YYYY-MM-DD | null",
  "backdrop_url": "https://image.tmdb.org/t/p/w1280/… | null",
  "tmdb_rating": { "average": 8.0, "count": 20412 },   // or null
  "directors": ["string"],                              // possibly empty
  "cast": [                                             // at most 6, billing order
    { "name": "string", "character": "string | null", "profile_url": "…/w185/… | null" }
  ],
  "trailer": { "provider": "youtube", "key": "string", "name": "string" },  // or null
  "fetched_at": "ISO-8601"
}
```

**Nullability is the compatibility story.** `details` is required on the
response and nullable in it: `null` means "this row has no payload", never
"this response did not look". A title the offline pass has not reached, a
database seeded before migration 0013, and a payload that fails validation on
read all produce the same `null`, and the page renders the layout it had before
the column existed. Every field inside the object is nullable on the same
terms — TMDB reports a runtime of `0` and a score of `0.0` for titles it knows
nothing about, and both are stored as `null` rather than rendered as a fact.

**The list endpoint does not carry it.** `MovieDetailItem` is a separate schema
from `CatalogItem` precisely so it cannot leak into `GET /users/{id}/catalog`:
a 48-row Browse page carrying 48 backdrops and 48 cast lists to render a poster
grid is a page-size regression, and the split is asserted in
`tests/unit/test_openapi_contract.py` rather than left to review.

**Trailer keys are validated where they are written.** The client interpolates
`trailer.key` into a `youtube-nocookie.com` embed URL, so the enrichment
accepts only a plain YouTube id (`[A-Za-z0-9_-]{5,64}`) and only
`provider: "youtube"`; anything else is dropped at write time and refused again
by the response model. Nothing third-party is fetched until a viewer asks for
the trailer — the detail response carries a key, not an embed.

Refreshing a running database's read model is a re-seed, exactly as it is for
posters: rebuild the API image so the fixture inside it is current, then run
the seeder (`ON CONFLICT DO UPDATE` covers `details`, which is fixture-owned).
A stale image seeds yesterday's snapshot and the demo agrees with itself.

## Serving behavior

The request middleware still owns the RLS transaction. Synchronous SQLAlchemy
catalog/detail reads run in Starlette's thread pool so PostgreSQL I/O does not
block the async event loop. Operations on a request connection remain
serialized.

## Frontend behavior

`/browse` provides URL-preserved search, genre filters, sort state, bounded load
more, keyboard-reachable poster links, loading/empty/error states, and
session-local scroll restoration. `/movies/[movieId]` provides source-aware
metadata, resilient poster/synopsis fallbacks, dynamic record metadata, and an
honestly labelled rating action. Both server reads use the Auth.js-held access
token through the BFF; rating writes additionally enforce Origin and double-
submit CSRF checks.

Poster network failures preserve the fixed 2:3 card footprint and replace the
image with deterministic title artwork. Reduced-motion preferences collapse
nonessential transitions.
