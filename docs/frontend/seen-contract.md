# The Seen experience — Library, History tab

**Status:** Shipped. Written as a contract ahead of the code and merged with it
in the same commit (`b62a5b7`, PR #84); it now describes behaviour on `main`.

**Last updated:** 2026-08-29

## Purpose

The Library's History tab becomes the place a viewer goes to look back at what
they have watched, rather than a chronological receipt they scroll. Two parts,
built against the same page of rows:

1. a **spotlight** above the list that presents one seen title at a time in the
   same visual family as Discover's featured section, with the shared
   `RatingStars` for a quick re-rate and the existing confirmed
   `Remove from history`; and
2. **search, filters and rankings** on the list — `q`, one genre, a release-year
   range, and five sorts — so a library of a few hundred titles can be narrowed
   to the part somebody is actually looking for.

It is an evolution of a tab, not a new route. `/library?tab=history` is still
the address, `history` is still the API and URL value, and every mutation still
goes through the one write path in `web/lib/movie-state/`. The only thing that
changes about the tab's identity is its visible label, which becomes **Seen**.

The semantics do not move. Watched is final and `Remove from history` is the
confirmed way out; a star value is display feedback online and never a training
signal; and seen titles are already kept out of Discover's featured slot and
ranked rail by the serving exclusion set (`watched-and-dismissed-excluded-v1`).
Seen *states* that last fact in its collection-meaning line — it does not
re-implement it (ADR 0012, `web/lib/quick-picks/contract.ts`).

## The API

### Request

```http
GET /users/{user_id}/library
  ?tab=rated|watchlist|history
  &sort=recent|title|rating|release|tmdb
  &q=&genre=&year_from=&year_to=
  &limit=24&cursor=
```

| Parameter | Type | Default | Validation | On violation |
|---|---|---|---|---|
| `tab` | enum `rated\|watchlist\|history` | `rated` | FastAPI `Literal` | `422` |
| `sort` | enum `recent\|title\|rating\|release\|tmdb` | `recent` | FastAPI `Literal`; `rating` is refused on `watchlist` | `422` for an unknown value; `400` for `sort=rating&tab=watchlist` |
| `q` | string, nullable | `null` | `max_length=120` | `422` |
| `genre` | string, nullable | `null` | `max_length=40` | `422` |
| `year_from` | int, nullable | `null` | `ge=1878, le=2100` | `422` |
| `year_to` | int, nullable | `null` | `ge=1878, le=2100`; `year_from <= year_to` | `422` |
| `limit` | int | `24` | `ge=1, le=50` | `422` |
| `cursor` | string, nullable | `null` | `max_length=1024`; bound to the query fingerprint | `422` when longer; `400` when it does not belong to this query |

`genre`, `year_from` and `year_to` are new; the other five keep the bounds they
already have. The endpoint's default `limit` stays 24 even though the web client
asks for 12 (`LIBRARY_PAGE_SIZE`).

`sort=rating&tab=watchlist` stays a `400` rather than becoming a `422`: a
watchlisted title cannot carry a star value — a rating implies watched and
watched clears the watchlist — so the combination is refused for a product
reason, and the existing clients already read it as a bad request. `release`
and `tmdb` are accepted on **every** tab; they order movie facts, which every
tab's rows have, and there is no reason for the API to refuse an order it can
produce. Which of them a viewer is *offered* is a UI decision, and it lives in
`sortsForTab` (below) — Rated and Seen now offer both.

An unknown genre is a valid query that matches nothing: `200` with no rows, not
a `400`.

### The normalized query

Normalization happens once, before anything else touches the parameters:

| Field | Normalization |
|---|---|
| `tab`, `sort` | as given |
| `q` | collapse runs of whitespace to one space, strip, then `None` if empty |
| `genre` | strip, then `None` if empty. Case-sensitive: it is matched against the MovieLens genre vocabulary, which is fixed |
| `year_from`, `year_to` | as given, after the range check |

Title matching lowercases `q`; the response echoes the normalized value in its
original case, or `null`. This is a small change from today, where `q` is only
`.strip()`ed — `"  the   thing "` and `"the thing"` are now the same query, and
therefore the same fingerprint and the same cursor.

`q` is now LIKE-escaped with the catalog's `_escape_like` (`\`, `%`, `_`).
Today's Library read interpolates it raw, so `q=%` matches every row; after this
it matches the titles that contain a literal percent sign. The escape character
is declared in SQL (`ESCAPE '\'`), exactly as `src/serving/catalog.py` does.

### The fingerprint

Same construction as `src/serving/catalog.py::_query_fingerprint`, with `tab`
added — a cursor must not survive a tab change any more than a filter change:

```python
payload = json.dumps(
    {
        "tab": tab,
        "sort": sort,
        "q": q.lower() if q else None,
        "genre": genre,
        "year_from": year_from,
        "year_to": year_to,
    },
    sort_keys=True,
    separators=(",", ":"),
)
fingerprint = hashlib.sha256(payload.encode()).hexdigest()[:16]
```

`limit` and `cursor` are deliberately outside it. Paging deeper into the same
query, or asking for a different page size, is the same view.

### The row set

```sql
FROM user_movie_state AS s
JOIN movies AS m ON m."movieId" = s.movie_id
LEFT JOIN movie_catalog_metadata AS cm ON cm.movie_id = s.movie_id
WHERE s.user_id = :user_id
  AND <tab condition>
  AND (:query    IS NULL OR lower(m.title) LIKE :query ESCAPE '\')
  AND (:genre    IS NULL OR ('|' || m.genres || '|') LIKE :genre ESCAPE '\')
  AND (:year_from IS NULL OR cm.release_year >= :year_from)
  AND (:year_to   IS NULL OR cm.release_year <= :year_to)
```

The tab conditions are unchanged (`_TAB_CONDITION`). The genre predicate is the
catalog's: `m.genres` is the pipe-delimited MovieLens string, so the parameter
is `%|Drama|%` over `'|' || m.genres || '|'`.

**The year filter is the one filter that can hide a row the tab contains.**
`release_year` comes from the left-joined snapshot, so a title
`movie_catalog_metadata` has never covered has `NULL` there and drops out the
moment either bound is set. That is correct — an unknown year cannot satisfy
"between 1990 and 1999" — and it is bounded to the filtered case: with no year
filter such a row is still listed, with `release_year: null` and
`poster_url: null`, which is the rule
`docs/frontend/library-feedback-contract.md` already states.

### Sorts

Every sort ends in `s.movie_id ASC`, so every one of them is a total order.
Every key is a **non-null scalar**: nulls are mapped to a sentinel below the
lowest real value rather than spelled as `NULLS LAST`. That is the technique
`src/serving/catalog.py` already uses for `newest`, and it is what keeps the
keyset predicate to a two- or three-term comparison instead of a per-dialect
null dance.

| `sort` | Key vector, in ORDER BY order | Full ORDER BY | Where nulls land |
|---|---|---|---|
| `recent` | `k1` = `s.rating_updated_at` (rated) / `s.watchlisted_at` (watchlist) / `s.watched_at` (history) | `k1 DESC, s.movie_id ASC` | Not reachable — the tab condition guarantees the column |
| `title` | `k1` = `lower(m.title)` | `k1 ASC, s.movie_id ASC` | None |
| `rating` | `k1` = `COALESCE(s.rating, -1)`, `k2` = `COALESCE(s.watched_at, s.rating_updated_at)` | `k1 DESC, k2 DESC, s.movie_id ASC` | Unrated rows carry `-1` and land last. `k2` is non-null on both tabs this sort is allowed on: `watched_at` by the History tab condition, `rating_updated_at` by the Rated one |
| `release` | `k1` = `COALESCE(cm.release_year, -1)` | `k1 DESC, s.movie_id ASC` | Unknown year is `-1` and lands last |
| `tmdb` | `k1` = `COALESCE(<tmdb score>, -1)` | `k1 DESC, s.movie_id ASC` | No score is `-1` and lands last |

Ratings are `>= 0.5`, release years `>= 1878`, and TMDB averages are in
`(0, 10]`, so `-1` is below every real value in all three cases.

`<tmdb score>` is one module-level expression, chosen by dialect the same way
`FOR UPDATE` and the advisory lock already are. It yields `NULL` for: no
snapshot row, `details IS NULL`, no `tmdb_rating`, a non-numeric payload, and a
vote count of zero.

PostgreSQL:

```sql
CASE
  WHEN jsonb_typeof(cm.details -> 'tmdb_rating' -> 'average') = 'number'
   AND jsonb_typeof(cm.details -> 'tmdb_rating' -> 'count')   = 'number'
  THEN CASE
         WHEN (cm.details -> 'tmdb_rating' -> 'count')::text::numeric > 0
         THEN (cm.details -> 'tmdb_rating' -> 'average')::text::numeric
       END
END
```

The outer `CASE` is two `jsonb_typeof` calls, neither of which can raise, and
the cast only runs underneath them. A hand-written row that put a string where a
number belongs reads as "no score" rather than erroring the whole page — the
same tolerance `CatalogService._details` applies in Python, for the same reason.

SQLite (the unit-test store only):

```sql
CASE
  WHEN cm.details IS NOT NULL
   AND json_valid(cm.details)
   AND COALESCE(json_extract(cm.details, '$.tmdb_rating.count'), 0) > 0
  THEN json_extract(cm.details, '$.tmdb_rating.average')
END
```

`json_valid` is what keeps the deliberately broken payload in the catalog
fixtures from erroring a query rather than reading as absent.

The count guard is the product rule, not a null check: an average with no votes
behind it is not a score. It is spelled once, in SQL, so the row's
`tmdb_rating`, the `tmdb` ordering, and the spotlight's `tmdbScoreText` cannot
disagree about which titles have a score.

**The one deliberate change to an existing view.** `sort=rating` gains
`watched date DESC` as its second key, so equally-rated titles are ordered by
when they were watched rather than by movie ID. That is observable on the Rated
tab. It is worth it: two orders behind one sort name is exactly the divergence a
shared contract exists to prevent, and the alternative was a `rating` sort that
means one thing on Rated and another on Seen. Total order and cursor safety are
unchanged either way.

### The cursor

Opaque, versioned, base64url without padding, over
`json.dumps(..., sort_keys=True, separators=(",", ":"))`:

```jsonc
{
  "v": 2,
  "f": "9f2c1ab30d4e5f60",   // the 16-hex fingerprint above
  "k": [8.5, "2026-03-11T20:14:00+00:00"],  // the last row's key vector
  "id": 1219                  // the last row's movie_id
}
```

`k` carries the sort's key vector for the **last row of the page**, in ORDER BY
order, each element a JSON string or number and never `null` (the sentinel rule
above guarantees it). Datetime keys are ISO-8601 UTC strings, produced by the
existing `_cursor_value`.

Decoding raises `InvalidLibraryCursorError` — surfaced as `400`, detail
`"library cursor is invalid for this query"` — when any of these is true:

- it is not base64url, or not JSON, or not an object;
- `v != 2`. **v1 cursors are not accepted**; a link someone kept from before
  this change is rejected exactly like any other stale cursor, and the frontend
  restarts from the top behind a plain notice;
- `f` is not the fingerprint of *this* request's normalized query — the reuse
  rejection, and the whole point of the fingerprint;
- `id` is not an `int >= 1`;
- `k` is not a list of the arity this sort expects: 2 for `rating`, 1 for the
  other four;
- any element of `k` is a bool, or is neither `str` nor `int`/`float`;
- an element's type does not match the sort's key type — `str` for `recent`,
  `title` and `rating[1]`; a number for `release`, `tmdb` and `rating[0]`.

The keyset predicates, appended to the `WHERE` above:

```sql
-- recent, release, tmdb  (one descending key)
AND (k1 < :c1 OR (k1 = :c1 AND s.movie_id > :cid))

-- title  (one ascending key)
AND (k1 > :c1 OR (k1 = :c1 AND s.movie_id > :cid))

-- rating  (two descending keys)
AND (k1 < :c1
     OR (k1 = :c1 AND k2 < :c2)
     OR (k1 = :c1 AND k2 = :c2 AND s.movie_id > :cid))
```

The read asks for `limit + 1` rows. `has_more` is whether the probe row came
back; `next_cursor` is issued only when it did, from the last row of the
trimmed page.

### The response

Three additions and three echoes. Everything else is byte-identical to today.

```jsonc
{
  "tenant_id": "demo",
  "user_id": 900000101,
  "tab": "history",
  "sort": "recent",
  "query": "blade",        // normalized, original case, or null
  "genre": "Sci-Fi",       // new: echoed, or null
  "year_from": 1990,       // new: echoed, or null
  "year_to": 1999,         // new: echoed, or null
  "counts": { "rated": 61, "watchlist": 4, "history": 88 },
  "page": {
    "next_cursor": "eyJ2IjoyLC...",
    "has_more": true,
    "matched": 42          // new
  },
  "items": [
    {
      "movie_id": 1219,
      "title": "Psycho (1960)",
      "genres": ["Crime", "Horror", "Thriller"],
      "release_year": 1960,
      "poster_url": "https://image.tmdb.org/t/p/w500/...",
      "tmdb_rating": 8.4,  // new, nullable
      "state": { "...": "unchanged" }
    }
  ]
}
```

**`page.matched`** — the exact number of rows matching the tab condition *and*
the filters, ignoring the cursor and the limit. One extra bounded query over the
same `FROM`/`WHERE` with the cursor predicate dropped:

```sql
SELECT COUNT(*) FROM user_movie_state AS s
JOIN movies AS m ON m."movieId" = s.movie_id
LEFT JOIN movie_catalog_metadata AS cm ON cm.movie_id = s.movie_id
WHERE <the same conditions>
```

It runs on **every** page, not only the first, so the spotlight's "4 of 42"
stays true after an append. It is exact and always present; it is never an
estimate and never a cap.

The catalog's "no invented total" rule is not being broken here, it is being
respected. That rule is about the shared catalog: counting it is a scan of a
tenant-wide table and the number is a claim about breadth. This counts one
persona's own rows, bounded by what that viewer has done, and it is the number
the spotlight would otherwise have to make up.

`counts` is **not** filtered. It stays the three whole-tab totals the tabs
print. `matched` and `counts.history` therefore differ the moment a filter is
on, and both are on screen at once, which is why the copy below names one
"of 42" inside the spotlight and leaves the tab badge alone.

**`items[].tmdb_rating`** — `number | null`, the TMDB crowd average for that
title, produced by the exact expression above so the row and the `tmdb` sort
agree by construction. The **vote count is deliberately not on the row**: a
compact list mark has no room for `· 4,812 ratings`, and dropping the count
while keeping the average is precisely what `tmdbScoreText` refuses to do. The
spotlight, which reads the full detail resource, keeps the count-with-average
rule where there is space for it.

### What does not change

- Tab values, in the API and in the URL: `rated | watchlist | history`. Only the
  visible label of the third becomes `Seen`.
- The Rated and Watchlist tabs: conditions, controls, copy, default sort, empty
  states. The single exception is the `rating` sort's new tie-break, called out
  above. (Rated's sort *options* changed later — see "Rated ranks by the
  movie's own facts too".)
- `counts` semantics, and the fact that all three are returned on every read.
- Every mutation path. ADR 0012's transition table, `expected_revision`,
  idempotency keys, the conflict re-read and single replay, the
  commit-before-acknowledge rule, and the append-only feedback events are all
  untouched. This change adds no write.
- The exclusion set. Seen filters nothing on behalf of Discover; it describes
  what serving already does.
- `MovieDetails` stays off list responses. The library row grows one nullable
  number, not the detail block (`tests/unit/test_openapi_contract.py`).
- No migration. `matched` is a count and `tmdb_rating` reads a column migration
  0013 already added.

### Serving behavior

Unchanged in shape: the middleware owns the RLS transaction, the reads are
synchronous SQLAlchemy in Starlette's thread pool, and operations on the request
connection stay serialized. The request goes from two queries (page, counts) to
three (page, counts, matched), all bounded by one persona's own rows and all
served by the existing tenant-leading index on `user_movie_state` plus
primary-key joins.

One fixture note the backend implementer will hit immediately:
`tests/unit/test_serving_recommendations.py::_connection` — the SQLite store the
feedback tests share — creates `movie_catalog_metadata` **without** a `details`
column. It needs `details TEXT`, matching the shape
`tests/unit/test_serving_catalog.py` already declares, or every Library test
fails on an unknown column.

## The UI

### Route and label

`/library?tab=history`. `libraryTabLabel("history")` returns `Seen`, and every
string derived from it follows: the tab, the eyebrow (`Seen collection`), the
filter placeholder (`Filter seen by title`), the list's `aria-label`
(`Seen movies`). The URL value, the API value, and the `LibraryTab` type stay
`history` — one rename in a label map, not a migration.

### URL state

`web/lib/library/url-state.ts` keeps ownership of tab, sort, filters and page
position. New fields on `LibraryUrlState`: `genre: string | null`,
`yearFrom: number | null`, `yearTo: number | null`.

| Param | Values | Default | Written to the URL |
|---|---|---|---|
| `userId` | persona id | `900000101` | always |
| `tab` | `rated \| watchlist \| history` | `rated` | when non-default |
| `sort` | whatever `sortsForTab` allows | `recent` | when non-default |
| `q` | free text, ≤ 120 chars | `""` | when non-empty |
| `genre` | trimmed, ≤ 40 chars | `null` | when set |
| `year_from`, `year_to` | `/^\d{1,4}$/`, clamped to `[1878, 2100]` | `null` | when set |
| `cursor` | opaque, ≤ 1024 chars | `null` | when set |

Rules, all of them extensions of rules that already exist here:

- `sortsForTab`: `rated` and `history` → `recent, title, rating, release,
  tmdb`; `watchlist` → `recent, title`. Watchlist is the one real exclusion,
  and it has a product reason: a saved title carries no star value, and the
  endpoint refuses `sort=rating&tab=watchlist` with a `400`. `normalizeSort`
  still falls back to `recent` for a sort the tab does not offer, so a
  hand-edited `?tab=watchlist&sort=tmdb` lands on `Most recent` rather than on
  an error, and a viewer who ranked Seen by crowd score keeps that ranking when
  they switch to Rated.
  *(Rated gained `release` and `tmdb` after this document first landed — see
  "Rated ranks by the movie's own facts too" below.)*
- An inverted year range (`year_from > year_to`) drops **both** bounds, exactly
  as `parseBrowseQuery` does, because the endpoint answers it with a `422` and
  guessing which bound the viewer meant is worse than showing the unfiltered
  collection.
- Filters are **parsed on every tab** so a deep link is honoured, and the filter
  **controls render on the Seen tab only** in this change. One place decides.
- Changing tab, sort, `q`, `genre`, `year_from` or `year_to` drops the cursor
  (`nextLibraryUrlState`), because the API binds the cursor to the fingerprint
  of exactly that set.
- `libraryViewKey` grows the three new fields, so a filter edit is a new view
  and not an "already loaded" one.
- Only non-defaults are written, so `/library?userId=900000101&tab=history` is
  the canonical resting URL of the Seen tab.

The BFF route (`web/app/api/users/[userId]/library/route.ts`) forwards a
parameter allow-list; it grows `genre`, `year_from` and `year_to`.
`LibraryQuery` in `web/lib/resources/server.ts` grows the same three plus the
two new sort values.

**One cross-boundary trap.** `isLibraryResponse` in
`web/lib/resources/validate.ts` currently asserts
`oneOf(["recent", "title", "rating"])(value.sort)`. An API that starts returning
`sort: "tmdb"` against an unwidened validator fails the region as
`invalid-payload` — a healthy backend rendering as a broken page. The list must
grow to the five values in the same change, `page.matched` must be accepted as a
number, and `tmdb_rating` must be accepted as `number | null` **or absent**, on
the same reasoning `movieMetaLine` already applies to `release_year`: the API
and the web app are separate images and one can be older than the other.

### The spotlight

Rendered inside the `history` tab panel only, between the collection controls
and the list. `id="library-spotlight"`, `aria-label="Seen spotlight"`.

**It walks the list.** The spotlight's queue *is* the loaded window of rows, in
the exact order the rows are in, under the same filters and the same sort. There
is no second fetch that drives it and no second ordering. A position is an index
into `items`.

**Its rich fields come from the detail resource, for the current title only.**
`GET /users/{id}/movies/{movieId}` through `readBffResource(MOVIE_DETAIL, …)` —
the same resource `lib/movie-state/client.ts` already reads — in the browser,
after hydration. One request in flight at a time: moving on aborts the previous
read with an `AbortSignal`, because a viewer pressing `Next` five times must not
leave five detail requests racing. The list stays light; nothing about a row
changed.

**It extends through the cursor.** When
`index >= items.length - SPOTLIGHT_EXTENSION_TRIGGER` (3, the same depth
`QUEUE_EXTENSION_TRIGGER` uses on Discover) and `page.has_more`, the route runs
the same `loadMore()` the list's button runs. One window, one cursor, appended
by `appendLibraryPage` as it already is.

#### The reducer

Pure, in `web/lib/library/spotlight.ts`, so the interesting rules are testable
without React — the reason `web/components/discover/queue.ts` is not a hook
either.

```ts
type SpotlightState = { index: number; movieIds: readonly number[] };

next(state): SpotlightState
previous(state): SpotlightState
syncToWindow(state, movieIds: readonly number[]): SpotlightState
removeCurrent(state, movieId: number): SpotlightState
```

- `previous` and `next` clamp and never wrap. At either end the corresponding
  control is `disabled` — a `Next` that silently returns to the first title is a
  press that did nothing.
- `syncToWindow` keeps the spotlight on the same **movie id** across a window
  change (an append, a re-read, a row leaving). If that id is gone it holds the
  same index, clamped into range. An empty window is `index: 0` and renders
  nothing.
- `removeCurrent` leaves the index where it is, so the title that took the
  removed one's place becomes current — and clamps to the last row when the
  removed title was the last one.

#### Controls, in order

1. `Previous`, the position readout, `Next`.
2. The shared large `RatingStars` (`legend="Your rating"`,
   `clearLabel="Clear rating"`), with the note movie detail already prints under
   the same control: *"The star value is display feedback today, not a graded
   training signal."*
3. `MovieStateControls` declared with `libraryControlSet("history", watched)` —
   the same call the row makes, so the spotlight and the row cannot offer
   different actions. That is `watched: confirm` (the confirmed
   `Remove from history`) plus `dismissal: undo`.

Every write goes through `web/lib/movie-state/` with the row's own
`state.revision` as `expected_revision` and a fresh idempotency key per intent.
The spotlight adds no transport.

#### Keyboard

`ArrowLeft` → previous, `ArrowRight` → next, both `preventDefault()`, handled on
the spotlight section. The handler **ignores the event** when its target is an
`input`, `select` or `textarea`, or sits inside `.rating-stars`: the star row
owns the arrow keys for its roving tab stop, and stealing them would break the
one control in the product that already documents that binding.

#### Focus and announcements

- Moving the spotlight does **not** move focus. The pressed button stays
  focused, so a repeated `Next` keeps working.
- The spotlight owns one `aria-live="polite"` region, and it speaks about
  **navigation only**: `Psycho, 1960. 4 of 42 in Seen.` Mutations keep
  announcing through the route's existing region. Two live regions in one panel
  is how one of them stops being read, so they are split by subject rather than
  duplicated.
- After a committed `Remove from history`, the existing `restoreFocus` walk runs
  with the spotlight as its second stop: the control → `#library-spotlight` →
  the active tab. The spotlight advances and the row disappears in the same
  commit.
- After a committed rating, `RatingStars` runs its own acknowledgement and
  collapses to `You rated 4/5 · Change rating` with focus on that button —
  unchanged behaviour. The row for the same movie updates through
  `replaceMovieState` from the committed state, so the list agrees without a
  refetch.

#### While the detail read is loading, or after it fails

- **The base layer renders immediately, from the row the list already has**:
  poster, title, year, genres, `Seen on <date>`, the rating control, the
  actions, the position readout. It never waits on the detail read.
- The rich layer — backdrop wash, runtime, TMDB score with its vote count, cast
  — is *added* when the read resolves `ready`. Nothing is reserved for it while
  it loads and nothing announces it. The degraded card is the base case, exactly
  the way `/movies/[movieId]` treats its enriched record.
- A failed, timed-out or `not-found` detail read is **silent**: no error region,
  no `Try again`, no announcement. It is progressive enhancement of a card that
  is already complete. The failure is still a `ResourceFailure` with its request
  ID for logs.
- **The list must never blank.** The detail read is not one of the list's
  regions; every state above leaves the rows exactly as they were.

#### Images

Every image in the spotlight declares explicit `width`/`height` or sits in an
aspect-reserved box — the backdrop as `1280×720` with `sizes="100vw"`, the same
declaration `movie-detail-view.tsx` uses; the poster through `PosterCard`. This
is what keeps `web/tests/perf`'s reserved-box claim green on `/library`.

### The list

Unchanged except for one addition: a compact `TMDB 8.1` mark beside the existing
meta line when `tmdb_rating` is a number. When it is `null` the row shows
**nothing** in its place — no "unscored" label. A missing synopsis is named
because the reader is looking for one; a missing crowd score beside a movie is
noise, which is the same split the enriched record already documents.

Load more, the cursor notice, optimistic reconciliation, rollback, the focus
walk, and the taste summary are all as they are today.

### States, in the 5A vocabulary

| Situation | Resource state | What the viewer sees |
|---|---|---|
| First page loading | `loading` | The existing `ResourceLoading` skeleton for "the seen collection". **No spotlight placeholder** — there is no window yet, and a skeleton hero is a promise about a title nobody has read |
| `200` with rows | `ready` | Spotlight, then rows |
| `200`, no rows, no filters | `empty` | The existing history `EmptyState`, with `Browse the catalog`. No spotlight |
| `200`, no rows, filters set | `empty` | `No matches in this collection` / `Nothing in Seen matches these filters.` with a `Clear filters` action. No spotlight |
| First-page read fails `bad-request` while a cursor is set | handled, not surfaced as an error | Drop the cursor, reload from the top, show the stale-cursor notice |
| Any other first-page failure | `forbidden` / `auth-expired` / `not-found` / `upstream-error` | The shared region block, named by its headline; `Try again` only when retryable. No spotlight |
| `Load more` fails | rows stay | The existing `LibraryProblem`: "The next page could not be loaded" |
| A write fails | rows stay, state rolls back | The existing `LibraryProblem`: "That change was not saved", plus the rollback announcement |
| The spotlight's detail read fails | — | Nothing. Base layer only |

### Copy

| Where | Exact string |
|---|---|
| Tab label | `Seen` |
| Collection eyebrow | `Seen collection` |
| Collection title | `Everything you have watched` |
| Collection meaning | `Watched titles are the positive interactions candidate lookup reads from, and serving already excludes them from Discover's featured slot and ranked rail. A star value is display feedback and never a training signal.` |
| Spotlight label | `Seen spotlight` |
| Position readout, visible | `4 of 42` — `{index + 1} of {page.matched}`. `matched` is `required` in the published schema, so the loaded window is a fallback only for the moment before the first page arrives, never for a response that omitted it |
| Position readout, announced | `Psycho, 1960. 4 of 42 in Seen.` |
| Previous / Next | `Previous` / `Next`, with `aria-label` `Previous seen title` / `Next seen title` |
| Seen-on line | `Seen on 12 Mar 2024`, or `Seen on an unknown date` — same UTC formatter `formatLibraryDate` already pins, so a screenshot and a runner agree |
| Rating legend / clear | `Your rating` / `Clear rating` |
| Rating note | `The star value is display feedback today, not a graded training signal.` |
| Remove from history | unchanged — the row's `watched: confirm` control and its confirmation |
| Sort options | `Most recent`, `Title`, `Highest rated`, `Newest release`, `Highest TMDB score` |
| Genre control | label `Genre`, first option `All genres` |
| Year controls | `From year` / `To year` |
| Filtered-empty | title `No matches in this collection`, message `Nothing in Seen matches these filters.`, action `Clear filters` |
| Stale-cursor notice | `That page link no longer matches this view, so the list starts from the beginning.` |
| Row TMDB mark | `TMDB 8.1` |

## Tests

### Backend

- **Sorting** (`tests/unit/test_serving_feedback.py` — the alternative of a
  separate `test_serving_library_seen.py` was not taken): each of the five sorts
  returns the
  documented order over a fixture with deliberate ties; unrated rows land last
  under `rating` and equal ratings break on watched date then movie ID; unknown
  release years land last under `release`; unscored titles land last under
  `tmdb`. `release` and `tmdb` are asserted on `tab=rated` as well, over that
  tab's own row set — the two sorts read the movie rather than the state row,
  so the tab decides only which rows they order, and a watched-but-unrated
  title has to be absent from both.
- **Fingerprint**: stable for the same query; changes when any of `tab`, `sort`,
  `q`, `genre`, `year_from`, `year_to` changes; unchanged by `limit` and
  `cursor`; `"  the   thing "` and `"the thing"` produce the same one.
- **Cursor rejection**: a cursor issued under another tab, sort, `q`, genre or
  year bound → `InvalidLibraryCursorError`; a v1 cursor → the same; wrong `k`
  arity, a bool in `k`, and a type that does not match the sort's key → the
  same. Paging with a valid cursor returns the next rows with no overlap and no
  gap across a tie boundary.
- **`matched`**: exact under filters; identical on page 1 and page 2 of the same
  query; unaffected by `limit`; different from `counts.history` when a filter is
  set and equal to it when none is.
- **`tmdb_rating`**: `null` for no snapshot row, `details IS NULL`, no
  `tmdb_rating`, `count = 0`, a non-numeric payload, and (SQLite) a payload that
  is not valid JSON; the value otherwise.
- **Filters**: the LIKE escape (`q="%"` matches only titles containing a percent
  sign); the genre predicate matching whole genre tokens and not substrings —
  `genre=Sci-Fi` matches a `Sci-Fi` title, `genre=Noir` matches nothing even
  though `Film-Noir` contains it; a year bound dropping rows with no snapshot
  row, and those same rows surviving with no year bound.
- **Errors**: `sort=rating&tab=watchlist` → `400`; `year_from > year_to` →
  `422`; `limit=51` → `422`; a 1025-character cursor → `422`.
- **Contract**: `tests/unit/test_openapi_contract.py` asserts `page.matched` and
  `items[].tmdb_rating` exist and that `MovieDetails` still does not appear on
  `LibraryResponse` or `CatalogResponse`. `make api-contract-check` and
  `make web-api-types-check` regenerate and gate.
- **Tenant isolation** (`tests/tenant_isolation/test_no_cross_tenant_leak.py`):
  the Library canary fires the new parameters as tenant A —
  `?tab=history&sort=tmdb&genre=Drama&year_from=1990&year_to=2001&q=<a fragment
  of tenant B's title>` — and asserts no tenant B title appears **and that
  `page.matched` counts only tenant A's rows**. That second assertion is the one
  that earns its place: `matched` is a `COUNT`, and a count that crossed the
  boundary would leak a number without leaking a row.
- The SQLite fixture in `tests/unit/test_serving_recommendations.py::_connection`
  gains `details TEXT`.

### Frontend

- **Vitest, `web/tests/unit/library-url-state.test.ts`**: the three new
  parameters parse, clamp and round-trip; only non-defaults are written; each of
  tab / sort / `q` / genre / year drops the cursor; an inverted range drops both
  bounds; `sortsForTab` is the five on both `history` and `rated` and two on
  `watchlist`, and a sort survives a Rated ↔ Seen switch; `libraryViewKey`
  changes with each new field.
- **Vitest, `web/tests/unit/library-spotlight.test.ts`**: the pure reducer —
  clamping at both ends, `syncToWindow` following the movie id through an
  append, `removeCurrent` advancing and clamping at the tail, an empty window.
- **Vitest, `web/tests/unit/resources-validate.test.ts`**: `isLibraryResponse`
  accepts `sort: "release"` and `"tmdb"`, accepts `page.matched`, and accepts a
  row whose `tmdb_rating` is `null` or absent.
- **RTL, `web/components/library/library-spotlight.test.tsx`**: renders fully
  from the row with no detail read; adds runtime, score and backdrop when the
  detail read resolves; stays silent when it fails, with the rows untouched;
  `Previous`/`Next` and `ArrowLeft`/`ArrowRight` move it and announce title plus
  position; arrow keys inside `RatingStars` do **not** move it; a committed
  `Remove from history` advances the spotlight and drops the row; a committed
  rating updates the row. Plus the existing jest-axe pass.
- **Fixture-mode Playwright, `web/e2e/library-slice.spec.ts`**: the Seen tab at
  390 / 768 / 1440 with axe — spotlight present, filter and sort controls,
  filtered-empty state, stale-cursor notice, and no page overflow at 320. The
  Rated tab exercises its two added orderings at the same widths, and asserts
  that the genre and year controls stayed on Seen.
- **One service-backed journey** in `web/tests/e2e/`, in the serialized
  `browser-auth-e2e` set (`workers: 1`), owned by **Action Fan (900000101)**:
  sign in through Keycloak as `web/tests/e2e/keycloak.ts` does, open
  `/library?tab=history`, read the spotlight's title and its current rating,
  change the rating from the spotlight, assert the committed chip and the
  matching row, then **restore the original value in the same spec** — clearing
  it if there was none. The Cold Start persona (900000104) is never written, and
  `persona-hygiene.spec.ts` keeps asserting it.
- **Perf**: `/library` keeps the reserved-box structural claim — every image in
  the spotlight declares `width`/`height` or sits in an aspect-reserved box.

## Notes from integration

Three things the two halves only settled once they were built against each
other and run on the seeded stack.

**`matched` sits on `CursorPageResponse`, and that is safe.** The name reads
shared, and widening a schema the catalog also used would have handed the
catalog exactly the total its own contract refuses to invent. It does not: the
catalog carries `CatalogPageInfo`, and `CursorPageResponse` has one consumer —
`LibraryResponse`. Worth stating because the next person to add a field there
gets no warning from the name.

**The Library types are the generated ones.** The five additions were widened by
hand in `web/lib/api.ts` while the endpoint was ahead of `docs/api/openapi.json`,
each optional so an older API would degrade rather than break. The published
schema now marks all five `required`, and ADR 0013 deploys every image at one
commit SHA, so the two halves cannot be different ages: the widening is gone and
the validators assert what the schema promises. A missing key is a broken API,
and failing the region beats rendering `3 of undefined`.

**A removed watched date cannot be restored.** `PUT /users/{id}/movies/{id}/watched`
carries no body and stamps `watched_at = now()`, so `Remove from history`
followed by re-marking watched returns the title with today's date, not the one
it had. The confirmation copy is already honest about deleting the interaction;
the consequence for *tests* is the sharper one, and it is why the QA walk
exercises removal on a title the persona has never seen rather than on a seeded
row whose 2023 date it could not put back.

## Rated ranks by the movie's own facts too

*Settled after this document first landed; it closes what was open question 1.*

Rated now offers `release` and `tmdb` alongside `recent`, `title` and `rating`.
The original restriction was never a product judgement — the API has accepted
both on every tab since the first version of this contract, and the rows the
Rated tab already renders carry `release_year` and `tmdb_rating` and print
them. Seen held them alone so that the Rated tab's finish-gate evidence stayed
valid while Seen was being built. That turned out to cost nothing to undo: a
closed `<select>` shows its selected option and nothing else, so no committed
capture changes, and `recent` is still the default the resting URL omits.

What "one edit to `sortsForTab`" left out is the check that the endpoint really
answers both orderings over the *Rated* row set, which is a different set from
Seen's — every rated title is watched, but not every watched title is rated.
`tests/unit/test_serving_feedback.py` covers it on `tab=rated` directly:
`release` and `tmdb` in the documented order with `COALESCE(..., -1)` putting
the unknowns last, a watched-but-unrated row absent from both, an exact
`matched`, and a one-row-at-a-time cursor walk that reproduces the whole order
across the sentinel's ties.

Three things are deliberately unchanged. Watchlist still offers two sorts, for
the product reason above. The genre and year **filter controls** still render
on Seen alone — the sort control is the only one this widens, and
`web/e2e/library-slice.spec.ts` asserts the filters did not follow it. And
because both tabs now answer the same five, `nextLibraryUrlState` carries the
sort across a Rated ↔ Seen switch instead of resetting it to `recent`.

## Open questions

1. **Whether the spotlight's position should survive a reload.** It does not:
   the URL owns tab, sort, filters and cursor, and a spotlight index in the URL
   would be a fourth thing to keep in step with a window that moves under a
   write. It resets to the first loaded title.
2. **Whether `matched` needs a ceiling.** It does not for a persona-sized
   library. If `/me` ownership ever lands and a real user's library is
   unbounded, `matched` is the field that needs a cap and a `999+` rendering,
   and the spotlight readout is the only thing reading it.
