# ADR 0016 — SASRec for Sequential Retrieval

**Status:** Accepted
**Date:** 2026-09-03

**Pilot outcome (2026-09-04):** The corrected deterministic 6% pilot passed
its stop rule. With the same seed-42 users, architecture, two-epoch budget, 32
unique uniform negatives, and exact retrieval, standard BCE reached warm
recall@500 of **0.3186** (NDCG@500 0.1089), versus gBCE at **0.2937**
(NDCG@500 0.1022). BCE is 8.5% higher in warm recall and both clear the same
slice's 0.1974 popularity reference. The frozen full-data configuration is
therefore BCE, 32 negatives, two epochs, seed 42, and exact FAISS. These runs
include the fix in commit `1d189a8`: retrieval runs in evaluation mode and
each training epoch explicitly restores training mode. Earlier runs are
diagnostic only because dropout was active during evaluation and the epoch-one
callback left epoch two in evaluation mode.

**Decision note (2026-09-04):** Approved as the next model after ADR 0015's
bounded pilot triggered its stop rule. The owner explicitly directed the work
to move from the repaired two-tower to the next model. Implementation remains
subject to the staged correctness, pilot, promotion, and serving gates below.

## Context

The current retrieval champion is item-item cosine similarity. At the shared
cold-start threshold of ten positive interactions it reaches warm recall@500
of 0.3991. The first learned retriever, the history-mean two-tower from
[ADR 0006](0006-two-tower-retrieval-architecture.md), was measured and not
promoted: its best swept configuration reached 0.0591. The sweep ruled out
training duration, learning rate, and approximate nearest-neighbour search as
the primary explanations. It also exposed a missing softmax temperature, but
fixing that objective defect did not close the retrieval gap.

The two-tower and item-item models both discard sequence order. Item-item uses
co-occurrence around history seeds; the two-tower mean-pools the last 50 item
embeddings. Neither can distinguish “watched recently” from “watched years
ago,” represent a change in taste, or learn that one viewing commonly follows
another. MovieLens supplies timestamps, the evaluation split is temporal, and
the serving path already reads ordered positive history. Sequence is therefore
the largest unused signal already present in the system.

Rung 2 of [`docs/modeling-roadmap.md`](../modeling-roadmap.md) names SASRec as
the expected sequential model. This ADR records its design ahead of that work,
but the owner has deferred it behind the Phase 3 platform closeout and
two-tower v2. The roadmap requires the owner to change both this status and the
decision-log row to `approved` before training code begins.

## Proposal

Build a SASRec candidate retriever: a causal Transformer encoder over a user's
ordered positive interaction history, trained to predict the next item. The
first implementation will be deliberately narrow so its result answers whether
sequence modeling helps, rather than whether a large search budget can rescue
an underspecified experiment.

### Model contract

- Input is the most recent 50 positive item interactions strictly before the
  target timestamp, ordered oldest to newest. Fifty matches ADR 0006 so the
  comparison changes the encoder, not the amount of history available.
- Dismissals, watchlist additions, and rating magnitude are excluded. This
  preserves [ADR 0002](0002-implicit-feedback-label.md) and ADR 0012's meaning
  of a positive signal. Revisiting labels is a separate decision.
- The encoder uses learned item and positional embeddings, two causal
  self-attention blocks, two attention heads, hidden width 64, a point-wise
  feed-forward width of 256, dropout 0.2, pre-layer normalization, and padding
  masks. These are starting defaults, not claims of optimality.
- The final non-padding position is the user representation. Item embeddings
  are shared between the input and output tables. Both user and item vectors
  are normalized before retrieval so the existing inner-product FAISS boundary
  remains usable.
- The maximum item vocabulary is the training catalog plus explicit padding
  and unknown ids. An unknown or post-snapshot item cannot silently alias a
  trained title.
- Histories below `COLD_START_THRESHOLD` continue to use popularity. SASRec
  does not receive special routing merely because it can technically encode a
  one-item sequence. The synthetic h0/h1/h3/h10 slices remain the evidence for
  revisiting that shared threshold later.

### Training contract

- Construct examples by sorting each user's interactions by `(timestamp,
  movie_id)` and predicting item `i` only from positions `< i`. The tie-breaker
  is deterministic; the target is absent from its own context; no validation or
  holdout interaction enters a training sequence.
- Use every eligible next-item position in the training split rather than one
  final target per user. Record the number of users, sequences, targets, and
  truncated interactions in MLflow.
- Start with sampled negatives and generalized binary cross-entropy following
  gSASRec. The negative count and the gBCE calibration parameter are explicit,
  logged configuration. Standard SASRec binary cross-entropy is a required
  ablation so any gain can be attributed to the loss rather than asserted from
  the paper.
- Negatives must not be padding, the positive target, or an item already in the
  example's prefix. Sampling is seeded and its distribution is logged. A
  popularity-aware or in-batch sampler may be compared, but the default must be
  named by the implementation ADR note before the full run.
- The implementation first overfits a small deterministic slice and passes
  point-in-time canaries. A bounded pilot then fixes obvious defects. Only the
  frozen pilot configuration receives the full MovieLens 25M run.
- Run at seeds 42, 7, and 13 when compute permits. At minimum, the promoted
  claim must use the same aggregation and seed policy as its comparator; a
  single favorable seed cannot promote the model.

### Evaluation and promotion contract

- Primary model metric is recall@500 on the existing temporal holdout through
  `src/evaluation/`, reported overall, warm, cold, and by serving policy.
- Compare against item-item at the same threshold, exclusions, catalog, split,
  K, and eligible-user set. Also report popularity@500 and two-tower v1 so the
  result has both a floor and lineage.
- Record NDCG@500 as diagnostic evidence, not as a substitute for candidate
  recall. The LightGBM ranker still owns final ordering.
- Report the ADR 0011 synthetic h0/h1/h3/h10 slices even though h0/h1/h3 route
  to popularity. The h10 boundary is the first direct sequential slice.
- Add coverage and popularity diagnostics: unique items retrieved, catalog
  coverage, mean item popularity rank, and the fraction of holdout targets
  reachable from the retrieved set. These cannot promote SASRec, but they can
  reveal a model that raises recall only by collapsing onto the head.
- SASRec replaces item-item only if it clears the retrieval promotion criterion
  in ADR 0004 and does not violate the online latency gate. A newer architecture
  earns no serving preference on its name.

### Serving contract

- Export a checksum-pinned encoder and item matrix in the serving manifest.
  The manifest records sequence length, vocabulary fingerprint, architecture,
  loss parameters, and the ordered feature contract.
- The private model sidecar loads the encoder once at startup. Per request it
  receives ordered positive movie ids, encodes one sequence, then queries the
  existing FAISS boundary for approximately 500 candidates.
- Candidate exclusions remain structurally separate from positive history and
  are applied during retrieval and by the coordinator's final fail-closed
  sweep. A dismissed title never becomes a sequence token.
- The prediction audit gains the SASRec artifact version and encoder latency;
  the existing input-state hash continues to identify the sequence used.
- The unchanged authenticated k6 profile remains authoritative: p99 must stay
  below 100 ms with correct warm, cold, and mixed responses. The proposal sets
  an internal encoder target of p99 below 15 ms on the production topology to
  leave room for auth, RLS, ranking, hydration, audit commit, and variance.
- No online sequence feature is materialized separately for v1. The ordered
  history already lives in Postgres and is read for routing and exclusions.
  Duplicating it in Redis before measurement would introduce a second source of
  truth without proving it is needed.

## Why SASRec before two-tower v2

SASRec tests a different hypothesis with a signal the current models discard.
Two-tower v2 adds hard negatives and item side features to an architecture that
currently trails item-item by 6.8 times. Those additions remain valuable for
cold items, but no cold-item evaluation slice exists yet and the product's
near-term question is next-item relevance for users with real histories.

The causal encoder also creates a representation later rungs can reuse. Rung 3
can expose its user embedding and candidate-attention signals to LightGBM before
considering a neural ranker. Work on sequence correctness therefore compounds;
work on an id-and-content item tower primarily advances a separate cold-item
track.

This ordering does not declare two-tower v2 a bad model. If the owner decides
cold-item coverage is the more urgent product gap, Rung 1 should be approved
instead and this proposal should remain proposed or be marked deferred.

## Alternatives considered

### Two-tower v2 first

Hard negatives, side features, and a corrected temperature are the direct
repair path for ADR 0006. It preserves the cheapest online encoder and provides
an embedding for unseen items. It is the right choice when cold-item retrieval
is the next product requirement. It is not proposed first because current
evidence shows a very large quality deficit, there is no cold-item slice with
which to judge its main benefit, and it still discards order.

### BERT4Rec

Bidirectional masked-item training can use context on both sides during
training, but online next-item prediction has only a left context. It adds a
masking objective and more expensive training before this project has shown
that a causal sequence encoder helps at all. gSASRec reports that calibrated
negative-sampling training can outperform BERT4Rec with lower training cost;
the project should reproduce the causal baseline before paying for this branch.

### GRU4Rec or a convolutional sequence model

These are credible lower-compute sequence baselines and the original SASRec
paper compares against recurrent and convolutional approaches. Implementing
one first would teach less of the transformer-based path the roadmap targets,
while creating another serving implementation that later rungs do not plan to
reuse. A GRU becomes a useful diagnostic only if SASRec cannot beat a simple
recency baseline and attention itself is suspected.

### Add recency features to item-item or LightGBM

Recency-weighted seeds and aggregate recency features are cheap, interpretable,
and should eventually be ablations. They do not model transitions or expose a
sequence representation to later stages. They answer whether recency matters,
not whether order and conditional next-item structure matter.

### Keep item-item and move directly to a sequence-aware ranker

This could improve NDCG@10 without disturbing the strong retriever. It also
leaves candidate recall capped by item-item and makes the ranker score hundreds
of candidates with per-candidate sequence attention. Rung 3 explicitly depends
on a sequence encoder; measuring that encoder as retrieval first gives a clean,
stage-specific verdict.

## Consequences

- Training and serving gain a Transformer implementation, but the existing
  artifact, sidecar, FAISS, audit, and evaluation boundaries remain intact.
- Point-in-time sequence generation becomes a load-bearing reusable component.
  Its tests must be stronger than model-convergence tests because leakage can
  improve every metric while producing an unusable model.
- Online latency now includes inference rather than only lookup and tree
  scoring. Model quality and the p99 gate are co-equal promotion conditions.
- The model remains weak on truly unseen items because its output table is
  id-based. That is explicit debt owned by two-tower v2 or a later content-aware
  retriever, not hidden under a “cold start” label.
- Sampled-negative design becomes part of the result. Loss, sampler, count, and
  calibration must be logged so a promising number is reproducible.
- If SASRec loses, the result is still useful: it says sequence order adds less
  than co-occurrence on this dataset under the fixed protocol, and item-item
  remains champion without apology.

## Risks and mitigations

- **Temporal leakage.** Centralize sequence construction and test exact prefixes
  around equal timestamps and the train/holdout boundary.
- **False negatives.** Exclude prefix items and the target; log sampler
  collision/rejection rates; compare one alternate sampler in the pilot.
- **Popularity collapse.** Report catalog coverage and popularity diagnostics
  beside recall rather than discovering the collapse in the product.
- **Evaluation mismatch.** Use the shared threshold, split, exclusions, K, and
  evaluator; refuse results whose protocol metadata differs from the champion.
- **CPU training cost.** Use deterministic smoke and pilot gates before the full
  run, log wall-clock and peak memory, and stop configurations that fail to beat
  popularity@500 on the pilot.
- **Serving regression.** Benchmark exported inference in isolation, then run
  the unchanged end-to-end k6 gate. Do not relax either budget to promote it.
- **Scope creep.** v1 excludes side features, multi-objective labels, BERT-style
  masking, and neural re-ranking. Each is a later decision supported by an
  ablation, not an unreviewed addition.

## How we would know this decision is wrong

- A deterministic pilot cannot beat popularity@500 or a last-item nearest-
  neighbour baseline after data and leakage checks pass.
- Full-data warm recall@500 fails ADR 0004's promotion criterion against
  item-item across the required seeds. SASRec is then measured, not promoted.
- Gains disappear when already-seen items and training-prefix collisions are
  removed, showing that the result depended on leakage.
- Recall improves but catalog coverage collapses enough that the model is only
  a more expensive popularity retriever.
- Exported encoder inference cannot fit the 15 ms internal p99 budget, or the
  unchanged service gate reaches 100 ms, on the production topology.
- Sequence-order ablation (shuffle the same prefixes deterministically) matches
  the trained model within seed variance. That would show order was not the
  source of the gain and reopen the simpler alternatives.
- The product priority changes to unseen-item coverage before implementation.
  In that case approve Rung 1, define the cold-item slice, and defer this ADR.

## References

- Wang-Cheng Kang and Julian McAuley, “Self-Attentive Sequential
  Recommendation,” IEEE ICDM 2018, DOI 10.1109/ICDM.2018.00035.
- Aleksandr Petrov and Craig Macdonald, “gSASRec: Reducing Overconfidence in
  Sequential Recommendation Trained with Negative Sampling,” ACM RecSys 2023,
  DOI 10.1145/3604915.3608783.
