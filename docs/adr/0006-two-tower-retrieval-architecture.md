# ADR 0006 — Two-Tower Retrieval Architecture

**Status:** Accepted
**Date:** 2026-07-02

## Context

[ADR 0003](0003-two-stage-architecture.md) pinned the two-stage architecture. [ADR 0004](0004-item-item-before-two-tower.md) pinned item-item as the classical baseline the learned candidate generator has to beat. This ADR fills the remaining slot in the candidate-stage lineage: the shape of the two-tower model itself — the encoders, the loss, the retrieval index, and the cold-start path.

The two-tower architecture has enough degrees of freedom that "we're building a two-tower" is not a decision — it's a family of decisions. The choices that matter, and that this ADR pins:

1. **What each tower encodes.** User side: a per-user embedding, or a function of the user's history?
2. **Embedding dimension.** 32, 64, 128 — the standard axis-of-adjustment, but the default shapes memory, index build time, and downstream ranker cost.
3. **Negative sampling.** In-batch negatives only, in-batch with popularity correction, full sampled softmax, or hard negatives.
4. **ANN library and index type.** FAISS vs hnswlib; IVF-Flat vs HNSW vs Flat.
5. **Cold-start path.** What happens when a user has zero history at query time.
6. **Point-in-time correctness in training.** How history is constructed at each training example so the model never sees the future.

Getting any of these wrong shows up either as bad recall (which the ADR 0004 gate would catch) or as silently inflated recall (which it would not) — the second failure mode is the one this ADR is written to prevent.

## Decision

The two-tower candidate generator ships with the following shape:

- **User tower — history-based encoder, no per-user-id embedding.**
  - Input: the most recent `N=50` items from the user's history strictly *before* the query timestamp.
  - Encoder: look up each item's embedding from the item-tower's embedding table, mean-pool the resulting vectors, L2-normalize the output.
  - Shorter histories are used as-is (no padding to 50 required for a mean-pool); a user with zero history is routed to the popularity fallback and never reaches the tower.
- **Item tower — id-only.**
  - A `nn.Embedding(n_items, d=64)`. No side features (genre, year, popularity) in this ADR — item-side features arrive in Phase 3 alongside Feast.
  - Output L2-normalized so retrieval is by cosine similarity (equivalently, inner product on unit-normalized vectors — the shape FAISS's `IndexIVFFlat` wants).
- **Embedding dimension: 64.** Same as CF/ALS ([`src/models/candidates/cf.py`](../../src/models/candidates/cf.py)), on purpose — see rationale #2.
- **Loss: sampled softmax with log-uniform negative correction** ([Yi et al., 2019](https://research.google/pubs/pub48840/)). Negatives sampled per batch from the log-uniform distribution over item ids (ordered by frequency); each negative's logit is corrected by subtracting `log P_sampled(item)` so the gradient recovers the true softmax over the full catalog in expectation. `num_sampled = 4 * batch_size`.
- **ANN retrieval: FAISS-CPU `IndexIVFFlat`**, `nlist = 100`, `nprobe = 10`, inner-product metric over L2-normalized item embeddings. Built once per training run after the item tower is frozen; loaded at recommend time.
- **Cold-start fallback: embedded `PopularityModel`.** Same pattern `CFModel` and `ItemItemModel` use. A user with zero training interactions is routed to popularity before any tower forward pass.
- **Point-in-time correctness in training.** Every training example is a `(user_history_before_t, positive_item_at_t)` pair. History is built by sorting each user's interactions by timestamp and, for the positive at position `i`, taking positions `max(0, i-N)..i-1`. The positive is never in its own history, and no future interaction is.

Ships with:
- `src/models/candidates/twotower.py` — model + FAISS index integration.
- `src/training/twotower.py` — training loop, MLflow logging into `phase-2-candidates`.
- `tests/unit/test_twotower.py` — contract tests, a synthetic-data converges smoke test, and a point-in-time-correctness canary.
- `Makefile` — `train-twotower` target.
- `pyproject.toml` — `faiss-cpu` added (`torch` is already a dependency).

## Rationale

1. **History-based user tower, not per-user-id embedding, because the point of the two-tower is graceful handling of new-ish users.** A per-user-id embedding trains a distinct vector per user; a cold user has no such vector, so the model has to fall back to popularity or a heuristic. A history-encoder is defined for *any* user with at least one interaction — including a user who arrives after training. That directly extends the model's warm-user radius past the training snapshot, which is the operational property that makes learned retrieval worth the training cost over item-item. It also aligns with how the model will be *served* in Phase 3: the online request supplies a user id and the feature store returns the user's history; the tower runs on features, not on a per-user embedding table that would have to be materialized to Redis for hundreds of thousands of users.

2. **Embedding dim 64 matches CF/ALS on purpose.** ADR 0004 argues that item-item is the baseline the two-tower has to beat, and by extension every comparability lever should be pulled. CF/ALS also has 64 factors ([`src/models/candidates/cf.py:33`](../../src/models/candidates/cf.py)); running the two-tower at the same dimension isolates the win to *what is being learned* (a history-aware encoder + sampled-softmax objective) rather than *how many parameters got thrown at the problem*. 64 is also the largest dimension where the full 62 k-item embedding table fits comfortably in the 16 GB machine budget alongside PyTorch's autograd overhead during training, so we don't lose the ability to sweep to 128 later on a bigger box.

3. **Sampled softmax with log-uniform correction, not in-batch negatives alone, because popularity bias in the negatives is the failure mode that quietly ruins recall.** In-batch negatives sample the negative distribution *from the batch itself*, which reflects the popularity distribution of the training data — popular items appear as negatives disproportionately often. Without correction, the model learns to push popular items *down* in the ranking to reduce their loss contribution as (frequent) negatives, which is exactly wrong: popular items should surface for warm users too. Yi et al.'s log-uniform correction subtracts the negative-sampling probability from each negative's logit, so the gradient becomes an unbiased estimator of the full softmax over the catalog. This is the fix that made two-tower retrieval production-viable at Google-scale, and it's a two-line change once the sampler is in place, so there's no reason to ship the biased version first.

4. **FAISS-CPU IVF-Flat, not hnswlib or exact search.** Three-way trade: exact search over 62 k items is `O(n_items)` per query — fine for offline eval on 2 641 holdout users (~165 million dot products, seconds), but the wrong shape to ship because it won't hold when the catalog or the query rate grows. HNSW gives excellent recall/latency but the graph structure adds tuning surface (M, efConstruction, efSearch) that would earn its own ADR. IVF-Flat with `nlist=100` clusters is the standard middle path: sub-linear query, `>0.95` recall vs exact at `nprobe=10` on catalogs of this size, one hyperparameter to tune, and FAISS's implementation is battle-tested. FAISS also sits behind roughly every open-source vector-search stack we'll evaluate in Phase 3 (Feast + Milvus, Vespa, Weaviate — all bind FAISS or a FAISS-shaped index type), so building on it now transfers.

5. **Popularity fallback rather than a "cold user" learned vector.** Same argument [ADR 0001](0001-evaluation-protocol.md) and CFModel used. A learned cold-user vector (e.g. the mean of all user encodings) is not measurably better than popularity on cold users and adds a routing decision the training loop would have to defend. Embedding popularity keeps the fallback path uniform across every candidate generator in the lineage, which means the per-policy attribution metric already established for [PR #17](https://github.com/kudratsingh/MovieRecSys-MachineLearningProject/pull/17) generalizes without change: `was_served_by_twotower(uid)` returns `False` for cold users, and the training script splits MLflow metrics into two-tower-served vs fallback-served exactly as the item-item script does.

6. **Point-in-time correctness in the history-encoder is a first-class concern, not a code-review note.** CLAUDE.md flags this explicitly: "features must only use data available at the time of prediction. This binds especially tight on the two-tower's history input." The failure mode is silent and severe — if a positive item can appear in its own user-history input (or if items with `t' >= t_positive` are in the history), the model learns to reproduce the input, and recall@500 on holdout inflates arbitrarily. This ADR pins the construction rule and the code owns a `test_history_is_strictly_past` canary that fails the build if the invariant breaks.

## Alternatives considered

- **Per-user-id embedding instead of a history encoder.** The classical two-tower shape and the simpler code path. Rejected on rationale #1 — the whole reason we're layering a learned model over item-item is the ability to score users who don't exist at training time (including the users the ranker will see two months after training via a stale champion). A per-user embedding table is also awkward to serve: online retrieval would need to load ~160 k user vectors into memory or Redis, and Phase 3 already carries the per-tenant-Redis-prefix cost; a history-encoder computes the user vector at query time from feature-store history, which is the shape Phase 3 wants.

- **Add side features (genre, year, popularity buckets) to the item tower.** Would probably lift recall — item tower with side features is the standard next-step after id-only. Rejected *for this PR* because (a) side features live in `src/features/` which doesn't exist yet, (b) they belong to the feature-store discussion which arrives in Phase 3 with Feast, and (c) an id-only two-tower is the honest apples-to-apples comparison against item-item, which is also id-only. Side features are a follow-up ADR once Feast lands.

- **In-batch negatives only, no correction.** The one-line loss. Rejected on rationale #3 — the popularity-bias failure mode is real, well-documented, and easy to fix at model-building time. Waiting for it to surface as a mysteriously bad recall number costs more than shipping the corrected loss up front.

- **Hard negative mining.** Retrieve nearest incorrect neighbors as negatives, on the theory that the model learns more per gradient step. Rejected as scope-creep — hard negatives add a second retrieval per training step, a warm-up schedule, and their own tuning axis. Sampled softmax with log-uniform correction is the production baseline; hard negatives are a lift on top and earn their own ADR if we choose to add them.

- **Embedding dim 32.** Smaller model, faster training, cheaper index. Considered. Rejected because item-item's implicit sparse index and CF/ALS both operate at effectively higher-dimensional representations of items (item-item is 62k-dim sparse, CF is 64-dense); 32 dims would be *the two-tower alone shrinking* while its baselines don't, which contaminates the comparison. We can sweep to 32 later after the 64-dim number is on the board.

- **Embedding dim 128.** Larger model, higher headroom. Considered. Rejected as the default because (a) doubling the item-embedding table doubles the FAISS index memory footprint and doubles the offline recommend time for the holdout eval, and (b) the interesting scaling question — does two-tower beat item-item on the same information budget as CF? — is answered at 64. 128 is a natural follow-up sweep, not a starting point.

- **hnswlib instead of FAISS.** Comparable recall/latency, sometimes better on CPU. Rejected on rationale #4 — FAISS's ecosystem penetration is deeper, IVF-Flat's tuning surface is smaller than HNSW's, and hnswlib doesn't sit under any of the Phase 3 feature-store candidates. FAISS is the choice we can defend against "why not the other one" in a design review without invoking taste.

- **Attention over history instead of mean-pool.** Better representation of user preference — recent items and repeat interests get properly weighted. Considered seriously. Rejected as the starting shape because (a) mean-pool is the point where two-tower stops adding parameters and it's the honest first data point to log against item-item, (b) attention adds `d² ≈ 4 k` parameters per attention layer that we'd want to explain the choice of, and (c) if mean-pool underperforms, attention is the obvious next lift and earns its own ADR with the mean-pool number as its context.

- **Exact retrieval (no ANN) at eval time.** 62 k items × 2 641 holdout users at 64 dims is ~10 s on CPU; workable offline. Rejected because the whole point of the retrieval stage is to *train the code path that Phase 3 serves*. Eval running on exact search while serving runs on IVF-Flat means the offline recall@500 doesn't include IVF's approximation loss — which is a form of the offline/online skew CLAUDE.md's non-negotiable #2 exists to prevent.

## Consequences

- **Code layout.** `src/models/candidates/twotower.py` matches the `CandidateModel`-shaped contract (`fit`, `recommend`, `recommend_for_users`, `was_served_by_twotower`) that `PopularityModel`, `CFModel`, and `ItemItemModel` share. The two-tower class holds the PyTorch modules and the FAISS index; the training loop lives in `src/training/twotower.py`. Phase 3's serving layer can swap between candidate generators without knowing which is loaded.
- **MLflow.** Runs land in the existing `phase-2-candidates` experiment (created by [PR #19](https://github.com/kudratsingh/MovieRecSys-MachineLearningProject/pull/19)) with `model_type=two_tower`, so item-item, CF, popularity (re-run for reference), and two-tower sit on the same recall@K_CANDIDATES axis in one experiment view.
- **Dependencies.** `torch` is already in `pyproject.toml`. `faiss-cpu` gets added. `implicit` and `lightgbm` are unaffected. No GPU requirement — training on CPU is slow (~30-60 min for a Phase 2 sweep) but doesn't block progress.
- **Promotion gate.** Per ADR 0004, the two-tower must beat item-item's recall@K_CANDIDATES on the warm-user slice by a defined threshold to be promoted to champion candidate generator. The threshold is set when the two-tower's number lands, since it depends on the item-item baseline that PR #19 produced.
- **Cold users.** Metrics are unchanged in shape: the overall number mixes two-tower-served warm users with popularity-served cold users. Per-policy attribution splits them cleanly, same pattern as PR #17.
- **Serving surface (Phase 3).** The user tower needs the user's item history at query time. Phase 3's feature store (Feast) will surface this via an online feature view keyed by `user_id`; the tower's forward pass is `mean-pool(item_embeddings[history_ids])` and runs in <5 ms on CPU for `N=50`. The FAISS index gets shipped as an artifact alongside the model weights and loaded at service startup.
- **Deferred to future ADRs.** Item-side features on the item tower (once Feast lands). Attention over history (if mean-pool underperforms). Embedding-dim sweep (32 / 128) if the 64-dim number is inconclusive. Hard-negative mining if the sampled-softmax baseline plateaus.

## Risks

- **Sampled-softmax correction implemented incorrectly.** The log-uniform correction is one line but easy to get wrong (subtract vs add, `log` vs `ln`, batch dim vs sample dim). If the sign is wrong, the model *inverts* popularity — the loss looks like it's decreasing and recall on warm users collapses. Mitigation: a unit test that trains for 3 epochs on a synthetic dataset with a known popular item and asserts the popular item's mean logit is *higher* than a rare item's, not lower.
- **History leakage.** The most severe failure mode. Mitigation: `test_history_is_strictly_past` unit test on a hand-built 5-user, 20-interaction fixture where the expected history at each position is precomputed.
- **FAISS index staleness.** The FAISS index is built from the item tower at end-of-training; if training resumes and item embeddings shift, the index does not automatically rebuild. Mitigation: `fit` always rebuilds the index at the end of training; a corrupted-index case is caught by a smoke test that recommends for one user and asserts `len(result) == k`.
- **Popularity fallback masking a bad tower.** If the two-tower is broken but 30% of the holdout is cold users getting popularity, the *overall* metric can look reasonable while the tower is producing garbage. Mitigation: rationale #5's per-policy attribution — the primary comparison against item-item is the warm-user slice, computed on users where both models actually run their learned path.
- **Training determinism.** Reproducibility (non-negotiable #5) requires that `make train-twotower` on a fixed seed produce the same model artifact hash. PyTorch's cuDNN determinism is off the table (CPU only) but batch-order and negative-sampling randomness still need explicit seeding. Mitigation: seed `torch`, `numpy`, and `random` at start of training; document the caveat in the training script if any stochasticity remains.

## How we'd know we're wrong

- **The two-tower does not beat item-item on warm recall@K_CANDIDATES.** ADR 0004 established that the two-tower has to clear item-item to earn the champion slot. If after a reasonable hyperparameter sweep it doesn't, the shape in this ADR is inadequate for MovieLens 25M's warm-user distribution and the next ADR probes: side features on the item tower, attention over history, or a larger embedding dim.
- **Warm recall looks great; cold recall is unchanged.** Expected in this ADR's shape — cold users go through popularity by design. If cold recall *dropped*, something is wrong with the routing (a warm user is being misclassified as cold or vice versa) and the per-policy attribution split would surface it.
- **Recall@500 improves but downstream recommender-end-to-end NDCG@10 does not.** Would mean the two-tower's top-500 contains different items than item-item's top-500, but the ranker (once it lands via ADR 0005) can't tell them apart. That's information for the ranker's feature set, not evidence against this ADR.
- **The point-in-time canary test starts passing spuriously fast.** Would suggest history construction was silently reverted to include the positive or later timestamps. Mitigation: the canary is a strict-equality check against a hand-built expected history, not a "is it small enough" heuristic.
