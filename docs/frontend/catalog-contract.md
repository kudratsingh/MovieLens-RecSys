# Catalog and movie-detail contract

**Status:** Bundle 3 implemented

**Last updated:** 2026-08-21

## Product boundary

Browse is a poster-first catalog, not another recommendation rail. A title can
be visible without being eligible for every serving policy, and the UI never
describes catalog breadth as recommendation coverage.

The reviewed demo snapshot contains:

- **120 visible titles** in `synthetic/personas/catalog.json`;
- **24 poster-backed, overview-backed titles** with complete reviewed metadata;
- **96 partial-metadata titles** that deliberately exercise poster and synopsis
  fallbacks; and
- **120 titles with deterministic background interactions**, so the current
  fixture's popularity and item-item artifact inputs cover 100% of visible
  titles after artifacts are regenerated.

Those are separate assertions. Future fixture changes must continue to report
visible, poster-backed, and recommendation-eligible counts independently.

## Persisted metadata read model

`movie_catalog_metadata` is a shared, non-RLS read model keyed by MovieLens
movie ID. It stores normalized sort title, release year, poster URL, overview,
source, source status, visibility, and source-update time.

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

Detail returns the same authoritative item shape with overview and durable
movie state. A hidden or absent title returns `404`. Cast, trailers, providers,
and similar-title modules remain deferred because no local contract owns them.

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
