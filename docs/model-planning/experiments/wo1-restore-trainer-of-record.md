# Experiment: SASRec v1 / restored trainer of record on the memory-bounded data path / wo1-v1

**Status:** measured.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md). Its 2026-09-15 decision note makes the
copied-prefix objective the objective of record, to run "once its data path is rewritten to be
memory-bounded … equality-tested against the current builder". Also governing: the ADR 0020
amendment 2026-10-05 (separate docs PR) and
[ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-1.
- Runs approved 2026-10-05.
- Pilot protocol: O-25 (2026-10-05, `docs/model-planning/owner-decisions.md`, PR #195). The 6%
  sample is cut at the full split's boundaries.

**Specification version:** wo1-v1 (2026-10-05), amended for O-25 before any run. The cells are
[`wo1-old-loop-control-6pct.json`](../../experiments/sasrec/wo1-old-loop-control-6pct.json),
[`wo1-restored-loop-6pct-s42.json`](../../experiments/sasrec/wo1-restored-loop-6pct-s42.json) and
[`wo1-restored-loop-6pct-seeds.json`](../../experiments/sasrec/wo1-restored-loop-6pct-seeds.json).

## Decision this experiment informs

Whether the restored loop on the P1 data path is SASRec v1's trainer of record. If it is, its four
pilot values become the reference set against which WO-2 and WO-3 are judged.

## Hypothesis and falsifier

**Hypothesis:** ADR 0020 step P1 changes no modelling. Storing each user's sequence once and
cutting the 50-movie window at batch time gives the same examples in the same order. At seed 42 on
CPU the restored loop should therefore produce bit-identical weights and identical metrics to the
loop at `89520be^`.

**Falsified when:** the seed-42 weights digest or any of the six @500 metrics differs from the
control's. That stops WO-1, and seeds 7, 13 and 21 do not run.

## Baselines and controls

| Model/control | Why it is required | Run/spec reference |
|---|---|---|
| Old loop from git (`89520be^` plus the recorded O-25 partition patch), seed 42 | Ground truth for equivalence | `wo1-old-loop-control-6pct.json`, run `3e031fa2046b42b4a1f50fc6e91fcc71` |
| Previous reference pilots 0.3186 / 0.3103 / 0.3258 / 0.2957 | Superseded: their split read the sealed window, and they predate PR #162. They are compromised and are not compared | `docs/results.md`; sealed-test memo, 2026-10-05 |
| Full-data v1 `528b1451…` / model `a11af5ed…` | The quality reference this objective produced. No full run in WO-1 | `full.json` |

## Changed and fixed axes

**Changed:**
- The example storage: the copied `(n_examples × 50)` table is replaced by stored sequences
  windowed per batch. This is the only difference between the control and the s42 run.
- The training seed: 42, 7, 13 and 21, for the reference set.

**Fixed:**
- **Cell.** `pilot6-bce-neg32` from pilot-6pct.json:
  - hidden 64, 2 blocks, 2 heads, feed-forward 256, dropout 0.2, L = 50;
  - BCE, with 32 uniform negatives from the per-example sampler;
  - batch 512, Adam 1e-3, 2 epochs.
- **Data.** The 6% user subsample (`SUBSAMPLE_SEED` 42, independent of the training seed), cut at the
  full split's boundaries (O-25). Threshold 10, exclusions, K = 500.
- **Process.** CPU, `OMP_NUM_THREADS=1` and `KMP_DUPLICATE_LIB_OK=TRUE` on all five runs.

## Protocol identity

| Field | Value |
|---|---|
| DVC/raw revision | `data/raw/ml-25m.dvc`, md5 `c3ce6309f6f0ec347a9e0a662c640021.dir`; fresh GroupLens download, `dvc status` clean |
| Derived snapshot | 6% user subsample (9,752 users, 1,517,399 ratings, `SUBSAMPLE_SEED` 42), cut at the full split's cutoff; train frame sha256 `93ee9a20a5883df784176772732cf25ff27e453d0aa5998deb395d6afe81c5a6` (pandas row hash) |
| Train cutoff / holdout window | 1466837397 / [1466837397, 1469256597), the full split's (O-25); train 1,202,444 rows, holdout 6,604 rows |
| Rolling window(s) | none (w0 only) |
| Partition(s) read | see the partition declaration below |
| Label contract | every holdout rating in the 28-day window, set per user |
| Catalog fingerprint | items in the subsample's train split; `sasrec_vocabulary_sha256` tag on each run |
| Cold threshold / routing | 10 / popularity below 10, SASRec at 10 or above; 108 warm / 39 cold users |
| Exclusion policy | the user's full training history |
| Stage / K | retrieval / 500 |
| Sequence contract | oldest-to-newest, left-zero padding, L = 50, strict prefix (equal-timestamp items never in each other's prefix); 1,186,847 examples |
| Protocol hash | **`sha256:faf2828d08a0b0ecf23993fcfaf037134359017e20c7601b53da7e2ebecc22bc`**, the new 6% pilot protocol. It replaces `090985d7…`, the contaminated split. Identical on all five runs |

## Partition declaration

| Field | Value | How a reviewer checks it |
|---|---|---|
| Partition(s) read | `holdout` (the full split's 28-day window, restricted to the 6% users) | `split.test` is built by `temporal_split` and never used by the trainer |
| Owner unseal approval | not applicable | — |
| Sealed boundary this run used | `holdout_end` = 1469256597 (2016-07-23 06:49:57 UTC), derived from the full 25M frame | restored-loop runs log `sealed_boundary_timestamp` and `holdout_end_timestamp` = 1469256597 |
| Feature source and its as-of | none. Raw ratings, strict prefix | — |
| Latest event timestamp that entered fitting | 1466819964 (2016-06-25) | `latest_fit_timestamp`; below 1469256597 |
| Latest event timestamp that entered scoring | 1469247943 (2016-07-23 04:25:43 UTC) | `latest_scored_timestamp`; below 1469256597 |

**First preflight, 2026-10-05, before O-25: FAILED, and nothing ran.** The 6% subsample computed its
own cutoff, **1471288304**, which is 23.5 days *after* the boundary; its `holdout_end` was
1473707504. Its train held 4,870 rows at or after 1469256597, all inside WO-8's window
`[1469256597, 1471675797)`. Its holdout's 7,528 rows were all at or after the boundary, and 1,126 of
them were inside that window. Every 6% pilot before O-25 trained and scored on that split. The scope
is recorded in
[`../memos/sealed-test-and-dataset-policy.md`](../memos/sealed-test-and-dataset-policy.md)
(2026-10-05).

**Second preflight, after O-25: PASSED.** Both worktrees ran the same script:
- cutoff 1466837397 and `holdout_end` 1469256597;
- identical train frame hash;
- protocol hash `faf2828d…`;
- latest fitted and scored timestamps as in the table above;
- examples byte-identical between the control's copied builder and the new store: 1,186,847 × 50,
  sha256 `62cc7e7af8ba3a48677c5bab417bb403cc46624e706ec87e301426568af4ef78`, with identical
  `SequenceExampleStats` (841,262 truncated examples, 262,417,767 omitted interactions).

**Affirmation.** Claude (WO-1 implementer), 2026-10-05: the five runs in this record read no
interaction at or after 1469256597, as the logged latest-fit and latest-scored timestamps show.

### How both arms got the same partition (O-25 control choice)

The control runs the real `89520be^` code with a minimal, recorded patch of the same cut. I chose
this over running the test-suite copy of the old loop through a new harness, for three reasons:
- the control stays the code git holds, including its own `run_once`, FAISS evaluation and export;
- it needs no new tooling;
- the patch touches only the partition, never the loop.

The patch, applied in a detached worktree at `89520be^`:

- **`src/data/split.py`:** byte-for-byte the WO-1 branch's change. `temporal_split` gains an
  optional keyword `cutoff`, and `temporal_cutoff` is added. `split.py` is identical at `89520be^`
  and at `main`, so the hunk applies unchanged.
- **`src/training/sasrec.py`:** three lines.

```diff
-from src.data.split import temporal_split
+from src.data.split import temporal_cutoff, temporal_split  # O-25 control patch
@@ def run_once(
+    full_cutoff = temporal_cutoff(ratings)  # O-25 control patch
     if sample_fraction != 1.0:
         ratings = subsample_users(ratings, sample_fraction, SUBSAMPLE_SEED)
-    split = temporal_split(ratings)
+    split = temporal_split(ratings, cutoff=full_cutoff)  # O-25 control patch
```

Before training, the examples were checked byte-identical in both arms (second preflight above).

## Cohorts and slices

Natural warm/cold/overall only (108 warm / 39 cold). A pilot has no synthetic cohort. The per-user
recall export is logged for the bootstrap.

## Metrics

**Primary (equivalence, control against s42):**
- The encoder-weights digest (`encoder_weights_sha256`: names, dtypes, shapes, raw bytes) must be
  equal.
- All six @500 metrics must be exactly equal.
- The per-user recall export must be identical.

**Reference set:** warm recall@500 at seeds 42, 7, 13 and 21.

**Guardrail:** cold recall must be identical across all runs, since popularity routes the cold users.

**Diagnostics:** `fit_seconds`; peak RSS (`fit_peak_rss_bytes`, `peak_rss_bytes`, from
`resource.getrusage`); `training_example_store_bytes`.

**Evaluation index:** the control scores with FAISS `IndexFlatIP` (as `89520be^` did) and the
restored loop with exact torch top-k. With the weights equal, every metric and every per-user value
also came out equal, so the two indexes agreed on this population.

## Grid and seeds

| Cell | Changed fields | Seed | Advance/stop rule |
|---|---|---|---|
| `wo1-oldloop-pilot6-bce-neg32-s42` (at `89520be^` plus the O-25 patch) | none (control) | 42 | — |
| `wo1-pilot6-bce-neg32-s42` | data path | 42 | must equal the control, or WO-1 stops. **Equal** |
| `wo1-pilot6-bce-neg32-s{7,13,21}` | data path, seed | 7, 13, 21 | ran only after s42 matched; recorded as they came out |

## Compute and storage budget

- **Hardware and environment:** the laptop CPU (Apple silicon, 36 GiB), `OMP_NUM_THREADS=1`,
  `caffeinate -i`. An Alembic migration was running against the shared Postgres in the background;
  it is I/O-bound. Load averages at each run's start:

  | Run | 1 / 5 / 15 min |
  |---|---|
  | control | 2.53 / 4.29 / 4.50 |
  | s42 | 1.93 / 2.60 / 3.39 |
  | seeds | 1.45 / 1.89 / 2.31 |

- **Wall-clock:** limit 54 min per run, enforced by a watchdog. Measured:

  | Run | Fit time | Wall time |
  |---|---:|---:|
  | control | 1,207.4 s | 20 min 31 s |
  | s42 | 1,080.8 s | 18 min 21 s |
  | s7, s13, s21 (one sweep process) | 1,080.4 s, 1,088.9 s, 1,099.0 s | 54 min 39 s in total |

- **Peak RSS:** restored loop 3.04 GB (2.83 GiB) in the s42 process, and 2.92 GB (2.72 GiB) in the
  three-seed process; each was measured after fit and at the end of the run. The
  example store is 23,799,328 bytes, against 237,369,400 bytes for the copied table it replaces. The
  control at `89520be^` predates the peak-RSS logging.
- **Spend:** none.

## Pre-run correctness checklist

- [x] Governing ADR is approved for this work (ADR 0020 decision note 2026-09-15, brief WO-1, O-25).
- [x] Protocol fingerprint is generated and identical across all five runs (`faf2828d…`). It is new
      by design under O-25, so there is no earlier baseline for it to match.
- [x] Temporal/equal-time leakage tests pass (`tests/unit/test_sequence_data.py`).
- [x] Model-specific correctness gate passes (`tests/unit/test_sasrec_trainer_of_record.py`).
- [x] Baseline and control implementations are fixed (`89520be^` plus the recorded patch;
      branch commit `ec3196d`).
- [x] Stop rule and compute cap are approved (brief WO-1, coordinator, 2026-10-05).
- [x] The partition declaration's pre-run rows are filled in; the second preflight passed.
- [x] The feature source is point-in-time per row (raw ratings, strict prefix).
- [x] Output paths and MLflow experiment are known (see Deviations).
- [x] `pgrep -fl src.training` printed nothing before each run.

## Commands

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WO1=$MAIN-wt-wo1
CTRL=$MAIN-wt-wo1-control
PY=$MAIN/.venv/bin/python
RUN_ENV="OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE MLFLOW_ALLOW_FILE_STORE=true \
  MLFLOW_TRACKING_URI=file://$MAIN/mlruns \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m SASREC_ARTIFACT_DIR=$MAIN/artifacts/sasrec"

# Control: 89520be^ plus the O-25 patch above, in a detached worktree.
git -C $MAIN worktree add --detach $CTRL 89520be^   # then apply the patch
cd $CTRL && caffeinate -i env $RUN_ENV $PY -m src.training.sasrec_sweep \
  $WO1/docs/experiments/sasrec/wo1-old-loop-control-6pct.json > $MAIN/artifacts/sasrec/logs/wo1-control-s42.log 2>&1
# Restored loop, seed 42, then (after the comparison matched) seeds 7, 13 and 21.
cd $WO1 && caffeinate -i env $RUN_ENV $PY -m src.training.sasrec_sweep \
  docs/experiments/sasrec/wo1-restored-loop-6pct-s42.json > $MAIN/artifacts/sasrec/logs/wo1-restored-s42.log 2>&1
cd $WO1 && caffeinate -i env $RUN_ENV $PY -m src.training.sasrec_sweep \
  docs/experiments/sasrec/wo1-restored-loop-6pct-seeds.json > $MAIN/artifacts/sasrec/logs/wo1-restored-seeds.log 2>&1
```

## Deviations

- **MLflow store.** The runs went to a local file store (`$MAIN/mlruns`), not to the shared server
  at `localhost:5001`.
  - **Why not the server:** it runs with `--default-artifact-root /mlartifacts`, a container volume.
    So `phase-2-candidates` has the artifact location `/mlartifacts/1`, which the host client cannot
    write. `run_once` logs artifacts before metrics, so every run would have failed after training.
    This is the same failure as attempt `833812ee…` on 2026-09-11.
  - **First attempt at a local store:** the control's first launch used a file store under
    `artifacts/mlflow-wo1`. It failed in one second at `start_run`, before any split or training,
    because MLflow 3.15's file store refuses run directories whose path contains a folder named
    `artifacts`. That empty store is kept at `artifacts/mlflow-wo1-failed-attempt-artifacts-path`,
    with log `artifacts/sasrec/logs/wo1-control-s42-failed-mlflow-path.log`.
  - All five runs are FINISHED in that store, and each carries its `model/` and
    `per_user_recall.json` artifacts. They can be imported into the shared server later.
  - The coordinator's 2026-10-05 note says the shared server, since recreated with
    `--serve-artifacts`, failed artifact uploads for experiments created earlier that day. It does not
    apply to these runs: none of them used the server. No WO-1 pilot started after that note, so
    none needed a new `-phase-a` experiment name.
  - The durable records are the local archives under `artifacts/sasrec/<run-id>/` and the weights
    digests listed below.
- **Control patch.** The control is `89520be^` plus the recorded O-25 partition patch, as described
  above.

## Expected artifacts

- **MLflow file store** `$MAIN/mlruns`, experiment `phase-2-candidates`. Each run has params, the
  six @500 metrics, `per_user_recall.json` and the protocol envelope. The restored-loop runs also
  carry `sasrec_weights_sha256`, peak RSS and the partition timestamps.
- **Model archives:** `$MAIN/artifacts/sasrec/<run-id>/`.
- **Logs:** `$MAIN/artifacts/sasrec/logs/wo1-*.log`.
- **Results entry:** `docs/results.md`, "SASRec trainer of record restored (WO-1)".

## Verdict

**Validity:** valid. All five runs ran on protocol `faf2828d…`, the O-25 partition.

**Partition affirmation:** intact. Latest fitted timestamp 1466819964 and latest scored timestamp
1469247943, both below 1469256597.

**Decision:** advance. The restored loop on the P1 data path is the trainer of record.

**Rule application (equivalence, control `3e031fa2…` against s42 `7baeb7d0…`):**

| | control (old loop) | s42 (restored loop) | |
|---|---|---|---|
| encoder weights digest | `sha256:86e112c75929763e78dae3b4b820237aeca0837c9de6187008cf5930f9afa8ec` | same | **equal** |
| epoch losses | 0.09626240980758807, 0.06954592609165197 | same | **equal** |
| warm recall@500 | 0.36254870756392454 | 0.36254870756392454 | **equal** |
| warm NDCG@500 | 0.13540066428140057 | 0.13540066428140057 | **equal** |
| cold recall@500 | 0.5427033422192526 | 0.5427033422192526 | **equal** |
| cold NDCG@500 | 0.4341170760032304 | 0.4341170760032304 | **equal** |
| overall recall@500 | 0.41034483512554226 | 0.41034483512554226 | **equal** |
| overall NDCG@500 | 0.2146519571871922 | 0.2146519571871922 | **equal** |
| per-user recall export | — | — | **identical** |

The archives' own SHA-256s differ (`9213b61f…` and `b63a6c5a…`). That is expected: the new archive's
metadata carries `training_objective`, which is why the weights digest is the comparison.

**Reference set (warm recall@500, protocol `faf2828d…`):**

| Seed | Run | Warm recall@500 | Warm NDCG@500 | Cold recall@500 | Overall recall@500 |
|---:|---|---:|---:|---:|---:|
| 42 | `7baeb7d0b812486e9c86fdd3435ea715` | 0.3625487076 | 0.1354006643 | 0.5427033422 | 0.4103448351 |
| 7 | `d71f0fa6321d4fd6bf3288ee9c28e710` | 0.3611061547 | 0.1307310996 | 0.5427033422 | 0.4092850003 |
| 13 | `2d9f3cc1ed1943b495fbecc7ac101485` | 0.3714076606 | 0.1253087969 | 0.5427033422 | 0.4168534537 |
| 21 | `8668ca0c57f6411dacd0d53af0b8b2fa` | 0.3936318283 | 0.1483434889 | 0.5427033422 | 0.4331814136 |

**Warm recall@500 across the four seeds:**
- mean **0.372174**;
- sample standard deviation 0.015013;
- range 0.361106 to 0.393632, which is **8.74%** relative.

**What the spread means.** There are 108 warm users, so one user is 0.93% of the slice. A relative
range of 8.74% is a handful of users moving, and this is a correctness-and-direction surface, not a
place for final results.

**Other checks:**
- Cold recall@500 is 0.5427033422 on all five runs, as the popularity routing requires.
- The four weight digests are distinct, so the seed reaches the model:
  - s7: `1995ea69…`
  - s13: `b3c4d1f7…`
  - s21: `2db2b3bd…`
  - s42: `86e112c7…`

**Superseded values.** These four values replace 0.3186 / 0.3103 / 0.3258 / 0.2957, which had mean
0.3126 and a range of 9.65%. They are not comparable: the old values were measured on the
contaminated split (protocol `090985d7…`), before PR #162.

**Runs:** control `3e031fa2046b42b4a1f50fc6e91fcc71`; restored s42
`7baeb7d0b812486e9c86fdd3435ea715`; restored s7 `d71f0fa6321d4fd6bf3288ee9c28e710`, s13 `2d9f3cc1ed1943b495fbecc7ac101485`, s21 `8668ca0c57f6411dacd0d53af0b8b2fa`.

**What is not authorized next:**
- No full-data run.
- No change to any gate threshold or to the champion.
- No comparison of these values with the September 6% numbers: those read the sealed window.

WO-2 and WO-3 judge their pilots against this reference set, on protocol `faf2828d…`.

## Run data

Added 2026-10-07 under non-negotiable 12 (run preservation); the run-by-run table is [`run-ledger-2026-10-05..07.md`](run-ledger-2026-10-05..07.md).

- **Shared store:** `http://localhost:5001`, experiment `phase-2-candidates` (id 1), runs
  `3e031fa2046b42b4a1f50fc6e91fcc71`, `7baeb7d0b812486e9c86fdd3435ea715`,
  `d71f0fa6321d4fd6bf3288ee9c28e710`, `2d9f3cc1ed1943b495fbecc7ac101485` and
  `8668ca0c57f6411dacd0d53af0b8b2fa`. Imported with their original ids from `$MAIN/mlruns` on
  2026-10-05 and re-verified against that store on 2026-10-07 (5 of 5, per-user files by SHA-256).
- **Artifacts:** in the store's volume under `1/<run id>/artifacts/`; working copies in
  `artifacts/sasrec/<run id>/` (byte-identical) and the logs in `artifacts/sasrec/logs/wo1-*.log`.
- **Backup:** `kudratsingh/movielens-backups` commit `b6f92ac418f58600aa60fb56227c86495e66ceb4`,
  `wo1-runs-2026-10-05.tgz` (the original file store with archives and per-user files, the four
  console logs, the cells JSONs); run metadata also in the post-catch-up dump, commit `531f62ec7e8b297b8ae1abcd04ca758e48064a7a`.
- Nothing on rule 1's list is missing for these runs.
