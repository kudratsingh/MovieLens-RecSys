# Experiment: SASRec v1 / hand-written Transformer encoder / wo2-v1

**Status:** measured. Done-criterion 3 passed. Done-criterion 5 came out **outside the range**, and
that result is with the owner.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), with its 2026-10-05 amendment (rule
D7 and the equivalent-not-bit-identical note), and
[ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-2.
- Item 3 (inference check) runs approved 2026-10-05.
- Item 5 (pilot) approved 2026-10-05, after WO-1 (PR #194) and O-25.

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
| `wo2-handwritten-pilot6-bce-neg32` | encoder | 42 | inside [0.3611, 0.3936], else report to the owner |

## Compute and storage budget

- **Hardware:** the laptop CPU, `caffeinate -i`, one job at a time. `pgrep -fl src.training` was
  empty before each run.
- **Item 3:** about 1 min per run. Three attempts; the reasons are under Deviations.
- **Item 5:** projected about 20 min (WO-1's 18 min 21 s plus the measured encoder slowdown);
  actual 22 min 03 s. Spend: none.

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

**Validity:** valid (both runs).

**Partition affirmation:** intact. Item 3's latest fit timestamp is 1466837396 and its latest
scored is 1469256332; item 5's are 1466819964 and 1469247943. All are below 1469256597.

**Decision:**
- **Item 3: advance (passed).**
- **Item 5: report to the owner (criterion not met).**

**Rule application:**
- **Item 3.**
  - Warm recall@500 is 0.5091713455402274 against 0.5091713455402272 (+1.1e-16), and every
    per-user value is identical.
  - Cold is 0.5262729520330651, exact.
  - Against the packaged encoder on the same weights, 36 of 1,931 warm top-500 lists changed, all
    in order only.
- **Item 5.**
  - Warm recall@500 is **0.3427768663** against the range [0.3611061547, 0.3936318283]: 0.0183
    below the floor, −5.45% against WO-1 s42, and z = −1.96 against the reference mean.
  - Cold is 0.5427033422, identical to the references.
  - Epoch losses are within 0.09% of WO-1 s42.
  - Per-epoch warm recall crossed over: 0.3313 vs 0.3225 after epoch 1, 0.3428 vs 0.3625 after
    epoch 2.
  - Per user against WO-1 s42: 72 warm users identical, 11 higher, 25 lower.
  - Fit took 1,315.3 s against 1,080.8 s (+21.7%). Peak RSS was 3.08 GB against 3.04 GB.

**Runs:**
- Item 3: `a2c3f5ac09064114b24d70897d68526c` (the record) and `7c1d3377de164914bb5758a6c7fb9527`
  (metrics only).
- Item 5: `38442d1a08dd42f3868c1f6147a56fd8`.

**What is not authorized next:** retuning, re-running, or running further seeds with the
hand-written encoder. The owner chooses between, for example:
- paired seeds 7, 13 and 21 with the new encoder, to compare four against four;
- accepting the encoder on the equivalence and reproduction evidence; or
- investigating further before WO-3.

WO-3 should not start on the hand-written encoder until that decision is made.
