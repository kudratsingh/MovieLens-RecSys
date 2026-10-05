# Experiment: SASRec v1 / hand-written Transformer encoder / wo2-v1

**Status:** measured and accepted.
- Done-criterion 3 passed.
- The first seed-42 pilot missed the range.
- The owner-ordered training-parity checks found one difference (the dropout-mask layout), which
  was fixed.
- After the fix all four seeds reproduce WO-1's pilots exactly, so the owner's rule accepts WO-2.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), with its 2026-10-05 amendment (rule
D7 and the equivalent-not-bit-identical note), and
[ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-2.
- Item 3 (inference check) runs approved 2026-10-05.
- Item 5 (pilot) approved 2026-10-05, after WO-1 (PR #194) and O-25.
- Owner ruling 2026-10-05 after the miss: training-parity checks, a fix if they find a difference,
  seeds 7/13/21, and acceptance if the four-seed mean is not below 0.353565.

**Specification version:** wo2-v1 (2026-10-05). The cells are
[`wo2-converter-recheck.json`](../../experiments/sasrec/wo2-converter-recheck.json) and
[`wo2-handwritten-pilot-6pct.json`](../../experiments/sasrec/wo2-handwritten-pilot-6pct.json).
Branch `feat/wo2-transformer-from-scratch`, PR #196, rebased onto WO-1.

## Decision this experiment informs

Whether the hand-written encoder (`src/models/candidates/transformer.py`) can replace PyTorch's
packaged encoder as SASRec's encoder. That needs two things: saved models keep scoring as recorded,
and training with it lands where training with the packaged encoder lands.

## Hypothesis and falsifier

**Hypothesis:** the hand-written encoder computes the same function as the packaged one.
- With converted weights it reproduces the recorded full-data retrieval.
- Its initialization is bit-identical, so a retrained pilot differs only by float rounding over
  training, which should be no larger than seed-to-seed variation.

**Falsified when either:**
- the converted v1 model misses warm recall@500 0.5091713455 at four decimals, or cold
  0.5262729520 exactly (done-criterion 3); or
- the seed-42 pilot lands outside the range of WO-1's four reference pilots (done-criterion 5).

## Baselines and controls

| Model/control | Why it is required | Run/spec reference |
|---|---|---|
| Full-data v1, packaged encoder | The recorded number criterion 3 must reproduce | run `528b14513d9a49e098a0525417f23285`, model `a11af5ed…` |
| The same v1 weights run through the packaged encoder today | Separates encoder effects from environment effects | `tests/unit/test_sasrec_transformer.py::test_pinned_v1_population_lists_against_the_packaged_encoder` |
| WO-1 reference pilots s42/s7/s13/s21, packaged encoder | The range criterion 5 is judged against | `7baeb7d0…`, `d71f0fa6…`, `2d9f3cc1…`, `8668ca0c…` |

## Changed and fixed axes

**Changed:** the encoder implementation only. Item 3 also changes the weight layout on load, by a
lossless in-memory conversion.

**Fixed:**
- **Item 3.** Data, split, catalog, threshold 10, exclusions, K = 500 and exact search: all of
  `528b1451…`'s protocol.
- **Item 5.** WO-1's cell exactly: hidden 64, 2 blocks, 2 heads, feed-forward 256, dropout 0.2,
  L = 50; BCE with 32 uniform negatives; batch 512, Adam 1e-3, 2 epochs; objective
  `strict-prefix-final-position-v1`; seed 42; O-25's 6% partition; CPU with `OMP_NUM_THREADS=1`
  and `KMP_DUPLICATE_LIB_OK=TRUE`.

## Protocol identity

| Field | Item 3 | Item 5 |
|---|---|---|
| DVC/raw revision | `md5:c3ce6309…` (ml-25m CSV, 25,000,095 rows) | same |
| Train cutoff / holdout window | 1466837397 / [1466837397, 1469256597) | same (O-25) |
| Rolling window(s) | w0 only | w0 only |
| Partition(s) read | `holdout` | `holdout` |
| Cold threshold / routing | 10 / train-history-count ≥ 10 | same |
| Stage / K | retrieval / 500 | retrieval / 500 |
| Protocol hash | `sha256:b4ed5afa…`, equal to `528b1451…` | `sha256:faf2828d…`, equal to WO-1's, confirmed before the number was read |

## Partition declaration

| Field | Item 3 | Item 5 |
|---|---|---|
| Partition(s) read | `holdout` | `holdout` |
| Owner unseal approval | not applicable | not applicable |
| Sealed boundary this run used | `holdout_end` = 1469256597 (2016-07-23 06:49:57 UTC), derived by `temporal_split` from the CSV | 1469256597, the logged `holdout_end_timestamp` and `sealed_boundary_timestamp` |
| Feature source and its as-of | raw interactions, strict prefix | raw interactions, strict prefix |
| Latest event timestamp that entered fitting | none (inference only); the model's train frame max is 1466837396 and the cohort max is 1466751… | 1466819964 (logged `latest_fit_timestamp`) |
| Latest event timestamp that entered scoring | 1469256332 (holdout max) | 1469247943 (logged `latest_scored_timestamp`) |

**Affirmation.** Claude (WO-2 implementer), 2026-10-05: neither run read any interaction at or after
the sealed boundary above.

## Metrics

**Primary:** warm recall@500 through `src/evaluation/protocol.evaluate`.

**Guardrail:** cold recall@500 must be identical, since cold users are routed to popularity.

**Diagnostics:**
- per-user recall against the reference;
- changed top-500 lists, split into membership and order (item 3);
- epoch losses and epoch warm recall, fit seconds and peak RSS (item 5).

## Grid and seeds

| Cell | Changed fields | Seed | Rule |
|---|---|---|---|
| converter recheck | encoder, plus the layout conversion on load | 42 (the artifact's) | warm equal to 4 dp and cold exact, else stop |
| `wo2-handwritten-pilot6-bce-neg32` (before the fix) | encoder | 42 | inside [0.3611, 0.3936], else report to the owner |
| `…-rerun-s42`, `…-s7`, `…-s13`, `…-s21` (after the fix) | encoder | 42, 7, 13, 21 | four-seed mean not below 0.353565 (within 5% of 0.372174), else stop |

## Compute and storage budget

- **Hardware:** the laptop CPU (8 cores), `caffeinate -i`, `OMP_NUM_THREADS=1`. Item 3 and the
  first pilot ran with no other `src.training` process. The post-fix pilots ran under the owner's
  concurrency rule: at most three 6% pilots in flight, counting another worker's gBCE pilot, with
  at least two cores free.
- **Item 3:** about 1 min per run. Three attempts; the reasons are under Deviations.
- **Item 5:** projected about 20 min (WO-1's 18 min 21 s plus the measured encoder slowdown);
  actual 22 min 03 s.
- **Post-fix pilots:** wall time 34 min 44 s, 34 min 45 s, 31 min 28 s and 29 min 22 s, under
  contention. That is within twice a contended projection, though above the 22 min solo figure.
  The training-parity checks took about 30 min of test time. Spend: none.

## Pre-run correctness checklist

- [x] Governing ADR is approved for this work (ADR 0020 and its 2026-10-05 amendment; brief WO-2).
- [x] The protocol fingerprint matches its baseline: `b4ed5afa…` for item 3, `faf2828d…` for item 5.
- [x] Temporal and equal-time leakage tests pass (unit suite: 1,964 passed after the rebase).
- [x] Model-specific correctness gates pass (`tests/unit/test_sasrec_transformer.py`: D7 grep,
      equivalence, memorization, gradients, causality, padding).
- [x] Baselines are fixed (`528b1451…`; WO-1's four pilots).
- [x] Stop rules are approved (brief WO-2; coordinator 2026-10-05).
- [x] The partition declaration is filled in before the runs.
- [x] The feature source is point-in-time per row.
- [x] Output paths and MLflow stores are known (see Deviations).
- [x] No other training process was running.

## Training-parity checks (owner ruling, 2026-10-05)

The first seed-42 pilot missed the range, and the equivalence evidence covered inference only. The
owner therefore ordered three checks of training itself before more pilots. They are tests, not
training runs.
- **Code:** `tests/unit/test_sasrec_transformer.py`.
- **Real-batch report:** `artifacts/wo2-pilot/training-parity.json` in the main checkout.
- **How they ran:** both encoders go through `SASRecModel._train_strict_prefix`'s own step: WO-1's
  strict-prefix store, the per-example sampler, BCE. They start from the same weights (the packaged
  encoder's, converted) and the same generator state.

**1. Initialization: identical. No change.**
- **Construction order.** Both encoders draw from the generator in this order, which is v1's:
  1. `item_embedding` normal(0, 1);
  2. `position_embedding` normal(0, 1);
  3. block 0's attention output projection (Kaiming-uniform weight, uniform bias, the bias then
     zeroed);
  4. one Xavier-uniform `(3d, d)` matrix, sliced into query, key and value (biases zeroed);
  5. the `expand` layer (Kaiming-uniform, uniform bias), then the `contract` layer;
  6. layer norms set to ones and zeros (no draws);
  7. blocks 1..N−1 as deep copies (no draws);
  8. `item_embedding` redrawn as normal(0, 1/√d), row 0 zeroed.
- **Seed 42 at the pilot's shape** (18,780 item rows):
  - every tensor is bit-identical, so shape, mean, standard deviation, minimum and maximum are
    identical;
  - the generator state after construction is identical, so the same number of draws was consumed.
  - Example values: `item_embedding` mean −5.8e-5, sd 0.1249, range [−0.608, 0.583]. Block 0's
    query weight sd 0.0887, range ±0.1531 against the Xavier bound √(6/256) = 0.1531.
  - The per-tensor table is in the JSON report.
- **Tests:** `test_a_fresh_encoder_is_a_fresh_v1_encoder_bit_for_bit` (now also asserting the
  generator state) and `test_training_parity_on_three_real_pilot_batches`.

**2. Gradients: within tolerance.** The first three batches of the seed-42 pilot (512 examples
each), drawn from the O-25 6% partition exactly as `fit` draws them, compared at the same starting
weights:

| Dropout | Dtype | Max loss difference | Max gradient difference (all tensors) | Largest gradient |
|---|---|---:|---:|---:|
| 0 | float32 | 6.0e-8 | 1.1e-8 | 2.0e-2 |
| 0 | float64 | 1.1e-16 | 2.4e-17 | 2.0e-2 |
| 0.2 (same seed) | float32 | 0 | 6.5e-9 | 1.9e-2 |
| 0.2 (same seed) | float64 | 1.1e-16 | 1.7e-17 | 1.9e-2 |

Both are within the required 1e-5. After three Adam steps on those batches, the losses agree
exactly in float32. The weights agree to 7.8e-6 to 8.4e-6 in float32 and about 2e-14 in float64.
Adam's early steps move each weight by roughly the learning rate whatever the gradient's size, so
rounding on a near-zero gradient shows up at that scale.
- **Tests:** `test_same_seed_training_steps_match_the_packaged_encoder` (permanent, synthetic data,
  both dtypes, both dropout settings) and the data-gated real-batch test.

**3. Dropout: same sites and rates, one difference found and fixed.**

| Site | `nn.TransformerEncoderLayer` (v1) | Hand-written | Rate |
|---|---|---|---|
| Attention weights, after softmax | `self_attn.dropout` (inside scaled-dot-product attention) | `attention.weight_dropout` | 0.2 |
| Attention output, before the residual add | `dropout1` | `attention_dropout` | 0.2 |
| Feed-forward, after GELU | `dropout` | `feed_forward.dropout` | 0.2 |
| Feed-forward output, before the residual add | `dropout2` | `feed_forward_dropout` | 0.2 |
| Embeddings, final norm, anywhere else | none | none | — |

**Generator consumption.** Both implementations consume the generator in the same order:
1. construction (above);
2. per epoch, `randperm` over the examples;
3. per forward pass and per block, four Bernoulli masks: attention weights `(B, H, L, L)`,
   attention output `(B, L, d)`, inner feed-forward `(B, L, F)`, feed-forward output `(B, L, d)`.

The sampler's negatives come from a separate NumPy generator, untouched by the encoder.

**The difference.** The count and order of draws were already the same, but one mask landed on
different elements.
- PyTorch's batch-first attention returns a `(B, L, d)` view of an `(L, B, d)` buffer, and dropout
  fills its mask in memory order. So `dropout1`'s random numbers fell on `(L, B, d)`-ordered
  elements in v1 and on `(B, L, d)`-ordered elements in the hand-written encoder.
- The two models are statistically the same, but they follow different training trajectories from
  the same seed. That divergence starts at step 1 and fully explains why the first pilot's epoch-1
  loss differed from WO-1's s42 in the fifth digit.
- **Fix** (commit `82bba87`): merge the heads position-major and return the same view. Same-seed
  train-mode outputs now agree to 7e-7.
- **Proof the test catches it:** before the fix the dropout-0.2 parity tests fail. They now pass,
  and `test_dropout_sites_and_rates_match_the_packaged_encoder` pins the table above.

**Consequence.** Under the owner's rule (b), the pre-fix seed-42 pilot `38442d1a…` is superseded by
a re-run. With identical masks, a same-seed run now differs from WO-1's only by float rounding.

## Commands

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WT=$MAIN-wt-wo2
PY=$MAIN/.venv/bin/python
# Item 3, record (local SQLite store; see Deviations)
cd $WT && SASREC_RECHECK_SCOPE=converter \
  SASREC_FASTPATH_RETRIEVAL_TRACKING_URI=sqlite:///$MAIN/artifacts/wo2-converter/mlflow.db \
  SASREC_FASTPATH_EVIDENCE_DIR=$MAIN/artifacts/wo2-converter/evidence \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m OMP_NUM_THREADS=1 \
  caffeinate -i $PY -m src.training.sasrec_fastpath_recheck
# Item 3, list comparison against the packaged encoder
cd $WT && SASREC_PINNED_MANIFEST=artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/sasrec-manifest.json \
  SASREC_V1_LIST_DIFF_OUT=artifacts/wo2-converter/list-diff.json \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m OMP_NUM_THREADS=1 \
  caffeinate -i $PY -m pytest tests/unit/test_sasrec_transformer.py -k pinned -s
# Item 5
cd $WT && caffeinate -i env OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE MLFLOW_ALLOW_FILE_STORE=true \
  MLFLOW_TRACKING_URI=file://$WT/mlruns TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m \
  SASREC_ARTIFACT_DIR=$MAIN/artifacts/wo2-pilot/models \
  /usr/bin/time -l $PY -m src.training.sasrec_sweep docs/experiments/sasrec/wo2-handwritten-pilot-6pct.json
```

Post-fix pilots (owner ruling step c): one process per cell, each with the item-5 environment
above, started by a queue that never let `src.training` processes in flight exceed three. The
queue script is `wo2-pilot-queue.sh`, kept in the session scratchpad; its log is
`artifacts/wo2-pilot/logs/queue.log`.

```bash
for name in rerun-s42 s7 s13 s21; do
  while [ "$(ps -axo command | grep -cE '^/opt/homebrew/\S+/Python -m src\.training\.')" -ge 3 ]; do sleep 30; done
  ( caffeinate -i env $ITEM5_ENV /usr/bin/time -l $PY -m src.training.sasrec_sweep \
      docs/experiments/sasrec/wo2-handwritten-pilot-6pct-$name.json > $LOGS/wo2-pilot-$name.log 2>&1 ) &
  sleep 45
done; wait
```

## Deviations

- **MLflow stores.** The shared server at `localhost:5001` uses `--default-artifact-root
  /mlartifacts`, which only exists inside its container, so the host client cannot write
  artifacts.
  - Item 3's first attempt, `7c1d3377…`, logged every metric there (identical to the record) and
    then failed on its first artifact. It is tagged `failure_cause`.
  - The record is a local SQLite store with a host artifact location, run `a2c3f5ac…`.
  - A file-store attempt failed at run creation: MLflow 3.15 refuses a file-store path that
    contains a folder named `artifacts`.
  - Item 5 used a local file store in the worktree.
- **Cohort.** The ADR 0011 parquet was absent, so it was regenerated from the CSV. Its md5
  `9e0c978e…` equals the DVC pointer.

## Verdict

**Validity:** valid. All six runs are valid: item 3's record and its metrics-only twin, the pre-fix
pilot, and the four post-fix pilots.

**Partition affirmation:** intact.
- Item 3: latest fit 1466837396, latest scored 1469256332.
- Every pilot: latest fit 1466819964, latest scored 1469247943.
- All are below 1469256597.

**Decision:** accept WO-2 (owner rule d).
- The parity checks pass after the fix.
- The four-seed mean is 0.3721736, equal to WO-1's 0.372174 and above the 0.353565 floor.

**Rule application:**
- **Item 3.**
  - Warm recall@500 is 0.5091713455402274 against 0.5091713455402272 (+1.1e-16), and every
    per-user value is identical.
  - Cold is 0.5262729520330651, exact.
  - Against the packaged encoder on the same weights, 36 of 1,931 warm top-500 lists changed, all
    in order only.
- **Pre-fix pilot `38442d1a…`.**
  - Warm recall@500 0.3427768663, outside [0.3611, 0.3936] and −5.45% against WO-1 s42.
  - Superseded: the training-parity check traced the gap to the dropout-mask layout.
- **Post-fix pilots.** Warm recall@500 against WO-1's same-seed run:

  | Seed | WO-2 | WO-1 |
  |---:|---:|---:|
  | 42 | 0.3625487076 | 0.3625487076 |
  | 7 | 0.3611061547 | 0.3611061547 |
  | 13 | 0.3714076606 | 0.3714076606 |
  | 21 | 0.3936318283 | 0.3936318283 |

  - All 108 warm users' recall is identical on every seed, and cold is identical.
  - Epoch losses agree to about 1e-9. Weight digests differ, from float32 rounding.
  - Mean 0.3721736 and sample standard deviation 0.0150130, both equal to WO-1's.
- **Cost.**
  - Solo fit (pre-fix run, the same arithmetic): 1,315.3 s against WO-1's 1,080.8 s, +21.7%.
  - The post-fix runs were concurrent: up to three pilots, plus another worker's gBCE pilot and an
    ingest. Fit 2,072.4 / 2,071.3 / 1,874.2 / 1,750.9 s, `ru_maxrss` 2.34 / 1.88 / 1.82 / 1.86 GB.
    These describe the shared machine, not the encoder.

**Runs:**
- Item 3: `a2c3f5ac09064114b24d70897d68526c` (record) and `7c1d3377de164914bb5758a6c7fb9527`
  (metrics only).
- Pre-fix pilot: `38442d1a08dd42f3868c1f6147a56fd8` (superseded).
- Post-fix pilots: `d0b596f7171a4623b22b6b9e774ade3f` (s42), `2d800544a27b4a67bc6f844a91ad0efc`
  (s7), `76a1cd9e13d541958e564bcaea49c414` (s13) and `29ad5b791ebe48bdbf6e23de51966bfa` (s21).

**What is not authorized next:** this accepts the encoder for WO-3 onward. It is not a quality
claim: these are 108-user pilots that check correctness and direction. No full-data run, gate,
threshold or champion changes.
