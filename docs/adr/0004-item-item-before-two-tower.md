# ADR 0004 — Candidate-Stage Progression: Item-Item Before Two-Tower

**Status:** Accepted
**Date:** 2026-06-01

## Context

[ADR 0003](0003-two-stage-architecture.md) pinned the two-stage architecture but stopped short of *how* the candidate stage gets built. [CLAUDE.md](../../CLAUDE.md) names two candidate-stage implementations for Phase 2 — item-item similarity and a two-tower neural model — and lists "item-item before two-tower" as one of the decisions deserving its own ADR. This document fills that slot, pinning the order and the rationale before the Phase 2 candidate-stage code arrives.

The choice is not *which* model to ship — both ship — but **which comes first** and **why a learned model gets a classical baseline in front of it.** Same question Phase 1 already answered for the recommender as a whole (popularity before CF, [ADR 0002](0002-implicit-feedback-label.md)); the candidate stage gets the same discipline applied locally.

## Decision

**Item-item similarity ships first; two-tower ships second.**

- **Item-item ([`src/models/candidates/itemitem.py`](../../src/models/candidates/itemitem.py))** — precomputed item-item cosine-similarity index over the implicit-feedback interaction matrix. No learned parameters. For a query user, retrieve top-N items by aggregating similarity scores against the user's history. Logged into a new `phase-2-candidates` MLflow experiment, evaluated through the same harness as Phase 1 (recall-oriented at K_candidates, not NDCG@10 — the candidate-stage metric per ADR 0001).
- **Two-tower (PyTorch)** — learned user and item embeddings trained with in-batch negative sampling; an ANN index (FAISS or hnswlib, decided in its own follow-up ADR) over the item tower for retrieval. Logged into the same `phase-2-candidates` experiment so item-item, two-tower, and the Phase 1 baselines (popularity, CF) sit side by side on the same recall axis.

Item-item is **not** thrown away once two-tower lands. Both remain valid candidate generators; the two-tower has to beat item-item's recall@K_candidates on holdout — through ADR 0003's per-stage promotion gate — to graduate from "second option" to "champion."

## Rationale

1. **A learned model needs a baseline to beat or it has no recall floor.** Two-tower recall numbers in isolation are meaningless — "recall@500 = 0.41" reads as a real number but tells you nothing about whether the embeddings are doing work. Item-item, run on the same data through the same harness, gives the two-tower a number to clear. This is the same logic ADR 0002 / PR #14 applied at the recommender level (popularity before CF); applying it locally to the candidate stage is non-negotiable for the same reason.

2. **Item-item has zero learned parameters, which makes the delta interpretable.** Item-item's output is a deterministic function of the interaction matrix and the cosine/Jaccard formula. When two-tower wins by X points of recall, that X is *exactly* what learned embeddings buy us over co-occurrence — not what learned embeddings buy us over a worse-tuned variant of something already learnable. Eliminating one source of variance per step is how the lessons compound.

3. **Item-item exposes the candidate-stage *infra* pattern at low engineering cost.** Both candidate generators have to solve the same operational problem: precompute an item-keyed retrieval index offline, persist it, load it in the training pipeline, and structure it so Phase 3's serving layer can lift it into a Redis-backed online lookup without refactoring. Item-item's index is simply a top-K sparse matrix (item × top-N items by similarity). Two-tower's index is a dense embedding table plus an ANN structure. Building the simple index first means the *index-loading and retrieval contract* (what does `candidates.retrieve(user_id, n) -> list[int]` look like?) gets stress-tested by a model whose internals are easy to debug, and the two-tower drops into a known shape rather than co-designing infra and embeddings at the same time.

4. **Co-occurrence math is the muscle group a candidate-stage engineer is expected to have.** Item-item forces explicit engagement with the choices a learned model would make implicitly: sparsity handling (an item with 6 ratings is the median per [`docs/eda.md`](../eda.md) section 4; co-occurrence with rare items is noisy), normalization (raw counts vs cosine vs Jaccard vs shrinkage), the long-tail problem (popular items dominate co-occurrence without normalization), and the asymmetric vs symmetric similarity question. A senior ML engineer is expected to defend these choices independent of any framework. Building item-item first is how that muscle gets earned.

5. **Phase 2's lesson load is bounded.** Per CLAUDE.md, Phase 2 is meant to teach "two-stage design, feature engineering at scale, stage-specific metrics, why one model can't do both jobs well." Two-tower + ranker + features + per-stage evaluation is already a substantial new-concept queue. Inserting item-item between Phase 1's baselines and the two-tower upgrade smooths the gradient — the candidate-stage *infrastructure* concepts (offline index, retrieval contract, per-stage metric) land first against a familiar modeling pattern, and then the *embedding-learning* concepts land against a familiar infrastructure pattern. Doing both at once is a known way to learn neither well.

## Alternatives considered

- **Skip item-item; go straight to two-tower.** The most tempting shortcut. Rejected on rationale #1 — the two-tower's recall numbers are uninterpretable without a baseline at the same evaluation point, and the project's promotion-gate discipline (non-negotiable #7) demands a defined threshold the challenger has to clear. A baseline that doesn't exist can't be cleared.
- **Item-item only; no two-tower in Phase 2.** Defensible on operational simplicity grounds (no embedding model to train, no ANN infrastructure). Rejected because the two-tower is one of the named modeling pieces CLAUDE.md commits to building. The point of the project is to confront the technologies a mid-to-senior ML engineer deals with; learned candidate generation is one of those, and deferring it to a phase that may never come is the wrong trade.
- **User-user CF as the classical candidate baseline instead of item-item.** Same family of techniques, similar interpretability story. Rejected because user-user similarity is computed per (user, user) pair (N_users² in the worst case — 162 k users in MovieLens 25M), which doesn't precompute cleanly into a per-item index. Item-item is N_items² in the worst case (~62 k items, fits in memory) and matches the candidate-stage *retrieval shape* (look up items, not users) that the serving layer wants in Phase 3. The classical baseline should rehearse the production retrieval pattern, not depart from it.
- **A learned-but-shallow baseline (matrix factorization on co-occurrence, e.g. SVD on the item-item matrix).** Halfway house — learned, but cheap and explainable. Considered. Rejected because Phase 1's CF/ALS baseline ([PR #14](https://github.com/kudratsingh/MovieLens-RecSys/pull/14)) already covers "learned matrix factorization" and would be the natural thing to compare against. Adding SVD-on-cooccurrence as a Phase 2 candidate generator is a possible follow-up but isn't on the critical path; pure item-item gives the baseline its zero-learned-parameters property.
- **Defer the ADR until item-item is built.** Rejected by the same discipline that wrote ADR 0001 before the eval module and ADR 0003 before any Phase 2 code: pin the choice in writing first, build to it after. The point of an ADR is that the alternatives get written down while they're still live alternatives, not after the implementation has made them moot.

## Consequences

- **Code layout.** `src/models/candidates/itemitem.py` lands first under the same `CandidateModel`-shaped contract as `PopularityModel` and `CFModel` — `fit(train)` and `recommend(user_id, k)` / `recommend_for_users(user_ids, k)`. Two-tower lands second under the same contract; the orchestration layer in Phase 3 treats them interchangeably.
- **Training pipelines.** `src/training/itemitem.py` mirrors the Phase 1 shape (`load → temporal_split → fit → recommend → evaluate → log`). The pattern stays uniform — every new model is a swap of the model class and a few hyperparameter names, nothing else.
- **MLflow.** A new experiment, `phase-2-candidates`, holds item-item, two-tower, and (for direct comparability) re-runs of popularity and CF at the same K_candidates the new models are being evaluated at. The Phase 1 experiment `phase-1-baselines` is preserved unchanged for historical reference.
- **Metric.** Per ADR 0001, the candidate stage is scored on recall — but the relevant K is **K_candidates** (~500 candidates), not the K=10 the recommender end-to-end uses. The eval harness needs a small extension to accept a configurable K and report `recall_at_k_candidates`; this comes as part of item-item's PR, not as a separate ADR — it's an extension of the protocol, not a change to it.
- **Promotion gate.** The two-tower has to beat item-item's recall@K_candidates on holdout by a defined threshold to be promoted to champion candidate generator. The threshold itself is decided when the two-tower lands (it depends on item-item's actual number); ADR 0003 already established that per-stage promotion gates are stage-local, and ADR 0001 already established the comparison protocol.
- **Cold-start.** Item-item has the same cold-user problem as CF — a user with zero training history has no co-occurrence signal. The popularity fallback established in ADR 0001 / PR #14 lifts unchanged: cold users get popularity-stage candidates regardless of which candidate model is champion. The item-item module embeds the fallback the same way `CFModel` does; the two-tower will too.
- **Deferred to future ADRs in this lineage.** The two-tower's embedding dimension, negative-sampling strategy, and ANN library choice each touch their own tradeoffs and earn their own ADRs when the two-tower lands. ADR 0005 (LightGBM over neural ranker) is the next in the Phase 2 queue, independent of this decision.

## 2026-08-30 — measurement note: the comparison this ADR set up, run on the full dataset

Status stays **Accepted**; nothing above is retracted, and the decision this ADR
took — item-item first — is unchanged either way. What follows is the number the
Consequences section deferred, and what it settles.

### What was deferred, and what it now reads

The **Promotion gate** bullet above says the two-tower "has to beat item-item's
recall@K_candidates on holdout by a defined threshold", and leaves the threshold
open because "it depends on item-item's actual number". Both models have now been
run to completion on MovieLens 25M through the same harness, same split, same
seed, and the numbers are in [`results.md`](../results.md)'s 2026-08-30 section.

| Warm slice, 1,939 users, K_CANDIDATES = 500 | Item-item | Two-tower | Relative |
|---|---:|---:|---:|
| recall@500 | **0.400144** | 0.046581 | **−88.36%** |
| NDCG@500 | **0.139240** | 0.014575 | **−89.53%** |

MLflow runs `ab1fe49dc21e4c07abc15775fd0cd12d` (item-item, 19.7 s fit) and
`5628ab0b24c448a78c6f93440e6360b1` (two-tower, 4,687.9 s fit, 3 epochs over
19,867,692 (history, positive) pairs), both in `phase-2-candidates`, both routing
on index membership so the model is the only difference.

**The threshold never had to be pinned to settle this.** The challenger reaches
11.6% of the incumbent's warm recall@500 — it does not clear zero, let alone
ADR 0001's house rule of +3% relative. **Item-item remains the champion candidate
generator.** For calibration: drawing 500 of the 34,461 train items uniformly at
random gives an expected recall@500 of 0.014509, so the two-tower is at 3.21×
random and item-item at 27.6×.

Pinning the threshold's exact value is still owed and is deliberately not done
here — this comparison did not need it, and a number chosen to fit a result it
cannot change is worth less than one chosen on its own terms. Under ADR 0001's
+3% relative rule it would be **warm recall@500 ≥ 0.412148**, which is the figure
a future challenger should expect to be held to.

### What the result is, and is not, evidence for

The loss curve is the part worth recording. Mean sampled-softmax loss by epoch was
**10.3542 → 10.2726 → 10.2718** — the second epoch bought 0.08 and the third
bought 0.0008. The model converged, in the sense that it stopped moving, but it
converged somewhere that retrieves barely better than chance. Three epochs at
`lr=1e-3` over ~14,600 steps with 16,384 sampled negatives is a thin training
budget, and it was chosen in [ADR 0006](0006-two-tower-retrieval-architecture.md)
before anyone had run it at this scale.

So this is a measurement of **two-tower v1 as configured**, not a finding about
learned retrieval versus co-occurrence. Reading it as the latter would be exactly
the mistake rationale #1 of this ADR was written to prevent, run backwards.

What it *is* evidence for is that rationale #1 was right. A warm recall@500 of
0.0466 reported on its own would have read as a real number — as that rationale
put it, "'recall@500 = 0.41' reads as a real number but tells you nothing" — and
only the zero-parameter baseline sitting beside it makes it legible as a failure.
The baseline earned its keep on its first real use.

### Consequences

- **Item-item stays champion.** The served bundle (`infra/model-bundle/`) is
  unchanged; it was never going to change on this result, but it is worth saying
  that nothing was promoted.
- **`docs/modeling-roadmap.md`'s Rung 1 is not skippable on the stated
  condition.** Its skip clause is "the full-dataset two-tower v1 already clears
  ADR 0004's threshold". It does not. Whether Rung 1 (hard negatives, item side
  features) or a plain training-budget sweep is the right next move on the
  two-tower is a rung decision for the owner, and the roadmap's approval gate
  applies.
- **The cheapest next experiment is not on the ladder at all.** Before any
  architectural change, the two-tower deserves a run with more epochs and a
  learning-rate sweep, because a loss that flattens after one epoch is a
  hyperparameter symptom before it is a model one. That is a re-measurement of
  this ADR's own comparison, not a new rung.

## 2026-09-05 — the three-seed requirement is suspended

The owner set a standing experiment-cost policy: **one run per configuration**, with no repeated runs
for seed confirmation, until the modeling ladder reaches modern advanced transformer-based models. A
full-data seed is about 4.5 hours and a three-seed set about 13.5, and the priority is reaching
advanced architectures rather than re-confirming a result already believed.

Clause 1 and clause 5 of the amendment below therefore do not currently apply. Everything else in it
stands — the protocol identity, the population equality checks, the four decision states, and the
rule that a retrieval pass is not permission to serve.

This leaves a live inconsistency, recorded rather than papered over: `src/evaluation/retrieval_gate.py`
requires seeds 42, 7 and 13 and returns `incomplete` on a partial set, so **under this policy the gate
cannot issue a verdict at all**. Two ways out, and the choice is the owner's: relax the gate's required
seed set to one and replace the across-seed dispersion term with a user-level bootstrap over the single
run, or leave the gate as written and accept that retrieval promotion stays a manual judgement until
the policy changes. Until one is chosen, a single-seed result is evidence and not a promotion.

## 2026-09-04 — amendment: protocol-bound retrieval promotion

Status remains **Accepted**. The owner approved the retrieval decision rule that the original
ADR deferred. It applies to SASRec and later stochastic learned retrievers whose job is to replace
the deterministic item-item candidate generator.

### Decision

A learned retriever clears the retrieval-quality gate only when all of the following are true:

1. Candidate runs use the complete seed set `42`, `7`, and `13`; the item-item incumbent is one
   deterministic, seedless run.
2. Every run is `FINISHED`, identifies its model type, and contains a complete canonical evaluation
   protocol whose recorded hash can be independently recalculated.
3. Candidate and incumbent protocols match exactly, including data/snapshot identity, time window,
   labels, eligible population, catalog, routing, exclusions, feature semantics, stage, metric,
   slices, and K.
4. Both sides evaluate the same warm and cold user populations at retrieval-stage recall@500.
5. Mean candidate warm recall@500 is at least `incumbent × 1.03`.
6. Mean candidate cold and overall recall@500 do not regress beyond separately measured
   retrieval-specific tolerances.

Cold and overall tolerances have no default. Until the retrieval noise study records both measured
fractions, the gate is incomplete and cannot promote. ADR 0001's ranking tolerances must not be
reused: those describe NDCG@10 variation in a different stage and model pipeline.

The gate returns four distinct states: `promote`, `refuse`, `not_comparable`, and `incomplete`.
Missing seeds or metadata are incomplete evidence, protocol/population mismatches are not
comparable, and a valid result below a quality threshold is a refusal. None may be collapsed into a
pass or into an ordinary model loss.

A retrieval `promote` is stage-local, not permission to serve. The exact candidate sets must also
pass the paired champion LightGBM NDCG@10 gate under ADR 0001, followed by artifact-equivalence and
latency gates. The sealed test partition remains untouched until a release candidate is frozen.

### Implementation and historical results

The executable contract is split deliberately:

- `src/evaluation/retrieval_gate.py` reads recall@500 and enforces this amendment.
- `src/evaluation/gate.py` remains the ranking-only NDCG gate and is unchanged.
- `src/evaluation/manifest.py` owns canonical protocol serialization and semantic hashing.

The earlier illustrative `0.412148` warm floor came from applying 3% to the historical item-item
score `0.400144`. It is not a constant. Every decision calculates its floor from the compatible
incumbent run supplied to the gate. Historical runs that predate the canonical manifest remain
useful evidence but are intentionally ineligible for an executable promotion verdict.
