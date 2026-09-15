# TMDB catalog metadata

**Status:** ingestion built and verified end to end on 2026-09-05; **the snapshot has
not been pulled** — see [Running it](#running-it). Owner decision O-2, 2026-09-05.
**Depends on:** [ADR 0017](../adr/0017-content-based-cold-item-retrieval.md) (why),
[ADR 0009](../adr/0009-feature-store-feast.md) (where features come from),
[ADR 0001](../adr/0001-evaluation-protocol.md) (the temporal split the leakage rule is about).

## Why this exists

ADR 0017's first increment represented an item by its MovieLens genres and its release
year, and the measurement killed it: warm recall@500 collapsed 10× against item-item
while cold-item recall moved from 0.0000 to 0.0001. The ADR's own falsification condition
fired, and it named the consequence — *either increment 2 becomes required — TMDB
metadata, which also covers the 3,413 items with no genres at all — or the rung stops
here with a recorded negative result.*

The owner took the first branch on 2026-09-05 and widened it: pull everything useful for
a strong recommender, not only what increment 2 strictly needs, because the same snapshot
is the item side of a two-tower's side features, the vocabulary a text or generative model
would read, and the only description this system has of a film nobody has watched.

There is a second reason the ingestion has to be a *snapshot* rather than a live call.
ADR 0017's third risk states it: TMDB's data changes underneath a cached copy, so a model
trained against the live API is not reproducible and non-negotiable #5 quietly stops
holding. The snapshot is DVC-tracked exactly like the ratings frame, and the manifest
records the day it was taken.

## What is pulled

One request per distinct TMDB id, against `GET /movie/{id}` with six sub-resources folded
into the same response:

```
append_to_response=keywords,credits,release_dates,external_ids,alternative_titles,translations
```

That is the whole catalog in 62,282 requests rather than seven times that.

**`images` is deliberately excluded.** It returns every poster, backdrop and logo TMDB
holds in every language it holds them — commonly one to three hundred entries per film —
and the only two artwork paths this system has ever used, `poster_path` and
`backdrop_path`, are already in the base payload. Appending it would roughly double the
size of the snapshot in exchange for nothing a recommender or the product can consume.

**`alternative_titles` and `translations` are pulled but not normalised into tables.**
Nothing reads them yet, and a table nothing reads is a table that rots. They are in the
shards the day something needs them, which is the point of storing the payload verbatim.

### The shape on disk

```
data/raw/tmdb/<pull-date>/
├── manifest.json                 # committed to git via DVC's pointer, not by hand
├── movies-00000.jsonl.gz         # 2,000 records per shard
├── movies-00001.jsonl.gz
└── ...
```

One record per line:

```json
{"movie_ids":[1],"tmdb_id":862,"status":"ok","fetched_at":"2026-09-05T09:00:00Z","payload":{…}}
```

`payload` is TMDB's response body embedded **byte for byte**, not a re-serialization of a
parsed copy — so nothing this code believes about the shape of a TMDB response can quietly
edit the snapshot. (The one exception, a pretty-printed body, is compacted because it
would otherwise break the one-object-per-line contract; TMDB does not send one.)

`movie_ids` is a list because the MovieLens → TMDB mapping is **not injective**: 34 TMDB
ids in `links.csv` are claimed by two MovieLens movies each, 69 rows in total. Those are
duplicate catalog entries for the same film. One request answers for both, and the loader
fans the payload back out to each movie id — which is also why `tmdb_movies` is keyed on
`movie_id` and merely *indexes* `tmdb_id`.

The manifest records the pull date, the API version, the exact `append_to_response`, the
catalog denominators, and one entry per run with its request count, 404 count, failure
count, throttled responses, wall-clock, and the SHA-256 of every shard.

## The leakage rule

**Six columns on `tmdb_movies` are not point-in-time safe and must never become model
features:**

| Column | Why not |
|---|---|
| `vote_average` | The crowd score *today*, with no history behind it |
| `vote_count` | Votes accumulated up to the pull, including every vote cast after 2019 |
| `popularity` | TMDB's own trending score, recomputed daily |
| `budget` | Frequently revised after release; no revision history is exposed |
| `revenue` | Lifetime gross as of the pull — literally an outcome measured after the fact |
| `status` | "Released" / "In Production" as of the pull |

MovieLens 25M's interactions end in 2019. These values describe the film as TMDB saw it in
2026. There is no per-observation timestamp on any of them, so no point-in-time join can
reconstruct what they were at a 2019 prediction time — the failure is not "we forgot to
join carefully", it is that the information required to join carefully does not exist.
A ranker fed `vote_average` would score better offline than it can possibly score online,
and would do it silently. That is precisely the failure mode CLAUDE.md's leakage warning
names.

They are **stored anyway**: they are worth having for analysis, for sanity-checking the
snapshot against the catalog fixture, and for the product's crowd-score display, which is
a presentation concern and not a model input. What makes storing them safe is that the
prohibition is enforced in four places rather than remembered:

1. A `# not point-in-time safe: as-of-pull values` block comment in
   [`src/data/tmdb_schema.py`](../../src/data/tmdb_schema.py).
2. A `COMMENT ON COLUMN` on each of the six in migration
   [`0018_tmdb_catalog`](../../alembic/versions/0018_tmdb_catalog.py), so `\d+ tmdb_movies`
   says it to anyone who looks at the database instead of the code.
3. This document.
4. [`tests/unit/test_tmdb_leakage.py`](../../tests/unit/test_tmdb_leakage.py), which fails
   if any of the six reaches `src/feature_contract.py`.

That test is careful about one thing worth stating, because a blunter version got it
wrong first: `item_popularity_all_time`, `item_popularity_30d` and `item_popularity_7d`
are already in the contract and are **safe**. They are interaction counts computed from
the training frame at the prediction timestamp, so they have exactly the point-in-time
history TMDB's `popularity` lacks. Same word, opposite property. The test's rule is about
where a value came from, not what it is called.

### What *is* intended as features

Everything else in the snapshot is a static attribute of the film and does not move once
the film exists: genres, overview, keywords, cast, crew, runtime, release date, original
language, collection membership, production countries, spoken languages, certification.
Those are the intended item-side features, and they are what makes a film with zero
interactions describable at all.

One caveat worth writing down before somebody trips on it: *static* is not the same as
*known at the time*. A film's certification is stable, but the TMDB row for a film
released in 2018 was itself edited after 2019. For the attributes above the edits are
corrections rather than accumulations — a plot summary gets rewritten, it does not grow
with viewership — which is why they are treated as safe. If a future feature depends on a
field where that stops being true, it belongs in the table above, not this paragraph.

## The normalised tables

Migration [`0018_tmdb_catalog`](../../alembic/versions/0018_tmdb_catalog.py) creates twelve
tables. The shards remain the source of truth; these are a derived read model, and dropping
them and re-running `make tmdb-load` costs nothing but time.

| Table | Grain |
|---|---|
| `tmdb_movies` | one row per MovieLens movie; PK `movie_id`, indexed `tmdb_id` |
| `tmdb_movie_genres` | (movie, TMDB genre) |
| `tmdb_keywords` / `tmdb_movie_keywords` | the keyword vocabulary and its links |
| `tmdb_people` | one row per person, shared across films |
| `tmdb_movie_cast` | top 15 billed roles, with `character` and `cast_order` |
| `tmdb_movie_crew` | the named roles (director, writer, producer, composer, cinematographer, editor, production and costume design), `--all-crew` to keep everything |
| `tmdb_production_companies` + join | (movie, company) |
| `tmdb_production_countries` | (movie, ISO 3166-1) |
| `tmdb_spoken_languages` | (movie, ISO 639-1) |
| `tmdb_release_dates` | (movie, country, release type, index) with certification |

**No row-level security on any of them, deliberately.** ADR 0008 forces RLS on
tenant-scoped tables because they hold one tenant's ratings, product state and audit rows,
and cross-tenant leakage is this system's highest-severity bug class. None of that applies
to a film's runtime or its cast list: this is catalog data, public in origin, identical for
every tenant, and carrying no `tenant_id` to key a policy on. `movie_catalog_metadata`
(migration 0011) made the same call for the same reason. Grants follow from it — `app_user`
reads, `admin_user` writes — and a unit test asserts the absence of RLS so a reader can see
the omission was a decision rather than a lapse.

Two key choices worth their sentence:

- **`tmdb_movies` is keyed on `movie_id`, not `tmdb_id`**, because of the 34 duplicated ids
  above. Keying it this way also means every join to `ratings`, `movies` and
  `movie_catalog_metadata` is a plain equality on the id those tables already use.
- **Cast and crew are keyed on `(movie_id, credit_id)`.** `credit_id` has to be in the key
  because the same actor can hold two billed roles in one film; `movie_id` has to be in it
  because a duplicated TMDB id puts the same credit on two MovieLens movies.

Re-loading is idempotent: a movie's rows are deleted and rewritten as a unit, so a re-load
over a *newer* snapshot replaces what the old one said rather than accumulating both — a
film that lost a keyword between two pulls loses the row. The three shared dimension tables
(people, keywords, companies) are upserted instead, because a delete keyed on one movie
would take another movie's row with it.

## Rate-limit safety

The whole design goal is that the key is never blocked. TMDB's published guidance sits
around 40–50 requests a second; this runs at **20** and never raises it. A snapshot that
takes 52 minutes instead of 21 is a trade worth making, and the whole run happens once.

- A **token bucket** paces every request, so a stall does not turn into a burst afterwards.
- **429**: `Retry-After` is honoured when TMDB sends one, otherwise exponential backoff
  from 1s, capped at 60s, jittered on both paths so a batch that hit the same limit does
  not come back in lockstep.
- **Sustained 429s halve the rate automatically** — five throttled responses at the current
  rate and the bucket drops to half, with a floor of 2.5/s. The owner's instruction was to
  halve and continue; automating it means it does not depend on somebody watching the log.
- **5xx and transport errors** are retried on the same backoff, four attempts per id.
- **401/403 stops the run immediately** and is the one failure never retried — a rejected
  key retried in a loop is how a key gets blocked outright.
- **Ten consecutive failures trip a circuit breaker**: the in-flight shard is flushed and
  renamed, the manifest records the stop reason, and the run exits non-zero.
- Connection reuse (one pool, 8 keepalive connections), 20s timeouts, progress every 1,000.

**Resumability and idempotence.** A record is written for every id that resolved *and*
every id TMDB answered 404 for, so a re-run reads the shards on disk, skips what they
contain, and asks only for what is missing. Ids that *failed* are deliberately not recorded
as skippable, so a transient failure is retried by the next run rather than baked in. A run
killed outright leaves at most one `.partial` shard, which the next run deletes and
re-fetches. Running the pull twice over a finished snapshot sends zero requests.

The token is read from `TMDB_READ_ACCESS_TOKEN` and from nowhere else. It is never logged,
never written into a shard or the manifest, and never committed — the repository is public.

## Running it

```bash
set -a && . /path/to/tmdb.env && set +a     # TMDB_READ_ACCESS_TOKEN, server-side only

make tmdb-ingest ARGS="--limit 200"         # smoke: ~10s, inspect a shard
make tmdb-ingest                            # the full catalog, ~52 min at 20 req/s

dvc add data/raw/tmdb/$(date +%F) && dvc push

make db-migrate                             # 0018 creates the tables
make tmdb-load                              # shards -> normalised Postgres
make tmdb-coverage                          # writes tmdb-coverage.md next to this file
```

`tmdb-load` and `tmdb-coverage` need no token: they read the snapshot on disk.

### Status, 2026-09-05

Everything above is built, unit-tested (53 tests, no network) and verified end to end
against a live Postgres — the migration applies, the twelve tables are created with their
column comments and grants and without RLS, the loader is idempotent across two runs, the
duplicate-`tmdb_id` fan-out lands as two rows, and the coverage report renders.

**The snapshot itself has not been pulled.** `TMDB_READ_ACCESS_TOKEN` is not set in this
environment and there is no `.env` in the checkout — only `.env.example` with an empty
value. The pull was stopped there rather than proceeding with an invented credential. When
the owner supplies the token, the three commands above produce the snapshot, the tables and
the coverage numbers with no further code changes.

## Coverage

The generated numbers land in `tmdb-coverage.md` beside this file, with the raw counts in
`tmdb-coverage.json`. Three populations, in ascending order of how much they matter:

1. **The whole catalog** — 62,423 movies, 62,316 of which carry a `tmdbId` (99.8%) over
   62,282 distinct TMDB ids.
2. **The cold items** — the 27,962 movies with no interaction in the ADR 0001 training
   frame. This is the population the rung exists for, and the tag genome covers 0.00% of
   it. Their TMDB coverage is the number ADR 0017 increment 2 is decided on.
3. **The items with no MovieLens genres** — 3,413 of them, which increment 1 cannot serve
   by construction. TMDB coverage here is the only way they become representable at all.

Each population is broken down by *field*, not collapsed into one "covered" figure: a movie
whose TMDB entry exists but carries an empty overview and no keywords is resolved and still
useless to a text representation. Collapsing those would hide exactly the thing increment 1
got wrong — a coverage number that looked like success while recall said otherwise.

The cold-item population is derived from the same `temporal_split` every trainer uses
rather than from a hardcoded id list, so the report cannot quietly describe a different
population than the models do.

| Population | Movies | TMDB-resolved | With overview | With keywords | With cast |
|---|---:|---:|---:|---:|---:|
| Whole catalog | 62,423 | 61,468 (98.5%) | 61,365 | 47,815 | 59,763 |
| Cold items (no train interaction) | 27,962 | 27,483 (98.3%) | 27,407 | 19,852 | 26,443 |
| No MovieLens genres (whole catalog; ADR 0017's 3,413 is the cold subset) | 5,062 | 4,893 (96.7%) | 4,862 | 2,602 | 4,464 |

Filled from the 2026-09-05 pull (`tmdb-coverage.md`, snapshot DVC md5 `da35a30da8b56ff7de01c9f08bb94be1.dir`,
62,081 requests at a self-throttled 7.1 req/s after 429s, 0 failures, 142 minutes). The argument for
increment 2 now rests on measured numbers: 98% of cold items have an overview and 95% have cast.

## What this does not settle

Coverage is necessary and not sufficient. Even at 99%, whether TMDB text and credits can
pick the *right* obscure film out of 24,549 candidates is an open question, and ADR 0017's
own risk section says the cold-item slice — 829 holdout rows over 313 users — may not be
able to separate two designs at any plausible effect size. This ingestion produces the
input for that measurement. It does not pre-judge it, and a high coverage figure quoted on
its own would repeat increment 1's mistake in a new costume.
