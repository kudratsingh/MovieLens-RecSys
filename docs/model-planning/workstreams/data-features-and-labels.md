# Data, features, sequences, and labels workstream

## Purpose

Define the point-in-time data products every model consumes, keep them reproducible, and prevent
demo-scale representations from being mistaken for full MovieLens production designs.

## Data layers

### L0 — Immutable source

- MovieLens raw files pinned by DVC.
- Source checksums, schema, row counts, timestamp range, and catalog identity.
- Synthetic cohorts stored separately and tagged; they never rewrite MovieLens.

### L1 — Canonical events and catalog

- Normalized positive interactions under ADR 0002.
- Stable user/movie identifiers and deterministic timestamp tie policy.
- Movie metadata available-time rules; static fields may be treated differently from mutable
  enrichment.
- Tenant/synthetic lineage where data enters tenant-aware training.

### L2 — Temporal split and examples

- Train, holdout, rolling backtests, and sealed test membership.
- Point-in-time sequence prefix/target examples.
- Ranker query groups, positive targets, candidate negatives, and exclusions.
- Synthetic h0/h1/h3/h10 inputs and targets.

### L3 — Model inputs

- Encoded vocabularies and unknown/padding rules.
- Compact user aggregates and item metadata.
- Candidate-time joined ranker rows.
- Feature/sequence schema fingerprints.

Each derived layer needs a manifest with parent hashes, builder code SHA/config, schema hash, row
counts, min/max timestamps, creation time, and content checksum.

## Sequence-example contract

- Sort per user by `(timestamp, movie_id)` only for deterministic grouping.
- Items sharing a timestamp receive the same strictly earlier prefix.
- A target never appears in its prefix.
- Use every eligible next-timestamp item as a target unless the experiment says otherwise.
- Left-pad with reserved zero; unknown is a distinct reserved ID.
- Truncate to the most recent approved maximum length and log truncated interactions.
- Training uses only the train split. Holdout/test interactions never become context for the same
  evaluation decision.
- Online uses the same ordering, qualification, truncation, and unknown rules.

Preserve the current dense builder as a fixture oracle. A scale implementation may stream or pack
examples but must reproduce oracle outputs exactly on randomized and boundary fixtures.

## Negative-sampling contract

For a sequence example, a sampled negative cannot be:

- padding or unknown sentinel;
- the positive target;
- any item in the available prefix;
- a duplicate within that example's sampled set;
- outside the eligible training catalog.

Log sampling distribution, requested/accepted draws, rejection reasons, catalog size, count, seed,
and throughput. A popularity-aware, log-uniform, in-batch, or hard-negative sampler is a changed
experimental axis and must not appear through an implementation default.

Vectorization must preserve exclusions and determinism. If bounded retries are used, define the
fallback/error behavior when the eligible catalog is too small.

## Ranker candidate and label contract

- Candidate generation at training uses the same source family/version and exclusions as serving.
- Query groups bind `(user, prediction timestamp)`; group ordering is deterministic.
- Features use only information available at that timestamp.
- Already-seen/dismissed candidates cannot become routine negatives when serving would remove them.
- Sampled negatives and naturally retrieved non-relevant candidates remain distinguishable for
  analysis.
- The label is binary implicit relevance until an ADR changes it.

If a new retriever changes candidate distribution, retrain/evaluate the ranker on that distribution
before promoting the paired bundle.

## Feature-store boundary

Recommended responsibility split:

- Python point-in-time builders remain the reference for arbitrary historical training rows.
- Feast historical retrieval may own productionized retrieval of persisted historical features
  only when it proves identical timestamp semantics.
- Feast/Redis owns materialized online snapshots and entity-keyed retrieval.
- A three-way fixture compares reference historical value, Feast historical output, and Redis
  online value for the same entity/event time where all are expected to represent the same state.

The boundary must be documented explicitly; “uses Feast” is not itself proof of point-in-time
correctness.

## Full-scale feature representation

The current demo-safe user×movie genre-affinity cross product is not acceptable for 162k users ×
roughly 62k catalog items.

Preferred design to validate:

1. Store compact per-user genre preference aggregates with event time.
2. Store item genre vector/metadata once per item.
3. Compute affinity only for retrieved candidates during historical row construction and serving,
   or materialize bounded candidate-associated rows for a named snapshot.
4. Batch the join and measure CPU, memory, row count, Redis payload, and p99 contribution.
5. Preserve the ordered eight-feature contract until an ADR/version changes it.

Alternatives include sparse vectors or database-side candidate joins. Reject any design whose row
count scales as all users × all catalog items.

## Cold-item evaluation proposal

ADR 0011 deliberately covers cold users, not cold items. Before a content-aware retriever claims
cold-item value, define:

- item catalog-availability time;
- “cold” interaction-count threshold measured before prediction time;
- eligibility requiring at least one future relevant event;
- buckets for zero, 1–N, and mature prior interactions;
- whether truly zero-interaction catalog items can be evaluated in MovieLens at all;
- recall/coverage metrics and whether the slice is diagnostic or a gate.

Do not treat catalog items with no MovieLens interactions as positive targets merely because they
exist. Side-feature representability and observed cold-item recommendation quality are separate
claims.

## Label evolution

Current truth:

- every MovieLens rating is a positive interaction;
- rating magnitude is stored but excluded from current model labels;
- dismissals are exclusions, not negative training labels;
- watchlist is organizational, not positive relevance;
- the dataset has no product impressions, clicks, completion, duration, or skips.

Any multi-objective change begins with an event/label audit and ADR 0002 amendment. Represent
unknown and censored outcomes explicitly. A missing event cannot become zero until its observation
window has closed and the user was known to be exposed.

## Data-quality gates

For every derived snapshot:

- required columns/types and non-null constraints;
- unique identifiers and reserved-ID collision checks;
- timestamp range and no future records per example;
- exact split membership and boundary ties;
- item/user/catalog coverage and unknown counts;
- sequence/target counts and prefix-length distribution;
- sampler collision/rejection distribution;
- ranker group-size sum equals row count;
- feature missingness/range and online materialization freshness;
- tenant/synthetic isolation where applicable;
- deterministic content hash under same inputs.

Fail snapshots before training; do not let a trainer silently repair or drop unexpected rows.

## 25M versus 32M trigger

Stay on MovieLens 25M for comparability until one of these is approved:

- a key slice lacks enough users/targets for its declared uncertainty;
- newer catalog/events are required by a named hypothesis;
- 25M-specific selection effects prevent an approved model question;
- a final migration experiment is explicitly intended to test dataset drift.

A migration creates a new protocol lineage. Re-run baselines; do not compare 25M and 32M metric
deltas as though only the model changed.

## Exit criteria

- Derived snapshots are immutable, hashed, and parent-linked.
- Reference and scale sequence builders agree on boundary fixtures.
- Negative sampling is deterministic, observable, and bounded.
- Ranker candidates/features match serving semantics.
- Full-scale feature rows do not use an all-user/all-item cross product.
- Cold-item and multi-objective claims cannot proceed without accepted label/slice contracts.
