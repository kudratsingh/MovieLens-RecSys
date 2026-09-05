# ADR 0018 — A ranker that reads the sequence (Rung 3)

**Status:** Accepted
**Date:** 2026-09-05

## Decision — 2026-09-05

Approved by the owner 2026-09-05. Roadmap Rung 3's decision-log row reads *approved* against
this ADR, and increment 1 is authorised to build.

One amendment is recorded with the approval, and it tightens stop rule 3:

> **Increment 2 (DIN) is not built unless increment 1 gains at least 3% warm NDCG@10 over the
> per-route bundle AND the owner names a warm NDCG@10 target after increment 1's number
> exists.**

Both conditions are necessary. The first is this ADR's own stop rule 1 read as a precondition
rather than only as a stopping condition; the second is new, and exists because the band the
proposal identifies — +3% to +9.6% warm, where the warm-primary and overall readings of ADR
0001 disagree — is a judgement the owner takes with the measurement in hand, not one a
proposal can pre-commit. A passing increment 1 therefore does not start increment 2 by
momentum: it starts a conversation whose output is a number written down before any DIN code
exists.

The rest of this document is the proposal as written on 2026-09-05 and is not rewritten.

## Context

Three measurements taken between 2026-09-04 and today define the gap this rung exists
to close.

**SASRec retrieves better.** The artifact-backed full-data run
(`a11af5ed0f0745f68572407237cfa4b9`, model SHA-256 `43320b87…`) reaches warm recall@500
**+16.57%** over the protocol-compatible item-item incumbent, cold exactly 0.00% because
both route below-threshold users to the identical popularity fallback, overall +11.16%.
Its isolated encoder p99 is **0.285 ms** against ADR 0016's 15 ms budget.

**The champion ranker could not use that.** Swapping only the candidate source under the
*fixed* booster moved warm NDCG@10 **+1.67%** and overall +0.43%, while warm recall@10
moved **+30.70%**. The positives were arriving in the slate and the ranker was not lifting
them into the top ten, because it had been fitted on item-item's candidate distribution and
carries no sequence signal.

**Retraining recovers the warm gain and breaks the cold slate.** PR #151's paired arms
(seed 42, identical positives, #126 exclusions, one protocol hash) measured a booster
retrained on SASRec candidates at warm NDCG@10 **+25.96%** (0.072792 → 0.091688) and warm
recall@10 +60.50% — fifteen times the fixed-ranker figure — but cold **−53.11%** and
overall −32.15%, which the gate refuses. The cause is the training mix, not retrieval:
every arm holds the same fitted fallback object, so cold candidates are byte-identical
across all four runs. The incumbent's importances are dominated by `item_popularity_30d`
at 260,507, the rule that orders a popularity slate well; the challenger's fall to 64,954
and go flat, because popularity discriminates poorly in SASRec's deep-catalog mix. One
booster trained on one candidate distribution cannot serve two routes. Two repairs clear
ADR 0001: the **per-route bundle** (`566f5309767a4076a4f5e8151be16645`, zero new boosters,
warm +25.96% / cold 0.00% / overall **+6.88%**) and a **union booster** (+26.69% / −1.05%
/ +6.30%).

What none of that changes is the ranker's inputs. It still sees the eight aggregate
columns in `src/feature_contract.py` — three user counters, three popularity windows, item
age, a genre affinity. Every one is a summary of the history, and none can tell the ranker
*which* past title makes this candidate plausible, which is the question a top-ten ordering
turns on and the question the roadmap wrote Rung 3 to answer. The warm gain in PR #151 was
bought by the retriever; the ranker's own contribution to warm ordering on that slate is so
far unmeasured.

## Proposal

Two increments, the second conditional on a named gap left by the first. The encoder is
**frozen** throughout: artifact `a11af5ed…` alone, SHA-checked at load. Fine-tuning it is
a separate decision, because it makes retriever and ranker co-dependent and would
invalidate the retrieval verdict just established.

### Increment 1 — the SASRec representation as point-in-time LightGBM features

Add to the warm-route feature contract, in this order after the existing eight:

| Feature | Definition | Arm |
|---|---|---|
| `sasrec_user_item_score` | Inner product of the L2-normalised user vector from `encode_movie_history` and the L2-normalised candidate item embedding. Because both sides are normalised at the retrieval boundary this **is** the cosine, and it is exactly the score FAISS ranked on. | required |
| `sasrec_user_item_logit` | Inner product of the *unnormalised* representation (`training_user_vectors`) and the unnormalised item embedding — the quantity the BCE objective actually calibrated, carrying the magnitude the normalised score discards. | required |
| `sasrec_candidate_rank` | The candidate's 1-based position in SASRec's own top-500, with an explicit sentinel for candidates it did not retrieve. | ablated |
| `sasrec_last_item_similarity` | Cosine between the candidate embedding and the embedding of the most recent history item. This is the cheap recency baseline run as an arm rather than as an argument. | ablated |
| `sasrec_prefix_length` | Length of the strict prefix actually encoded, capped at 50. Context and missingness, never a relevance proxy. | ablated |

The raw 64-d vector is deliberately **not** exposed as 64 columns: a GBDT splits
axis-aligned on dimensions of a latent space whose axes carry no stable meaning, and an
encoder retrain would silently reinterpret all sixty-four. The scalar interactions are the
part that survives an artifact version.

**Point-in-time contract.** The encoder query is the user's positive interactions with
`timestamp < as_of`, ordered by `(timestamp, movie_id)`, with equal-timestamp events
excluded from one another's context — the same `searchsorted(…, "left")` cut the exclusion
path already takes, so history and exclusions come from one slice and cannot drift apart. A
positive whose strict prefix is empty takes the missing sentinel and the route the fallback
already gives it; step 1 measured 857 such positives, 0.56% of the sample.

**Parity contract.** The feature-parity test (non-negotiable #2) extends to the embedding
features. Exact equality is not available: the offline path batches encodes, the online
path encodes one sequence, and float32 matmul is not associative. Proposed tolerances are
**1e-5 absolute** on `sasrec_user_item_score` (bounded in [-1, 1]), **1e-4 relative** on
`sasrec_user_item_logit`, **exact** on the two integer columns. The test asserts on the
artifact **SHA-256 and the history slice** as well as the values, so no tolerance can paper
over a different model or a different prefix.

**Storage: computed, not stored.** No 64-d user vector goes into Redis. It is a function of
the ordered history the sidecar already reads for routing and exclusions (ADR 0016's serving
contract), the sidecar already encodes that history to retrieve at 0.285 ms p99, and the
item embeddings are already resident in the loaded artifact; the added work is one 500×64
matmul. Materialising it would buy nothing and cost a second source of truth with its own
freshness clock, since a user's vector goes stale the moment they watch something while
online feature views materialise on a batch cadence. Feast's role is unchanged, and the
split is principled: a feature view is for values expensive to recompute and shared across
requests, and this vector is neither.

**Manifest.** Schema version 2 pins the encoder SHA-256 alongside the ranker feature
order, per route, and fails closed on a mismatch exactly as `MANIFEST_SCHEMA_VERSION`
already does for the columns.

### Increment 2 — DIN target attention, only on a named gap

If and only if increment 1 leaves a *named* gap: DIN-style attention from the candidate
over the last 50 history items (Zhou et al., 2018), living in the sidecar beside LightGBM
and scoring the warm route only. It enters first as a scorer whose output is one more
LightGBM feature, and may replace LightGBM on the warm route only by beating
LightGBM-plus-sequence — not the eight-feature model — on the gate. If its score ever
leaves the ranker, it carries a monotonic calibration layer.

**Latency.** The arithmetic is not the risk — 500 candidates × 50 positions × 64 dims is
1.6M multiply-adds plus a small MLP — per-request Python and PyTorch overhead is. The
proposed **isolated ranker budget is p99 < 10 ms** for 500 candidates, measured the way
ADR 0016 measured the encoder (single thread, request-shaped, after warmup, same machine)
and taken from headroom outside the encoder's 15 ms, never from it. Ten milliseconds is a
twentyfold increase over the tree scorer's ~500 µs and is the largest ask that leaves the
unchanged 100 ms service p99 comfortable. If DIN misses it the answer is not a bigger
budget: it is a two-tier ranker where LightGBM orders 500 and DIN rescores the top 50,
measured as its own arm rather than adopted by drift. TransAct (Xia et al., 2023) is not
proposed here and becomes a question only if DIN establishes that candidate-to-history
attention is worth its cost.

### How it is judged

Through `src/evaluation/` on ADR 0001's gate, NDCG@10, against the **per-route SASRec
bundle from PR #151** (`566f5309…`: warm 0.091688 / cold 0.549002 / overall 0.214631) as
the incumbent. Not against item-item plus LightGBM — that comparison would let increment 1
bank SASRec's retrieval gain a second time and call it a ranker result. Because the
per-route bundle leaves the fallback booster untouched, the cold slice is bit-identical by
construction and the whole claim is the warm slice plus the arithmetic it implies.

**O-1 is open, and the reading changes the bar.** Under a *warm-primary* answer the
requirement is +3% warm with a cold non-regression clause satisfied by construction, and
stop rule 1 below is the gate. Under the *overall* reading the arithmetic bites: with cold
frozen at 0.549002, the 1,931 warm users carry only **31.2%** of the bundle's aggregate
NDCG mass, so moving overall +3% requires moving warm **+9.6%**. A result between +3% and
+9.6% warm would clear the stop rule and fail the gate — measured, not promoted — and that
band is exactly where the decision about increment 2 would be taken. This ADR does not ask
for O-1 to be settled first, only that whichever reading is in force be recorded on the run.

**One run per configuration**, per the owner's replication-budget decision on ADR 0016 and
O-5. Seed 42, the `prepare_shared()` prologue, #126 exclusions, one protocol hash across
arms, so the only thing differing between incumbent and arm is the feature set.

### Stop rules

1. Increment 1 adds **< +3% warm NDCG@10** over the per-route bundle: record it and stop
   increment 1. That is also a stop for Rung 3 *unless* a diagnostic names what is
   missing — a small-but-real lift whose residual errors are explained by a specific
   history item rather than by the aggregate profile is a named gap; a lift the
   frozen-random-encoder control also reproduces is not, and closes the rung.
2. Increment 1 **clears the gate**: promote the simpler model and do **not** build DIN.
   That is the roadmap's own skip condition for this rung and the phase plan's H1 stop.
3. Increment 2 is not started without a gap named under rule 1 and written down before
   any code.
4. **Any latency breach is a stop, not a threshold change.** The 15 ms encoder budget and
   the 100 ms service p99 do not move for a model.

## Alternatives considered

**(a) Keep the eight aggregate features and ship the per-route bundle.** Honest and cheap:
it clears ADR 0001 at +6.88% overall with zero new training, and it is what happens if
Rung 3 is skipped. What it costs is knowing whether the ranker is doing anything. The
+25.96% warm gain came from the retriever, and the challenger's importances went *flat* on
the SASRec mix — the booster reporting it has nothing informative to lean on in that
candidate distribution. Shipping this ships a ranker demoted to a mild reordering of a good
retrieval list. And the roadmap's own skip test for Rung 3 is "adding the sequence embedding
as a GBDT feature already recovers the warm NDCG", which cannot be applied without running
increment 1: skipping here means skipping the measurement that decides the skip.

**(b) Recency-weighted aggregates instead of embeddings.** Decayed genre affinity, decayed
popularity, time since the last item in each genre. No artifact coupling, no parity risk,
trivially explainable, and they answer a real question. They cannot answer *this* one:
which past title makes this candidate plausible. ADR 0016 rejected the same substitution at
the retrieval stage for the same reason. This alternative is not discarded — it is run,
as `sasrec_last_item_similarity`, so the comparison happens inside the ablation rather than
in an argument. If that single feature carries most of the lift, this alternative was right
and the encoder is an expensive way to compute recency; that is one of the falsification
conditions below.

**(c) Neural ranker first, skipping increment 1.** A real argument: a GBDT over two scalars
discards the 64-d structure DIN would consume directly, and DIN's own paper starts where
increment 2 does. Rejected on three grounds. The cost asymmetry is large — increment 1 is a
feature-contract change plus a retrain step 1 showed costs eleven minutes, while DIN is a
new model family, artifact type, serving path and latency gate. The program guardrails
require neural work to be justified by the previous stage's *measured* failure mode, and
none has been measured. And without increment 1 there is no way to attribute a DIN win to
attention rather than to the ranker finally seeing the encoder at all: increment 1 is the
control increment 2 needs.

**(d) Joint retriever/ranker training.** The theoretically right answer: one objective, so
the retriever produces candidates the ranker can order. It is rejected because it dissolves
the stage-local verdicts this project's evaluation protocol is built on — ADR 0004's
recall@500 gate and ADR 0001's NDCG@10 gate stop being separable, and the +16.57% retrieval
result established this week would no longer mean anything on its own. It also makes every
retrieval change a ranker retrain, and it is a much larger compute ask on a CPU laptop with
O-3 still open. This is a Rung 7 conversation.

**(e) BERT4Rec-derived embeddings.** Stronger as a *feature* proposal than as a retrieval
one: the usual objection to BERT4Rec — that serving has only a left context — does not
apply at ranking time, where the candidate is known and the history is fixed, so a
bidirectional encoder could yield a better representation for exactly this use. It is not
proposed now because it means training a second sequence model before the first has been
shown to help the ranker at all, and gSASRec reports that a well-trained SASRec is the
stronger baseline anyway. If increment 1 works and increment 2's attention does not, a
bidirectional encoder becomes the interesting question, arriving with a reason.

## Consequences

- **The ranker becomes coupled to a specific retriever artifact.** Manifest v2 pins the
  encoder SHA beside the feature order and fails closed. The second-order effect is the
  one that matters: a SASRec retrain stops being a retrieval-only change and forces a
  paired ranker retrain. Phase 4's DAG must encode that dependency order — encoder first,
  ranker second, never the reverse — because a ranker fitted against a new encoder while
  the old one still serves is exactly the skew the parity test exists to catch.
- **Per-route rankers mean the fallback booster never sees these features, and that is
  correct.** A user below the threshold routes to popularity precisely because their history
  is too thin to encode; a 0–9 item prefix is a sequence the model never trained on, and
  step 1 measured what a booster trained on the wrong candidate distribution does to that
  slate. The fallback route stays bit-identical and the cold slice stays constant across the
  rung. Second-order: the two boosters now differ in their *feature contract*, not only
  their weights, so the sidecar must select the route before assembling a feature frame.
  Right model, wrong columns is a new failure class and needs a startup assertion.
- **Retraining cadence diverges.** The ranker retrains on a 30-day trailing window; the
  encoder does not retrain at all today. The rung introduces a version relationship between
  two artifacts on different clocks, and that has to be a recorded fact per run.
- **Skew risk in the embedding path is sharper than in the aggregate path.** An aggregate
  feature can be recomputed from Postgres and compared; an embedding feature reproduces only
  if artifact bytes, history slice, ordering rule and padding all match. Hence a parity
  contract that asserts on the SHA and the slice, not just the value.
- **Missingness becomes a signal.** The 857 empty-prefix positives and the 7,092
  learned-path positives whose prefix held fewer than ten items are invisible to the ranker
  today. After increment 1 they carry an explicit sentinel the GBDT will split on, so "no
  encodable history" becomes usable information whose effect must be reported, not absorbed.
- **Phase 4's explainability panel gets slightly less honest.** ADR 0005 sold LightGBM
  partly on SHAP for "why this recommendation?", and `sasrec_user_item_score` is a valid
  SHAP input that explains nothing to a person. The mitigation is increment 2's attention
  weights, which are at least item-level, or a nearest-history-item attribution from the
  same embeddings. Naming it now beats discovering it in Phase 4.

## Risks

- **The dot product just re-encodes the retriever's rank.** The candidate set *is*
  SASRec's top-500, so within a group the score is a monotone function of the rank by
  construction, and a listwise GBDT could learn nothing but "trust the retriever". This
  would present as a large feature gain and a small NDCG gain. Mitigation: run with and
  without `sasrec_candidate_rank`, plus a control arm carrying rank alone. If rank-alone
  matches, the embedding added nothing.
- **Leakage through a post-`as_of` history.** The highest-severity failure, and the one
  that would inflate every number. Mitigation: one function builds the query slice, shared
  with the exclusion path, and a canary asserts that the sequence encoded at time *t*
  equals the sequence built from the same fixture truncated to `< t`, equal timestamps
  included in the check.
- **Frozen-encoder staleness.** The encoder is trained on the train split, is stale at
  holdout by construction, and would be staler in production. Mitigation: report the
  feature's lift against encoder age in the rolling-window backtest.
- **Learning only where the retriever was right.** A positive SASRec misses drops its
  group; step 1 lost 4.8% more groups on SASRec than on item-item. The sequence features
  are therefore fitted on the subset the retriever already handles.
- **Compute.** 154,003 encodes at ~0.26 ms is under a minute; the real cost is candidate
  embedding assembly. Cap: one run per arm, and if training-set assembly exceeds roughly
  twice step 1's eleven minutes, batch the encodes rather than extend the budget.

## How we would know this decision is wrong

- Warm NDCG@10 moves **< +3%** over the per-route bundle. Sequence *scores* then add
  nothing the aggregates did not, and Rung 3's premise fails at its cheapest increment.
- The **rank-only control matches** the embedding arm. The feature is the retriever's
  opinion re-served and the GBDT learned to defer, not to rank.
- **`sasrec_last_item_similarity` alone carries the lift.** Alternative (b) was right and
  the encoder is an expensive recency feature.
- A **deterministically shuffled prefix** reproduces the lift, mirroring ADR 0016's own
  falsification test. Order was not the source.
- The **parity tolerance cannot hold**. The embedding path is then not reproducible across
  the offline/online boundary, and no offline number computed from it can be trusted.
- The lift **disappears on rolling windows**, or survives only with a feature that turns
  out to read the future.
- Increment 1 **clears the gate**. Then increment 2 is wrong to build, and the second half
  of this ADR should be marked skipped with that reason rather than climbed out of momentum.
- DIN cannot hold **p99 < 10 ms** isolated at 500 candidates and the two-tier fallback also
  misses. Increment 2 closes on latency, and the budget does not move to save it.

## References

- Zhou et al., *Deep Interest Network for Click-Through Rate Prediction*, KDD 2018.
- Xia et al., *TransAct: Transformer-based Realtime User Action Model for Recommendation at
  Pinterest*, KDD 2023.
- Kang and McAuley, *Self-Attentive Sequential Recommendation*, ICDM 2018.
- Petrov and Macdonald, *gSASRec*, RecSys 2023.
- [ADR 0001](0001-evaluation-protocol.md) — the gate, its slices, and the measured tolerances.
- [ADR 0005](0005-lightgbm-over-neural-ranker.md) — why the ranker is a GBDT, and the
  explainability commitment this rung touches.
- [ADR 0016](0016-sasrec-sequential-retrieval.md) — the encoder, its artifact, its serving
  contract, and the retrieval verdict this rung builds on.
- [`docs/results.md`](../results.md) — the fixed-ranker D-002 guardrail and the four-arm
  paired measurement (PR #151) this proposal reads as its incumbent.
- [`docs/model-planning/phases/04-sequence-aware-ranking.md`](../model-planning/phases/04-sequence-aware-ranking.md)
  — the hypothesis ladder and stage plan; [`01-program-guardrails.md`](../model-planning/01-program-guardrails.md)
  — the evidence, approval, and serving-eligibility rules this proposal is bound by.
