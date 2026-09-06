# ADR 0019 — Multi-retriever mixing and diversity re-ranking (Rung 5)

**Status:** Proposed
**Date:** 2026-09-05

## Context

The corrected SASRec artifact reaches warm recall@500 **0.5092**, while item-item reaches
about 0.399 on the same full-data protocol. The corrected two-tower is architecturally
complementary and cleared item-item on the six-percent diagnostic; its one full-data result
must be recorded before this proposal becomes an execution plan.

Rung 3 narrowed the next question. SASRec's own scores improved warm NDCG@10 only
**1.86%** over a same-frame control. The features carried 82.3% of the fitted model's gain
but missed the +3% gate: its scalar opinion mostly restated its own ordering. A genuinely
different source can add items that SASRec never offered, a different and testable claim.

The audit already preserves `candidate_sources`; the coordinator deduplicates and applies
fail-closed exclusions after mixing. What is missing is a fixed 500-candidate allocation,
source provenance for the ranker, and diversity that is not bought with lost relevance.

## Proposal

First prove complementary retrieval under a fixed budget, then train a source-aware
ranker, then try diversity re-ranking. This proposal approves neither code nor runs.

### 1. Build one attributed, deduplicated candidate pool

Query SASRec and the corrected two-tower for 500 each. Add item-item as a third experimental
source. Popularity only fills after exclusions when the union has fewer than 500 eligible
movies; it never displaces a retrieved item.

Deduplicate by `movie_id`. Each row retains every membership, each source's 1-based rank,
and a missing-rank sentinel; `candidate_sources` records all contributors. Raw similarities
are diagnostic and are never compared across families as if their scales were calibrated.

The raw union can contain 1,500 movies and is not a fair recall@500 challenger. Deterministic
reciprocal-rank fusion converts it to 500, with equal predeclared weights and ties broken by
best individual rank then `movie_id`. Report raw and budgeted recall; only the latter gates.

Training uses only interactions strictly before the positive timestamp. Serving applies
the same full-history watched, watchlisted, and dismissed exclusions after union and before
fill. Mixing must not create a second exclusion implementation.

### 2. Train a source-aware ranker on the mixed distribution

Fit the warm LightGBM ranker on candidates from the exact served mixer. Keep the fallback
route and booster byte-identical. Add to the accepted feature contract:

- one membership boolean and reciprocal-rank feature per source (zero when absent); and
- `candidate_source_count`, the number of independent contributors.

Rank is stable under a model's scoring transformation. Membership lets LightGBM learn
agreement without hiding attribution in a fused score. The ranker still sees one row per
movie, so three sources do not create three training examples.

The control uses the same candidates without source-aware columns. This separates coverage,
retraining-distribution, and provenance gains—the attribution Rung 3 could not supply.

### 3. Re-rank for diversity, with MMR first

Only after the mixed ranker clears its gate, apply maximal marginal relevance (MMR). It is
first because its trade-off is explicit, cheap, and ablatable. Similarity is cosine over
MovieLens genre multi-hot vectors; exclude missing-genre pairs and report their coverage.
Business rules remain final constraints, not invisible offline-metric inputs.

The metric is **genre intra-list diversity at 10 (genre-ILD@10)**: mean pairwise Jaccard
distance between nonempty genre sets in each top ten, averaged over users. Report evaluable
pair coverage so missing metadata cannot masquerade as diversity.

Choose MMR on a bounded predeclared grid. Maximize genre-ILD@10 only among cells whose
paired warm NDCG@10 is not below the unreranked control beyond the measured warm tolerance.
ADR 0001 does not move; a diversity win that changes a pass into a refusal loses.

A DPP is considered only if MMR establishes usable diversity headroom but cannot exploit it
inside the guardrail. Its kernel and latency require a dated amendment, not an automatic sweep.

### Evaluation and promotion contract

All arms share the 28-day holdout, cohort, seed 42, protocol hash, 500 cap, and exclusions.
One run per configuration follows the owner's rule. Retain every metric, per-user document,
manifest, log, and model artifact regardless of verdict.

The stages are judged in order:

1. **Retrieval:** budgeted mixed recall@500 must beat the stronger same-protocol single
   source under ADR 0004. Raw-union recall is diagnostic.
2. **Ranking:** against the per-route SASRec bundle, O-1 requires at least +3% warm
   NDCG@10, cold within measured tolerance, and overall reported but not gated.
3. **Diversity:** MMR must improve genre-ILD@10 while preserving the paired warm guardrail.
4. **Latency:** authenticated p99 remains **<100 ms**. SASRec retains its isolated
   **<15 ms** budget; two-tower receives at most the same, making dual retrieval's
   diagnostic budget **<30 ms**, with fusion and MMR in remaining headroom.

The ranking comparison is the per-route SASRec bundle, not item-item plus LightGBM, so the
arm cannot bank SASRec's accepted gain again. Any cold movement indicates bundle skew.

### Stop rules

1. If the budgeted union does not clear the retrieval gate over the best single source,
   stop before ranker training. More raw candidates without more fixed-budget recall are
   not a serving improvement.
2. If most raw-union gain disappears when capped at 500, stop and record allocation—not
   model quality—as the bottleneck. Do not tune fusion weights after reading the holdout.
3. If the mixed ranker gains less than 3% warm NDCG@10, or cold moves outside tolerance,
   record and stop before diversity re-ranking.
4. If source-aware columns do not beat the same-frame control, keep the simpler mixed
   ranker; candidate diversity, not provenance features, carried any gain.
5. If MMR cannot improve genre-ILD@10 inside the relevance guardrail, ship no diversity
   layer. DPP requires the specific MMR failure described above, not general curiosity.
6. If dual retrieval breaches either the isolated diagnostic budget or unchanged service
   gate, stop. A larger latency threshold is not an experimental arm.
7. If one source supplies nearly every hit and removing the others preserves both recall
   and NDCG, the roadmap's skip condition fires and the single source remains the design.

## Alternatives considered

### Pick one retriever

This is operationally strongest: one encoder, index, score space, and minimal latency.
But choosing SASRec now treats two-tower's difference as overhead before measuring overlap.
If unique positives do not survive the fixed budget and reach the top ten, this alternative
wins by the first or seventh stop rule rather than by preference.

### Cascade instead of union

A cascade saves work but cannot recover an item the first stage omitted; that retriever
becomes a hard ceiling on complementary recall. It is a latency repair only if the union
proves useful but dual retrieval misses the gate, not the correctness baseline.

### Distill the two-tower into SASRec

Distillation could preserve two-tower signal in one encoder, but adds a new loss,
temperature, teacher target, and capacity experiment. A student is justified only after
the teacher adds unique useful candidates and two encoders are the measured bottleneck.

### Train a larger SASRec instead

More capacity might improve one source without fusion, but W15 stopped that direction and
reopening it reopens O-3's cloud-GPU decision. Capacity attacks representation error;
mixing attacks coverage error. A larger SASRec is warranted only if union headroom is absent
and learning curves show underfitting—not as a substitute for the cheaper diagnostic.

## Consequences

- Warm requests load and execute two encoders and two learned indexes. Artifact manifests
  must pin both model hashes, both index hashes, fusion policy, feature order, and ranker.
- Provenance is a model input and audit fact; source names and sentinels become schema.
- The ranker's training distribution becomes inseparable from the mixer version. A source,
  allocation, or dedupe change requires a paired retrain and parity evidence.
- Popularity remains a safety fill and cold fallback, not a source allowed to crowd out
  learned candidates on warm requests.
- Genre-ILD measures topical variety, not novelty, fairness, serendipity, or business value.
- The design increases state and failure modes. A missing learned artifact must fail closed
  to the last complete bundle rather than silently serve a one-source approximation under
  a two-source manifest.

## Risks and mitigations

- **Multiplicity bias:** deduplication, a same-frame control, and source count expose it.
- **Score-scale mismatch:** cosine and model scores are not interchangeable. Fusion uses
  ranks; raw scores remain diagnostics unless separately calibrated.
- **Training/serving skew:** offline and online mixers can disagree on source order,
  exclusions, or ties. One canonical policy plus parity fixtures must cover all three.
- **False diversity:** genre metadata can reward arbitrary category spread. The paired
  relevance guardrail and coverage report prevent ILD from becoming the objective alone.
- **Latency and memory:** two resident encoders and indexes can fit but compete for CPU
  cache and workers. Measure combined resident bytes and request-shaped p99, not isolated
  model timings summed on paper.
- **Unstable attribution:** manifest hashes and overlap reports bind retriever versions.

## How we would know this decision is wrong

- The raw union has little unique-hit recall over corrected SASRec. The retrievers make
  the same errors, so mixing adds cost without candidate diversity.
- Raw-union recall rises but budgeted recall@500 does not. Equal-weight reciprocal-rank
  fusion is the wrong allocator, and ranker results built on it cannot judge mixing broadly.
- Budgeted recall rises while warm NDCG@10 does not. The added positives are not rankable
  from available point-in-time features, repeating the gap observed before Rung 3.
- The source-aware control ablation is flat or source rank overwhelms all semantic
  features. The ranker is learning model identity, not relevance.
- Genre-ILD improves only by consuming warm NDCG or by exploiting missing genres. The
  named diversity metric is then not aligned with the product objective.
- Combined retrieval misses latency despite each model passing alone. The architecture,
  not either encoder, is outside the serving budget.
- Rolling-window or parity evidence reverses the result. Complementarity was specific to
  one temporal split or came from offline/online skew.

## References

- Carbonell and Goldstein, *The Use of MMR, Diversity-Based Reranking for Reordering
  Documents and Producing Summaries*, SIGIR 1998.
- Cormack, Clarke, and Buettcher, *Reciprocal Rank Fusion Outperforms Condorcet and
  Individual Rank Learning Methods*, SIGIR 2009.
- Kulesza and Taskar, *Determinantal Point Processes for Machine Learning*, 2012.
- [ADR 0001](0001-evaluation-protocol.md) — learned-route promotion scope and tolerances.
- [ADR 0004](0004-item-item-before-two-tower.md) — candidate-stage retrieval gate.
- [ADR 0016](0016-sasrec-sequential-retrieval.md) — corrected SASRec and serving contract.
- [ADR 0018](0018-sequence-aware-ranking.md) — the scalar self-score result motivating
  candidate diversity.
