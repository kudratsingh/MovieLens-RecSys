# ADR 0017 — Content-based retrieval for cold items

**Status:** Proposed
**Date:** 2026-09-05

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

## Scope of the first increment

Genres and release year only; a content retriever with its own source label; a cold-item evaluation
slice; a measured coverage number and a first relevance figure. No TMDB ingestion, no hybrid scoring,
no serving integration until the offline number exists.

Per CLAUDE.md the rung needs the owner's approval before implementation, and its row in
`docs/modeling-roadmap.md` is the owner's to add.
