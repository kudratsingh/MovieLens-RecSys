# Bundle 5C Browse and movie-detail evidence

These captures come from the isolated UI preview, where `/ui-preview/browse`
and `/ui-preview/movies/[movieId]` mount the same components the authenticated
`/browse` and `/movies/[movieId]` routes mount. Only the resource behind them
differs: the preview reads the recorded catalog endpoint in
`web/app/api/ui-preview/catalog/route.ts`, which answers the Bundle 3 query
contract — composable filters, three sort orders, a page cap, and an opaque
cursor bound to the query fingerprint it was issued under.

That endpoint is fail-closed. It returns `404` unless the isolated preview flag
is set, so a production build cannot reach recorded data through it.

Capture command:

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3106
# In a second terminal:
npm run evidence:bundle5c
```

Each capture uses a fresh browser context, because Browse deliberately keeps
its loaded window in `sessionStorage`; sharing one tab would let an earlier
capture's restored grid stand in for the state a later capture is meant to
show. Captures are viewport-sized rather than full page, and several scroll a
named region into view first — the display headline is tall enough that an
unscrolled 1440x1000 shot of an error state and of a healthy grid would be the
same picture of a headline.

## Matrix

| Capture | Width | What it evidences |
|---|---|---|
| `browse-header-desktop` | 1440 | Route entry: search, filter sheet trigger, sort. |
| `browse-mobile` / `browse-tablet` / `browse-desktop` | 390 / 768 / 1440 | Result line with no invented total, poster grid, reserved 2:3 cards, per-card watchlist. |
| `browse-filtered-desktop` | 1440 | Genre plus sort applied through the real query contract. |
| `browse-empty-mobile` | 390 | No matches, with a way back to the full catalog. |
| `browse-stale-cursor-desktop` | 1440 | A cursor that outlived its query: results restart from the top behind a plain notice, not an error. |
| `browse-upstream-error-desktop` | 1440 | Catalog read failed; the region reports its own failure and offers a retry. |
| `browse-auth-expired-mobile` | 390 | Expired session routed to reauthentication rather than a retry. |
| `detail-mobile` / `detail-tablet` / `detail-desktop` | 390 / 768 / 1440 | Poster, identity, synopsis, provenance line, canonical state controls. |
| `detail-controls-mobile` | 390 | Watchlist, watched, and dismissal within thumb reach. |
| `detail-partial-metadata-desktop` | 1440 | A record with a synopsis but no artwork: deterministic fallback, named gap. |
| `detail-unavailable-metadata-mobile` | 390 | A MovieLens-only record: no synopsis to show, and the page says so. |
| `detail-not-found-desktop` | 1440 | An unknown movie resolved through the shared `not-found` resource state. |

## What the metadata states mean

The reviewed snapshot is knowingly incomplete, so `complete`, `partial`, and
`unavailable` records all appear here on purpose. Every fallback is derived
from data already held — title initials for artwork, a source-aware line in
place of a missing synopsis — and no card triggers a live third-party request.
Browse visibility, poster completeness, and eligibility for a serving policy
remain three separate measures; nothing in these captures claims otherwise.
