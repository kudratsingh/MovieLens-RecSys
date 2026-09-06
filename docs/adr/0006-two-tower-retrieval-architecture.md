# ADR 0006 — Two-Tower Retrieval Architecture

**Status:** Accepted
**Date:** 2026-07-02

**Correctness amendment (2026-09-05):** The negative retrieval conclusions in
the 2026-08-30 sweep note below are superseded. The item table was added to
FAISS as rows `0..N-1`, corresponding to dense item ids `1..N`, but
`TwoTowerModel.recommend()` discarded row 0 as though it were padding and used
every other row number directly as the dense id. Consequently, every retrieved
item was either dropped or translated to the preceding item. A cross-path test
against an independently built exact FAISS index exposed the mismatch, and a
200-user/400-item overfit canary reached loss `0.000498` and recall@10 `1.0`
after the row-to-id conversion was corrected.

One bounded seed-42 6% diagnostic after the fix reached warm recall@500
`0.3759484159` and NDCG@500 `0.1483997512` on 115 warm users (run
`8a22ed513b8f457eb0d5f93b826dc82a`, protocol
`sha256:090985d7075bd3df802ecb5da9402bc7fa7f3d10769dd9d384585113187cb629`).
The previous complete-v2 result was `0.0435`. Its same-cohort reference lines
were popularity `0.1974` and item-item `0.3619`. The corrected diagnostic used
exact retrieval and 16,384 sampled negatives rather than the earlier complete
arm's IVF retrieval and 4,096, so it does not isolate the effect size or make a
promotion decision. It does establish that the old below-popularity result was
an evaluator-path defect, not evidence about the embeddings. No full-data run
or additional sweep was authorized.

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

5. **Popularity fallback rather than a "cold user" learned vector.** Same argument [ADR 0001](0001-evaluation-protocol.md) and CFModel used. A learned cold-user vector (e.g. the mean of all user encodings) is not measurably better than popularity on cold users and adds a routing decision the training loop would have to defend. Embedding popularity keeps the fallback path uniform across every candidate generator in the lineage, which means the per-policy attribution metric already established for [PR #17](https://github.com/kudratsingh/MovieLens-RecSys/pull/17) generalizes without change: `was_served_by_twotower(uid)` returns `False` for cold users, and the training script splits MLflow metrics into two-tower-served vs fallback-served exactly as the item-item script does.

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
- **MLflow.** Runs land in the existing `phase-2-candidates` experiment (created by [PR #19](https://github.com/kudratsingh/MovieLens-RecSys/pull/19)) with `model_type=two_tower`, so item-item, CF, popularity (re-run for reference), and two-tower sit on the same recall@K_CANDIDATES axis in one experiment view.
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

## 2026-08-30 — sweep note: what a learning-rate and budget sweep found about this configuration

> **Superseded by the 2026-09-05 correctness amendment above.** These runs are
> retained as an audit record, but all retrieval metrics passed through the
> broken FAISS row-to-dense-id conversion and cannot support a model verdict.

[`results.md`](../results.md)'s first 2026-08-30 session measured this ADR's configuration on the full dataset and got warm recall@500 of 0.0466 against item-item's 0.4001, on a loss curve that stopped moving after one epoch. It called that "a hyperparameter symptom before it is an architectural one" and named a training-budget and learning-rate sweep as the cheapest next experiment. That sweep has now been run — a 12-cell pilot on a seeded 6% of users, then two full-dataset runs — and this note records what it found about **the configuration in this ADR**. The decision itself is not changed here: no default moved in code, and this note proposes rather than adopts.

### The finding: this ADR pins a normalization and a correction, and never names the constant that reconciles them

Two of the Decision bullets above interact in a way that neither states.

- **"Inner-product metric over L2-normalized item embeddings"** means a raw logit is a cosine, bounded to `[-1, 1]`. Two nats of range, total, for the model to express any preference in.
- **"Each negative's logit is corrected by subtracting `log P_sampled(item)`"** adds a fixed, unlearnable offset to every candidate. Measured over the real train split's 34,461 items with this ADR's own sampler, those offsets run from **2.713 nats** for the most-watched title to **12.794** for a title seen once: a **10.081-nat spread**.

**So the ordering of the 16,384 candidates in a batch is decided, to within ±1, by their popularity ranks, and the model is allowed to object by at most two nats.** That is not a subtle imbalance; it is a factor of five.

The consequence shows up in a number this ADR already had, which nobody read this way: **v1's final training loss, 10.2718, is worse than the loss of a model that emits the identical logit for every candidate**, which is `ln(1 + 16,384) = 9.7041`. A model that had learned nothing at all would have scored better on this objective than v1 did after three epochs. The loss curve flattening after epoch 1 was not a model that had finished learning; it was a model that had run out of room to say anything.

The standard constant that reconciles the two bullets is a **temperature** on the cosine before the correction is applied — `cos(u, i) / τ` — which every production cosine-similarity two-tower carries and which this ADR does not mention. v1 therefore ran at an implicit `τ = 1.0`. At `τ = 0.1` the model's authority over a logit becomes 20 nats instead of 2, and the sweep measures what that buys.

For the record, the correction is not a *mean* handicap on the positive: measured on the real distribution, a training positive draws a correction of 8.520 nats on average against a sampled negative's 7.572, so the positive is 0.948 nats ahead on average. **The damage is the spread, not the mean.**

### One discrepancy between this ADR and the code, now measurable

The Decision bullet says "each **negative's** logit is corrected". The code also corrects the positive's, which is what TensorFlow's `sampled_softmax_loss` does — it treats the true class as if it too could have been drawn. Rather than argue about which reading was meant, `correct_positive_logit` (default `True`, what v1 did) makes the difference a cell in a table: running this ADR's literal reading on the pilot costs **8.5 nats of loss** — 19.0297 → 18.5886 → 18.4928 against the code's 10.5581 → 10.1168 → 10.0212 — because a positive that gets no boost has to out-score negatives that got between 2.7 and 12.8 nats of one for free. So the code's reading is the better one, and it is this ADR's wording that should change. The more telling number is the other column: both cells finish at warm recall@500 of **0.0443**, the same to four decimal places. **Eight and a half nats of objective moved retrieval by nothing at all** — the sweep's central finding arriving early, in the one place a reader might otherwise assume the discrepancy mattered.

### What the sweep changed, and what it did not

The tower shapes, the loss family, the sampler, the retrieval index and the routing are all this ADR's, untouched. What changed is that every `TwoTowerConfig` field is now readable from a `TWOTOWER_*` environment variable and logged to MLflow, so a run is reproducible from its own params; and four fields were added, each defaulting to its v1-equivalent value — `logit_temperature = 1.0`, `correct_positive_logit = True`, `early_stopping_patience = 0`, `faiss_exact = False`. A unit test (`test_adr_0006_defaults_are_unchanged`) asserts every default in this ADR, so adopting anything below has to be a deliberate diff.

### What the sweep measured

Full tables, loss curves and wall-clocks are in [`results.md`](../results.md)'s second 2026-08-30 section. The findings that bear on this ADR:

1. **The learning rate in this ADR is fine.** At τ = 1.0, `lr ∈ {1e-4, 1e-3, 1e-2}` gives warm recall@500 of 0.0392 / 0.0443 / 0.0459 on the pilot — a spread smaller than a 116-user slice can resolve, and every value between 1.5× and 1.8× the chance line. **1e-3 stays.**

2. **A temperature fixes the objective and does not fix retrieval.** At τ = 0.1 and the default learning rate the pilot's loss goes 9.6159 → 8.7463 → 8.5407 — under the 9.7041 no-opinion line on the first epoch and still falling on the third, where v1's τ = 1.0 never gets under that line at all. Warm recall@500 over the same three epochs goes 0.0470 → 0.0432 → 0.0413. **The loss and the metric point in opposite directions**, which is the most useful thing the sweep produced: it rules out "under-trained" as the explanation, because the model is not under-trained. It is well-trained on an objective whose optimum does not retrieve.

3. **The model loses to its own fallback.** On the same warm users with the same already-seen filter, the embedded `PopularityModel` scores warm recall@500 of 0.1974 and item-item 0.3619, against the whole two-tower family's 0.04–0.05. A two-tower that loses by 4× to the popularity list it carries inside itself for cold users is not a tuning problem. And it is not a pilot artefact. Popularity at `K_CANDIDATES = 500` had never been measured on the full dataset — the candidate-stage table only ever scored it at K = 10 — so this sweep measured it: **warm recall@500 of 0.2310 over the same 1,939 warm holdout users, against two-tower v1's 0.0466.** A factor of **4.96**, with item-item at 0.4001 and chance at 0.014509. v1 does not sit between popularity and item-item where a working-but-weaker retriever would; it sits between *chance* and popularity.

### Proposal

**Adopt a `logit_temperature` on this ADR's Decision list, at `0.05`, the value the sweep actually measured on the full dataset — where it produced this project's best two-tower numbers, warm recall@500 **0.0591** against v1's 0.0466 and NDCG@500 0.0193 against 0.0146, on a loss of **8.1262**, more than 1.5 nats under the no-opinion line v1 never reached. One honesty note on the value rather than the principle: on the pilot the best *loss* at this ADR's own learning rate came from **τ = 0.1** (8.5407, against τ = 0.05's 8.8088), and τ = 0.1 was the better temperature at 1e-2 as well. Choosing cleanly between 0.05 and 0.1 wants a full-dataset pass of its own; the day held two, and they went to the budget question and to the pilot's best-scoring cell. So: adopt a temperature, at 0.05 on the evidence that exists, and treat the exact value as open**, beside the normalization and the correction it reconciles. The argument is not that it clears ADR 0004's gate — **it does not, and nothing here should be read as proposing promotion**. The argument is narrower: *a model should be able to fit its own loss*. At τ = 1.0 this configuration cannot; it scores worse than a model with no opinion at all, and any future work on this shape — hard negatives, side features, attention, a larger embedding — would be built on an objective that is not measuring what it is supposed to measure. Fixing that is a precondition for Rung 1 being interpretable, not a substitute for it.

**The default is not changed in code by this note.** `TwoTowerConfig` still ships this ADR's values and a test asserts them; the change wants its own diff, and the owner's approval gate in [`modeling-roadmap.md`](../modeling-roadmap.md) is the right place for it.

### How we'd know this note is wrong

- **The pilot's 116-user warm slice misled the ordering.** Plausible for differences of 0.005 between two-tower cells; not for the 8× gap to item-item or the 4× gap to popularity, and the full-dataset runs reproduce the ordering. If a re-seeded pilot inverted the temperature finding, finding #2 would need re-running; findings #1 and #3 would not.
- **The temperature helps at a budget nobody here could afford.** The loss at τ = 0.1 was still falling when every run stopped, so "it turns around after N more epochs" is not excluded by anything measured here. It is made unlikely by recall falling across the epochs of every temperature cell at the default learning rate, in the same direction, from the first epoch on — and by the full-dataset budget run, which carried this ADR's own configuration to eight epochs on all 19,867,692 training pairs. Epochs 4 through 8 cost an hour and three quarters of extra fit, bought **0.0011 nats** (10.2718 → 10.2707, with the eighth epoch worse than the seventh), and ended at warm recall@500 of **0.0451 — below the 0.0466 the third epoch reached.** A budget nearly three times v1's made the model very slightly worse. The per-epoch embedding-spread metric added in the same PR says why in one line: mean pairwise item cosine moves 0.130 → 0.138 and its standard deviation 0.737 → 0.731 across all eight epochs, so the geometry is settled inside the first epoch and seven more do not disturb it.
- **Retrieval, not the towers, is losing the recall.** FAISS IVF-Flat at `nprobe = 10` scans about a tenth of the catalog, and this ADR asserts ">0.95 recall vs exact at nprobe=10" without ever having measured it on these embeddings. So the sweep measured it: a cell identical to `lr1e-3-t0.05` except for exact inner-product search finishes at warm recall@500 of **0.0432 against the IVF twin's 0.0406**, having been *lower* than IVF at the first two epochs. Removing the approximation entirely moves recall by less than 0.003 and leaves the model 4.6× below popularity, so this ADR's IVF choice is exonerated and every recall number in the sweep is a measurement of the embeddings rather than of the index. The two cells' per-epoch losses are bit-identical (10.4800 → 9.1271 → 8.8088), which is also a free determinism check on the trainer.

## 2026-09-05 — environment note: `OMP_NUM_THREADS=1` alone does not make this ADR's runs reproducible

This note is about the conditions two-tower runs are measured under, not about the architecture.
Nothing in the Decision changes. It exists for two reasons: the standing mitigation for this model's
OpenMP problem is documented in two places and is **not sufficient**, and the corrected 6% figure in
the amendment at the top of this file turns out to have been taken under a condition nobody wrote
down.

### The standing mitigation is incomplete

[`results.md`](../results.md) and the `Makefile` both say the two-tower needs `OMP_NUM_THREADS=1` on a
macOS wheel set where torch and faiss each bring their own `libomp`, "otherwise it segfaults". That is
true, and it is half the story: the pin does not remove the collision, it relocates it. Measured on
the development machine (torch 2.12.0 installed 2026-05-20, faiss-cpu 1.14.3 installed 2026-07-03,
three `libomp.dylib` copies present — `torch/lib/`, `faiss/.dylibs/`, `sklearn/.dylibs/`) with a
ten-second script that does nothing but `import faiss`, `import torch`, a few torch matmuls, and one
`IndexFlatIP` build:

| condition | outcome |
|---|---|
| `OMP_NUM_THREADS` unset | **SIGSEGV (139)** inside torch's parallel region, before faiss is reached |
| `OMP_NUM_THREADS=1` | **SIGABRT (134)**, `OMP: Error #15`, at the **first faiss index build** |
| `OMP_NUM_THREADS=1` + `KMP_DUPLICATE_LIB_OK=TRUE` | exit 0, clean, no error emitted |

The middle row killed a full-data run on 2026-09-05: epoch 1 of 3 completed cleanly (loss 8.4730,
26m30s) and the process aborted immediately after, at `on_epoch`'s `build_index()` — the first
`faiss.IndexFlatIP` construction in the process. Memory was not a factor and was not assumed to be
one: RSS held ~5.4 GB and free+inactive never fell below 6.53 GiB, sampled once a minute throughout.

This also disposes of the remedy `results.md` proposes — "defer the `faiss` import until after the
training loop". The import is not the trigger: `import faiss` already sits at module top in
`candidates/twotower.py` and does not collide. The **first index construction** is the trigger, and
it cannot be deferred past the per-epoch evaluation without deleting the per-epoch recall curve, nor
past the final evaluation at all.

### The workaround is validated for this workload, by reproduction rather than by argument

`KMP_DUPLICATE_LIB_OK=TRUE` is described by the OpenMP runtime itself as "unsafe, unsupported,
undocumented", warning that it "may cause crashes or silently produce incorrect results". That warning
is a reason to measure, not a reason to assume. The 6% pilot was re-run under the flag in the recorded
conditions of the corrected diagnostic (seed 42, exact FAISS, 16,384 sampled negatives, CSV input,
throwaway file-store MLflow) and compared at full double precision against run
`8a22ed513b8f457eb0d5f93b826dc82a`:

| metric | re-run under the flag | recorded in the amendment above | agreement |
|---|---|---|---|
| warm recall@500 | 0.3759484158672824 | 0.3759484158672824 | exact |
| warm NDCG@500 | 0.14839975118338375 | 0.14839975118338375 | exact |
| cold recall@500 | 0.48295870268712504 | 0.48295870268712504 | exact |
| cold NDCG@500 | 0.3844085561908767 | 0.3844085561908767 | exact |
| overall recall@500 | 0.4083757755096589 | 0.4083757755096589 | exact |
| overall NDCG@500 | 0.21991757088262404 | 0.21991757088262404 | exact |

Every epoch agrees too — losses 9.8231 / 8.8138 / 8.6116, per-epoch warm recall 0.3339 / 0.3589 /
0.3759, and the embedding-spread pair at all three — on an identical input frame (9,752 of 162,541
users, 1,517,399 rows, cutoff 1471288304, 1,198,161 pairs across 8,316 users and 19,005 items) and
under the same protocol hash `sha256:090985d7…`. A flag that silently corrupted arithmetic would not
land on the same sixteen significant figures six times over, three epochs deep.

This is a reproduction check, not a second measurement: it makes no new claim, adds no number to the
record, and therefore does not sit against the one-run-per-configuration policy.

### The consequence for the record

The corrected 6% result **stands**, and the amendment above is unaffected. But the code path is
deterministically fatal on this machine without the flag, so that diagnostic cannot have been produced
without it or something equivalent, and the condition went unrecorded. It is recorded now: **a
two-tower run on this machine requires `OMP_NUM_THREADS=1` *and* `KMP_DUPLICATE_LIB_OK=TRUE`**, and
any two-tower run reported without both stated should be treated as having an unrecorded environment.

### The clean fix, deliberately not taken here

With `faiss_exact = True` the index is `IndexFlatIP` — exact inner product — which is a matrix
multiply and a top-k. Computing it in torch would remove faiss from the training process altogether
and retire the collision rather than suppress it, with results identical by construction rather than
by measurement. That is a code change to a champion-adjacent model and wants its own PR and its own
tests; it is recorded here as the follow-up, not adopted in this note.
