# ADR 0017 — Content-based retrieval for cold items

**Status:** Accepted
**Date:** 2026-09-05
**Approved:** owner, 2026-09-05

## Context

Every retriever in this system derives an item's representation from interactions. Item-item builds
neighbours from co-occurrence; the two-tower and SASRec learn item embeddings from consumption. That
works exactly as long as an item has been consumed, and fails completely when it has not — not
because the item ranks poorly, but because there is nothing in its entry to rank. Inserting a row for
it changes nothing.

The population is not hypothetical. Measured on the committed MovieLens 25M snapshot:

| Quantity | Measured |
|---|---:|
| Catalog | 62,423 |
| Movies with at least one rating | 59,047 |
| **Movies with zero ratings ever** | **3,376** |
| Movies appearing in the 28-day holdout that training never saw | **470** |
| Holdout interactions whose target is unseen in train | **829 (0.639%)** |
| Distinct holdout users affected | **313** |

Non-negotiable #3 requires "explicit answers for new users (no history) and new movies (no
interactions)". The first half is answered — the cold-start threshold routes low-history users to a
popularity fallback. The second half has never been built, and `docs/eda.md` has said so since the
first EDA: *"the candidate generator needs a fallback path for them (likely content-based once we
have features)."*

This matters more in production than the offline numbers suggest. Offline, a film released after the
training cutoff *should* be absent — putting it in the index would be leakage. In serving, a film
released this week must be recommendable on the day it is added, and today it cannot be.

## The signal that looked obvious and is not

The natural candidate is the tag genome: 1,128 tag-relevance scores per movie, already committed in
`data/raw/ml-25m/genome-scores.csv`. It is a rich, dense content vector and it would be the ideal
representation — except that it does not exist where it is needed.

| Signal | Catalog coverage | Coverage of the 3,376 cold items |
|---|---:|---:|
| Genome tag vectors | 13,816 (22.1%) | **0 (0.00%)** |
| Genres | 57,361 (91.9%) | 2,882 (85.4%) |
| Release year (parsed from title) | 62,011 (99.3%) | 3,338 (98.9%) |
| `tmdbId` in `links.csv` | 62,316 (99.8%) | ~99.8% |

**Not one cold item has a genome vector.** The genome is computed from user-applied tags, so it
exists only for films that already have engagement — it is inversely correlated with the need it
would serve. This is the single most important finding behind this ADR, and it was measured before
any code was written precisely because the design would otherwise have been built on it and failed at
the first cold item.

## What the cold items actually are

The ADR's own "how we would know we are wrong" asked for a sample to be inspected before
implementation, on the grounds that the population might be data artefacts. It was, and the answer
tempers the case rather than killing it. Eighteen drawn at seed 42:

```
Bullet Code (1940)                    Western
Wings for the Eagle (1942)            Drama
Up in Central Park (1948)             (no genres listed)
Fort Dobbs (1958)                     Western
Return of Shanghai Joe (1975)         Action|Western
Honeymoon Academy (1990)              Comedy|Drama
Caged Heat II: Stripped of Freedom    Action|Drama|Thriller
The Wife He Met Online (2012)         Thriller
Utopia in Four Movements (2010)       (no genres listed)
```

These are **real films, not artefacts** — the population is genuine. But they are overwhelmingly
**deep-catalog obscurities, not new releases**: mostly pre-2000, with a long tail of B-movies,
regional titles and documentaries that simply never accumulated a rating.

That distinction matters more than it first appears, because it means **the offline population and
the production need are not the same population**:

- **Offline**, "cold item" means *long-tail obscurity*. Making `Bullet Code (1940)` reachable is a
  coverage win and probably not a user-experience win, and this ADR should not pretend otherwise.
- **In production**, "cold item" means *recently added* — a film released this week, which must be
  recommendable on the day it enters the catalog. MovieLens 25M ends in 2019, so **this dataset
  cannot measure that case at all.**

The consequence for the claim: the offline work proves the *mechanism* — that a retriever can reach
items with no interactions — on the only population available. It does not, and cannot, demonstrate
the production benefit that motivates it. Anyone reading a cold-item recall figure from this rung
should read it as evidence the machinery works, not as evidence users are better served.

If that mechanism proof is not worth the increment on its own, the rung should be deferred rather
than justified by a benefit this dataset cannot show.

## Decision

Add a **content-based retriever** as a second candidate source, representing an item by its
attributes rather than by who consumed it, and route to it for items no interaction-derived source
can reach.

The representation is staged, cheapest-first, because coverage rather than richness is the binding
constraint:

1. **Genres and release year** — the day-one signal. No external dependency, present for 85.4% and
   98.9% of cold items respectively, and derivable from data already committed and DVC-tracked. A
   user's taste profile is the same genre-mask aggregate already measured for
   `user_genre_affinity` (11,532,291 distinct (user, mask) pairs, mean 71 masks per user), scored
   against the item's mask plus a recency prior on release year.
2. **TMDB metadata** — overview text, keywords, cast and crew, reachable for 99.8% of the catalog
   through `links.csv` and the proxy the backend already runs for posters. This is what covers the
   14.6% of cold items with no genres listed, and what makes the representation more than a
   19-dimensional bag. It is a separate ingestion with its own rate-limit, caching and licensing
   questions, and it is deliberately *not* in the first increment.
3. **Genome tags** — retained as an enrichment for *warm* items only, never as the cold-start answer.
   Stating this explicitly is the point: the obvious reach is the wrong one.

Cold items enter the slate through the existing candidate-source vocabulary in
`src/serving/policy.py` with their own source label, so an audit can say which candidates came from
content rather than from co-occurrence, and the frontend's "why this?" panel can say so too.

## Alternatives considered

**Do nothing and let popularity cover it.** This is today's behaviour. It is not a cold-item answer
at all — a new film is never in the popularity ranking either, so the user simply never sees it. The
failure is silent, which is the worst property it could have. Rejected.

**Put cold items in the index with a zero or random vector.** Costs nothing and does nothing: a zero
vector never wins a similarity comparison, and a random one is retrieved arbitrarily, which is worse
than not retrieving it. Rejected, and named here because "just add them to the index" is the natural
first suggestion and deserves a recorded answer.

**Wait for the two-tower or SASRec to learn item features.** ADR 0015's v2 fuses structured item
features into the item tower, which does help a *sparse* item. It does not help a *zero-interaction*
item, because the tower is trained on interactions and a never-consumed item contributes no gradient.
Rejected as a substitute; complementary if v2 is ever revived.

**Content-only retrieval for everyone.** Replacing collaborative retrieval with content similarity
would regress warm users badly — item-item's warm recall@500 of 0.400144 is exactly the co-occurrence
signal content cannot reconstruct. Rejected; this is a second source, not a replacement.

**A hybrid scoring function blending content and collaborative similarity for all items.** Defensible
and probably the right end state, but it changes retrieval for the 99.4% of traffic that is working
today in order to serve the 0.6% that is not. Deferred until the content source has a measured value
of its own.

## Consequences

The serving path gains a second candidate source and therefore a second failure mode: content
retrieval that returns plausible-looking but irrelevant items is harder to notice than an empty
slate. The source label and the exclusion digest are what make it auditable.

The evaluation harness gains a **cold-item slice**, which it does not have today — the existing
warm/cold split is by *user* history, not by item novelty. That slice is `829` holdout rows over
`313` users, and its thinness is the main threat to this rung being decidable at all (see Risks).

`docs/results.md` gains a line that is not comparable to any existing one, because no current model
is evaluated on cold items. The first measurement establishes the baseline rather than beating one.

Second-order: once items have content vectors, the same vectors are available to the ranker as
features, and to a future diversity or re-ranking stage (M6) that needs a notion of item similarity
that does not collapse to co-consumption. This ADR does not claim those; it notes the representation
is reusable.

## Risks

**The evaluation slice is underpowered.** 313 users is thin, and a recall difference on it will carry
wide intervals — especially under the 2026-09-05 one-run policy, where there is no seed spread to
measure either. This rung may be able to demonstrate *coverage* (cold items become reachable at all)
far more convincingly than *quality* (they are the right cold items). The honest framing is that
coverage is the primary claim and relevance is secondary and weakly measured.

**Genres are a weak signal.** Nineteen genres over 1,639 observed combinations is a coarse space; two
films sharing "Drama|Romance" may have nothing else in common. The first increment may show reachable
but poor recommendations, and that result should be published rather than tuned away — it is the
argument for increment 2.

**TMDB is an external dependency** with rate limits, terms of use, and data that changes underneath a
cached copy. Any ingestion must be snapshot-versioned like the ratings frame, or it silently breaks
the reproducibility guarantee in non-negotiable #5.

**14.6% of cold items have no genres at all.** Increment 1 cannot serve them by construction. They
are not a rounding error and the design should say what happens to them: today, nothing.

## How we would know we are wrong

- If the cold-item slice's recall under content retrieval is indistinguishable from zero, the
  representation is too coarse and increment 2 is mandatory rather than optional.
- If serving-side p99 moves measurably, a per-request content similarity over the catalog is the
  wrong shape and the vectors need precomputing into the index instead.
- If the 3,376 zero-rating movies turn out to be overwhelmingly items no user would ever want — data
  artefacts, duplicates, non-films — then the population is smaller than it looks and the rung is
  worth less than this ADR assumes. Nobody has inspected a sample of them, and that inspection should
  precede implementation.
- If a measured cold-item slice cannot separate two candidate designs at any plausible effect size,
  this rung cannot be gated and should be judged on coverage and latency alone, with relevance
  recorded as unproven.

## Result, 2026-09-05 — coverage achieved, slate quality regressed

The first increment was built and measured on the full 25M under threshold routing, 2,641 holdout
users.

| | Content retriever | Item-item |
|---|---:|---:|
| Warm recall@500 | **0.0388** | **0.400144** |
| Warm NDCG@500 | 0.0122 | 0.139240 |
| Overall recall@500 | 0.1698 | 0.434269 |

| Coverage | |
|---|---:|
| Items with no interaction in train | 27,962 |
| — servable by this increment (have genres) | 24,549 |
| — not servable, by construction | 3,413 |
| **Distinct cold items retrieved** | **4,998 (17.9%)** |
| Cold items per 500-slate | **115.3 (23.1%)** |
| Reachable by any interaction-derived retriever before | **0** |

**The claim this ADR was approved on holds.** 4,998 items that no interaction-derived retriever could
ever surface are now retrievable, against a prior of exactly zero. The mechanism works.

**And standalone it is not a usable retriever.** Warm retrieval is 10.3× worse than the champion, and
the reason is visible in the same table: 23.1% of every slate is cold items, so obscure titles are
displacing good ones wholesale. The coverage figure read on its own would be badly misleading, which
is why it is recorded here beside the recall it cost.

The question this rung cannot answer is the one that matters next: does a *bounded budget* of content
candidates add reachability without displacing good ones? That is source mixing with an allocation
policy — rung 5 — and it needs union, dedupe and attribution machinery that does not exist yet. This
measurement is the argument for building it, and the argument against shipping content retrieval as a
standalone source. **Item-item remains the retrieval champion; nothing here is promoted.**

Two gaps this run exposed, recorded rather than left to be rediscovered:

- **`catalog_fingerprint` under-describes a whole-catalog retriever.** It is derived from the fitted
  frame, because every other retriever ranks only within its training data. This one ranks the entire
  catalog — that difference *is* the rung — so the recorded fingerprint names a smaller catalog than
  the model retrieves from. `n_catalog_items` and `n_items_in_train` are logged as explicit params in
  the meantime, but the protocol contract exists precisely so that runs answering different questions
  do not compare equal, and here it does not do its job.
- **The implementation uses era *proximity*, not the recency prior this ADR specified.** Proximity to
  the nearest year the user actually watched, rather than a bias toward newer films, on the reasoning
  that a global newness prior would systematically demote the pre-2000 titles the offline cold
  population is made of. Defensible, and a deviation: either this ADR or the code should move.

## The falsification condition fired, 2026-09-05

This ADR's "how we would know we are wrong" said: *if the cold-item slice's recall under content
retrieval is indistinguishable from zero, the representation is too coarse and increment 2 is
mandatory rather than optional.* The slice was built, wired into both trainers, and measured. The
condition fired.

Both runs on the full 25M, threshold routing, same 2,641 holdout users, cold-item slice over the 313
users holding at least one of the 829 targets the training data never contained:

| | Item-item | Content (genres + year) |
|---|---:|---:|
| Warm recall@500 | **0.3991** | **0.0388** |
| Cold-item recall@500 | **0.0000** | **0.0001** |
| Cold items per 500-slate | 0 | 115.3 (23.1%) |
| Distinct cold items retrieved | 0 | 4,998 |

**The baseline is measured, not assumed.** Item-item scores exactly 0.0000 on the slice, which is
what an index built from co-occurrence must score on items that never co-occurred. That is what makes
"from a prior of zero" a measurement rather than an argument.

**And the increment fails on its own terms.** It pays a tenfold collapse in warm recall to move
cold-item recall from 0.0000 to 0.0001. The coverage is real but *indiscriminate*: it floods a
quarter of every slate with cold items and retrieves 4,998 distinct ones, while almost never
retrieving the one the user actually watched. Genre-and-year similarity cannot pick the right obscure
film out of 24,549 candidates, which is precisely the "genres are a weak signal" risk this ADR
listed.

Read the two coverage numbers together and the shape is clear: 4,998 items reached, 0.0001 recall.
Reporting the first without the second would have made a failed increment look like a success, and
the whole reason the falsification condition was written before the run was to make that impossible.

**Consequence, per this ADR's own text.** Increment 1 does not stand on its own. Either increment 2
becomes required — TMDB metadata, which also covers the 3,413 items with no genres at all — or the
rung stops here with a recorded negative result. That is an owner decision, and it should be taken
knowing that nothing about this measurement argues the *mechanism* is wrong: cold items became
reachable exactly as designed. What failed is the representation used to choose among them.

Item-item remains the retrieval champion. Nothing here is promoted, and the content retriever should
not be added to the serving slate in this form.

## TMDB metadata ingested, 2026-09-05

The owner took the first branch of the consequence above — increment 2 becomes required —
and widened it (decision O-2): pull everything useful for a strong recommender, not only
what this rung strictly needs, because the same snapshot is the item side of a two-tower's
side features and the only description this system has of a film nobody has watched.

**What was built.** A rate-limit-safe, resumable snapshot of `GET /movie/{id}` for every
distinct TMDB id in `links.csv` — 62,282 of them, covering 62,316 of the 62,423 catalog
movies — with `keywords,credits,release_dates,external_ids,alternative_titles,translations`
folded into the same request. Gzipped JSONL shards under `data/raw/tmdb/<pull-date>/`,
DVC-tracked and never committed, with a manifest carrying the pull date, the API version,
per-run request/404/failure counts and a SHA-256 per shard. Twelve normalised Postgres
tables behind migration `0018_tmdb_catalog`. The full write-up is
[`docs/data/tmdb-metadata.md`](../data/tmdb-metadata.md).

`images` is deliberately not appended: it returns every artwork entry in every language,
and the two paths this system uses are already in the base payload.

**The snapshot answers this ADR's third risk directly.** It said any TMDB ingestion must be
snapshot-versioned like the ratings frame or it silently breaks non-negotiable #5. It is —
DVC-tracked, with the pull date in the manifest and shard bytes that depend only on their
contents, so a re-pull of unchanged data leaves `dvc status` clean.

**The leakage rule, which is the part that binds.** `vote_average`, `vote_count`,
`popularity`, `budget`, `revenue` and `status` are as-of-pull values with no observation
history, so nothing can reconstruct them at a 2019 prediction time. They are stored and
they are **not features**, enforced by a block comment in the schema module, a `COMMENT ON
COLUMN` on each of the six, the data doc, and a unit test that fails if any of them reaches
`src/feature_contract.py`. Everything else in the snapshot — genres, overview, keywords,
cast, crew, runtime, release date, language, collection, certification — is a static
attribute of the film and is the intended feature set.

**What the data itself said before any model saw it.** 34 TMDB ids in `links.csv` are
claimed by *two* MovieLens movies each (69 rows) — duplicate catalog entries for one film.
That forced `movie_id` rather than `tmdb_id` as the key of `tmdb_movies`, and
`(movie_id, credit_id)` for cast and crew. Worth recording because it is the kind of thing
a mapping is assumed to be free of until the primary key rejects it.

**Coverage is not yet measured.** The ingestion, the loader and the coverage report are
built and verified end to end against a live Postgres, but `TMDB_READ_ACCESS_TOKEN` was not
present in the environment when this landed, so the pull has not run. The three coverage
numbers this rung is decided on — resolution and per-field coverage over the whole catalog,
over the 27,962 cold items, and over the 3,413 items with no MovieLens genres — are
generated by `make tmdb-coverage` the moment the snapshot exists, and are left visibly
blank in the data doc until then rather than estimated.

Nothing here changes the verdict above: item-item remains the retrieval champion, and
increment 2 is an input to a measurement, not a result. A high coverage figure quoted on
its own would repeat increment 1's mistake — that is the whole lesson of the section above
this one.

## Scope of the first increment

Genres and release year only; a content retriever with its own source label; a cold-item evaluation
slice; a measured coverage number and a first relevance figure. No TMDB ingestion, no hybrid scoring,
no serving integration until the offline number exists.

Approved by the owner on 2026-09-05 with the narrowed claim intact: this rung proves the mechanism
on the only population the dataset offers, and does not demonstrate the production benefit that
motivates it. Anyone reading its recall figure should read it that way.
