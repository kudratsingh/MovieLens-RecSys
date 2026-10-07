# Experiment: SASRec / cell 0b at ten passes on the all-positions trainer / wo3-cell0b-10pass-v1

**Status:** measured.
- **Seed 42** passed its declared read: 0.4343 at pass 10, within 0.03 of strict-prefix's 0.4564.
- **Item (e) ran on both trainers and fails its check.** The all-positions trainer's four-seed mean
  at a fixed 10 passes is 0.4095. The strict-prefix trainer's is 0.4513. That is −9.27%, outside 3%.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), amendments 2026-10-05 (D4) and 2026-10-07
(WO-3's checks at the WO-5 loss; the 0.03 single-seed noise rule).

**Owner approval:** 2026-10-07, relayed by the Phase A coordinator: "cell 0b on the all-positions
trainer, seed 42, early stopping off, 10 passes." The owner's binding conditions:
1. **The read** is warm recall@500 at pass 10, declared in the cells file before the run. Every pass
   is logged; the best pass is not picked.
2. **If it passes** (within 0.03 of strict-prefix `9b0d4994`, 0.4564) and item (e) runs, the fast
   trainer's seeds 7, 13 and 21 use the same fixed 10 passes. The strict-prefix seeds keep WO-4's
   rule (early stopping, 3 to 5 passes), as `9b0d4994` ran.
3. **If the curve peaks before pass 10 and then falls by more than 0.03**, the thread stops after the
   run and the per-pass numbers go to the coordinator before (e).
4. **WO-5 pricing** for the fast trainer uses its real pass count and proposes an early-stopping rule
   that one probe user cannot trigger (see the last section; the pricing itself is a later step).

Also under the owner's run-preservation rule (2026-10-07): every run is logged to the one shared
MLflow store with its full record, verified through the API.

**Specification version:** wo3-cell0b-10pass-v1 (2026-10-07). The cells, each committed before its
run:
- [`wo3-allpos-cell0b-10pass-6pct-s42.json`](../../experiments/sasrec/wo3-allpos-cell0b-10pass-6pct-s42.json);
- item (e): [`wo3-allpos-cell0b-10pass-6pct-seeds.json`](../../experiments/sasrec/wo3-allpos-cell0b-10pass-6pct-seeds.json)
  and `wo3-strictprefix-cell0b-6pct-s{7,13,21}.json`, held until seed 42's read passed, then released
  in commit `c3fc4ec`.

## Decision this experiment informs

Whether the all-positions trainer, given the passes it needs, matches the strict-prefix trainer at
the loss WO-5 trains with. If it does, the larger cells can train on it at a fraction of the cost.

## Hypothesis and falsifier

**Hypothesis.** WO-3's first pilots found the all-positions trainer needs about twice the passes of
the copied-prefix trainer for the same recall. Cell 0b's earlier all-positions run (`1fbde873`) was
stopped at pass 3 by early stopping, one probe user falling, with holdout recall still rising. Given
10 fixed passes, it should land within 0.03 of strict-prefix at seed 42, and its four-seed mean
within 3% of the strict-prefix four-seed mean.

**Falsified when** the pass-10 read misses 0.03, or the four-seed check misses 3%.

## Baselines and controls

| Model/control | Why | Reference |
|---|---|---|
| Strict-prefix cell 0b, seed 42, WO-4 early stopping (ran 5 passes) | The comparison | `9b0d499430474fde9de6a5305bcda649`, warm recall@500 0.4563981551 |
| Strict-prefix cell 0b, seeds 7, 13, 21, same rule | Item (e)'s strict-prefix mean | this record |
| All-positions cell 0b with early stopping (stopped at pass 3) | The run this one replaces as the comparison | `1fbde8732ab540cf85cd672744acd9e7`, 0.3817862300 |

## Changed and fixed axes

**Changed against `1fbde873`:** early stopping off and exactly 10 passes. **Fixed:** cell 0b's
settings (hidden 64, 2 blocks, 2 heads, feed-forward 256, dropout 0.2, L = 50; sampled softmax with
1,024 negatives; 512 targets per step; Adam 1e-3); the all-positions objective
(`all-positions-strict-timestamp-v1`, both WO-3 settings off); seed 42; CPU; O-25's 6% partition.

## Protocol identity

| Field | Value |
|---|---|
| Raw data revision | `md5:c3ce6309f6f0ec347a9e0a662c640021.dir` (from the run's `evaluation_protocol` tag) |
| Derived snapshot hash | `sha256:d469ff0016b91c8db424d93ce93f8a075d92a2745987de07bb617ec7819e8363` |
| Catalog fingerprint | `sha256:e2117c4ea9442df513460cd7bba9798a59664e7e01134d0d3ae476adee280b78` |
| Train cutoff / holdout window | 1466837397 / [1466837397, 1469256597) |
| Users | 108 warm / 39 cold |
| Stage / K | retrieval / 500 |
| Protocol hash | `sha256:faf2828d…`, confirmed on every run before any number was read |

## Partition declaration

| Field | Value |
|---|---|
| Partition(s) read | `holdout` (the full split's 28-day window, restricted to the 6% users) |
| Owner unseal approval | not applicable |
| Sealed boundary this run used | 1469256597, logged as `sealed_boundary_timestamp` |
| Feature source | raw ratings, strict timestamp prefix |
| Latest event timestamp that entered fitting | 1466819964 (seed 42) |
| Latest event timestamp that entered scoring | 1469247943 (seed 42) |

**Affirmation.** Claude (WO-3 implementer), 2026-10-07: seed 42 read no interaction at or after
1469256597, as its logged timestamps show. (Item (e)'s runs are affirmed in the verdict.)

## Seed 42: the declared read

Run `02060962064d41fd95e37c8941a596c5` (`phase-a-sasrec`, git `057a8ab`). Warm recall@500 by pass,
every pass as logged:

| Pass | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Warm recall@500 | 0.2610 | 0.3452 | 0.3712 | 0.4033 | 0.4110 | 0.4125 | 0.3736 | 0.4344 | 0.4094 | **0.4343** |
| Train loss | 5.158 | 4.662 | 4.384 | 4.206 | 4.102 | 4.012 | 3.918 | 3.829 | 3.756 | 3.697 |
| Pass seconds | 85.2 | 85.7 | 85.6 | 82.3 | 84.6 | 80.1 | 88.1 | 81.5 | 79.9 | 79.6 |

- **The read:** 0.4343017113 at pass 10, against 0.4563981551: **−0.0221, inside 0.03. Passes.**
- **Condition 3 does not fire.** The curve peaks at pass 8 (0.4344376606); pass 10 is 0.0001 below
  it. The curve is noisy, though: pass 7 fell 0.039 below pass 6 and pass 9 fell 0.025 below pass 8,
  each recovering at the next pass, while the loss fell every pass. On 108 warm users one user is
  0.0093 of recall.
- Warm NDCG@500 0.1379652, cold recall@500 0.5427033422 (the guardrail), overall 0.4630613,
  catalog coverage 30.6%.
- **Cost:** fit 849.4 s against 900 projected; wall 14 min 20 s against 16; peak RSS 2.44 GB. No
  other training process; load 4.94 at the start from other workers' store imports.
- Against strict-prefix `9b0d4994`'s 3,593.6 s fit for 5 passes, the fast trainer's 10 passes took
  24% of the time.

## Item (e): four seeds on both trainers

Run under the owner's condition 2:
- the all-positions trainer at the same fixed 10 passes as seed 42;
- the strict-prefix trainer under WO-4's rule (early stopping, 3 to 5 passes), as `9b0d4994` ran.

The strict-prefix seed 42 is `9b0d4994` itself. Every run used protocol `faf2828d…`, confirmed before
any number was read, with cold recall@500 0.5427033422 (the guardrail) and latest fit 1466819964 /
latest scored 1469247943.

| Trainer | Seed | Run | Read: warm recall@500 | Passes | Fit s | Peak RSS |
|---|---:|---|---:|---:|---:|---:|
| all-positions, 10 fixed passes | 42 | `02060962064d41fd95e37c8941a596c5` | 0.4343017113 | 10 | 849.4 | 2.44 GB |
| all-positions, 10 fixed passes | 7 | `b15c819402f144398fb3d2ce19207a12` | 0.3831199735 | 10 | 1,284.4 | 1.81 GB* |
| all-positions, 10 fixed passes | 13 | `d0c67983f7984e3fabde2996d8484dff` | 0.4084903737 | 10 | 1,238.9 | 1.81 GB* |
| all-positions, 10 fixed passes | 21 | `21e9473e8b3c43bdbd62cb65dca71698` | 0.4119432095 | 10 | 1,213.8 | 1.81 GB* |
| strict-prefix, early stopping | 42 | `9b0d499430474fde9de6a5305bcda649` (WO-4) | 0.4563981551 | 5 | 3,593.6 | 2.60 GB |
| strict-prefix, early stopping | 7 | `dc8a5b1912b9400e9107c3d00821dbfe` | 0.4487600607 | 3 (stopped early) | 3,076.8 | 2.77 GB |
| strict-prefix, early stopping | 13 | `436535462fcb416fadc5bc3120fe1b87` | 0.4558290488 | 5 | 4,924.1 | 2.14 GB |
| strict-prefix, early stopping | 21 | `13218e76109042e1ae52c51e9410884a` | 0.4442167286 | 5 | 3,978.9 | 1.95 GB |

\* The three all-positions seeds ran in turn in one process (09:41–10:43 UTC), so the peak RSS is that
process's.

**Every pass, warm recall@500** (the read is the last column of each row; no pass was picked):

| Run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all-positions s42 | 0.2610 | 0.3452 | 0.3712 | 0.4033 | 0.4110 | 0.4125 | 0.3736 | 0.4344 | 0.4094 | **0.4343** |
| all-positions s7 | 0.2381 | 0.3471 | 0.3892 | 0.4211 | 0.3958 | 0.4024 | 0.4056 | 0.3887 | 0.4310 | **0.3831** |
| all-positions s13 | 0.2499 | 0.3556 | 0.3585 | 0.3784 | 0.4073 | 0.3976 | 0.4059 | 0.4144 | 0.4179 | **0.4085** |
| all-positions s21 | 0.2383 | 0.3720 | 0.3830 | 0.3738 | 0.3940 | 0.4206 | 0.4483 | 0.4222 | 0.3741 | **0.4119** |
| strict-prefix s42 | 0.3496 | 0.4191 | 0.4493 | 0.4608 | **0.4564** | | | | | |
| strict-prefix s7 | 0.3627 | 0.3950 | **0.4488** | | | | | | | |
| strict-prefix s13 | 0.3657 | 0.4400 | 0.4350 | 0.4490 | **0.4558** | | | | | |
| strict-prefix s21 | 0.3733 | 0.4216 | 0.4289 | 0.4373 | **0.4442** | | | | | |

**The four-seed check.**

| | Four-seed mean | Sample sd | Range |
|---|---:|---:|---|
| all-positions, 10 fixed passes | **0.4094638170** | 0.0209604 | 0.3831–0.4343 |
| strict-prefix, early stopping | **0.4513009983** | 0.0058631 | 0.4442–0.4564 |

- **The difference** is −0.0418372, **−9.27% relative**. The pass mark was 3%, a floor of
  0.4377620. **Item (e) fails.**
- **Seed 42 was the fast trainer's best seed.** Its −0.0221 does not hold across seeds. Every other
  seed is further below, by −0.0656, −0.0473 and −0.0323 against the same-seed strict-prefix run.
- **The fast trainer is much noisier.** Its seed sd at a fixed 10 passes is 3.6 times the
  strict-prefix trainer's.
- **Its curves do not settle by pass 10.** Each all-positions curve swings by 0.04–0.05 between
  adjacent passes late in training:
  - s7 fell 0.048 from pass 9 to pass 10;
  - s21 fell 0.074 from pass 7 to pass 9.
  So the pass-10 read samples a moving curve. The strict-prefix curves rise almost monotonically to
  their stopping pass.
- **Condition 3.** Its pattern, a peak before pass 10 followed by a fall of more than 0.03, appears
  in two (e) seeds: s7 (0.048 below its pass-9 peak) and s21 (0.036 below its pass-7 peak). The
  condition gated (e) on seed 42, which did not show it. It was reported to the coordinator when s7
  landed, and (e) completed as approved.

**What the check does not license.** No best-pass or smoothed read is reported as a result. The
owner's condition 1 fixed the read at pass 10, and a different read chosen after seeing the curves
would be picking.

## Machine-time tally (fit seconds)

| Run | Projected | Actual | Note |
|---|---:|---:|---|
| Seed 42, all-positions, 10 passes | 900 s | 849.4 s | solo |
| All-positions s7 / s13 / s21 | about 900 s each solo | 1,284.4 / 1,238.9 / 1,213.8 s | one process, beside the strict-prefix seeds |
| Strict-prefix s7 / s13 / s21 | about 3,600 s each solo; killed past 150 min | 3,076.8 (3 passes) / 4,924.1 / 3,978.9 s | up to three of mine in flight; load up to 7.8 from other workers |
| **Total** | | **16,566.3 s (4.60 h)** | of which 3,737.1 s fast-trainer seeds and 11,979.8 s strict-prefix seeds |

No run came near its watchdog.

## The WO-5 pricing note (owner's condition 4)

Recorded for the later pricing step, which this record does not do:
- **Real pass count.** The all-positions trainer needs about twice the passes the strict-prefix
  trainer needs for the same recall: 4 against 2 on the v1 cell (WO-3 pilot 4), and 10 here against
  WO-4's 3 to 5 at cell 0b. Any WO-5 price for it uses about double the strict-prefix pass count, at
  its own per-pass cost (here about 83 s per pass at 6%, against about 690 s for strict-prefix).
- **An early-stopping rule one probe user cannot trigger.** WO-4's rule stops when probe recall@500
  fails to improve by 0.5% relative over the best earlier pass, after at least 3 passes. On the 6%
  pilot the probe holds 82 users, so one user is 1.2% of probe recall, and one user's movement
  stopped `1fbde873` at pass 3 while holdout recall was still rising. A proposal for WO-5, not yet
  adopted:
  - require the non-improvement to persist for **two consecutive passes** (patience 2) before
    stopping;
  - and require the shortfall to exceed what one probe user can move (stop only if the best earlier
    pass beats the current one by more than `1 / n_probe_users`, besides the 0.5% relative rule);
  - and set the minimum to the trainer's own pass count where it is known (for the all-positions
    trainer, about twice the strict-prefix minimum).
  At full data the probe holds about 1,600 users (1% of training users), where one user is 0.06%,
  so the second clause matters mainly at pilot scale.

## Run data

- **Store:** `http://localhost:5001`, experiment `phase-a-sasrec` (id 6), artifacts
  `mlflow-artifacts:/6`.
- **Seed 42:** run `02060962064d41fd95e37c8941a596c5`. Artifacts: `per_user_recall.json`,
  `model/sasrec-model.zip` (SHA-256 `3157a36348e031789b36a6991e2dd0b6f2e33e6c8841f30286c7e44384487d41`,
  read back from the store and matching its tag), `model/sasrec-manifest.json`,
  `model/popularity-order.json`, `training/loss_curve.png`, `logs/wo3-cell0b-10pass-s42.log`,
  `config/wo3-allpos-cell0b-10pass-6pct-s42.json`. Weights digest
  `sha256:731940ea31988e3427464c4f33e38ea7a19f47288b2f2f6c1bbed6e6b739f519`.
- **Item (e):** runs `b15c819402f144398fb3d2ce19207a12`, `d0c67983f7984e3fabde2996d8484dff`,
  `21e9473e8b3c43bdbd62cb65dca71698` (all-positions s7/s13/s21) and
  `dc8a5b1912b9400e9107c3d00821dbfe`, `436535462fcb416fadc5bc3120fe1b87`,
  `13218e76109042e1ae52c51e9410884a` (strict-prefix s7/s13/s21), all in `phase-a-sasrec`.
- **Each (e) run carries** the same artifact set as seed 42: per-user file, model archive (SHA-256
  read back from the store and matching its tag), manifest, popularity order, loss curve, console
  log and cells file. The SHA-256s are in each cells file's `result` block.
- **Logs:** `logs/wo3-e-fast-seeds.log` (the three all-positions seeds) and
  `logs/wo3-e-sp-s{7,13,21}.log`.
- **Local copies:** models under `artifacts/wo3/models/<run id>/` and logs under
  `artifacts/wo3/logs/` in the main checkout.
- **Backup, seed 42:** `kudratsingh/movielens-backups` commit
  `531f62ec7e8b297b8ae1abcd04ca758e48064a7a`, `wo3-10pass-run-2026-10-07.tgz`.
- **Backup, item (e):** `kudratsingh/movielens-backups` commit
  `1fc7e41fbc60ee9f55cc64f825e294a308613f34`. It holds `wo3e-runs-2026-10-07.tgz`: the six runs'
  `mlflow-run.json`, artifacts read back through the API, console logs and cells files, with a
  `MANIFEST.sha256`; text members were scanned for secrets with no match. It also holds
  `mlflow-post-wo3e-20261007T113834.sql.gz`, a `pg_dump` of the `mlflow` database taken after the last
  (e) run and before any clean-up.

**Verifying a run.** MLflow 3.15's client `list_artifacts` also calls a logged-models endpoint the
5001 server does not have (404). Runs were listed and read back through the plain REST endpoints
(`/api/2.0/mlflow/artifacts/list` and `/get-artifact`); uploads through the client work.

## Verdict

**Validity:** valid. All eight runs ran on protocol `faf2828d…`, confirmed before reading: the
seven new runs and WO-4's strict-prefix seed 42.

**Partition affirmation:** intact. Latest fit 1466819964 and latest scored 1469247943 on every run,
both below 1469256597. Claude (WO-3 implementer), 2026-10-07.

**Rule application:**
- **Seed 42's declared read passes:** 0.4343017113 against 0.4563981551, −0.0221.
- **Condition 3 did not fire on seed 42.**
- **Item (e) fails.** The all-positions trainer's four-seed mean at a fixed 10 passes (0.4094638)
  is 9.27% below the strict-prefix trainer's (0.4513010), outside 3%.

**What it means.**
- At cell 0b, 10 fixed passes do not make the all-positions trainer a substitute for the
  strict-prefix trainer at this check.
- Seed 42's pass was its best seed.
- Late in training its per-pass recall swings by up to about 0.05 on 108 warm users, so a
  fixed-pass read is a noisy sample of a curve that has not settled.

The fast trainer remains about 3.4 times cheaper per run in fit time: a mean of 1,147 s against
3,893 s here.

**Not an owner decision.** This verdict applies the pass check the owner set (ADR 0020's 2026-10-07
amendment). It decides nothing new, so it adds no row to the decision register. D-048 is annotated
that its run passed its read and (e) executed. Whether WO-5 prices the fast trainer at all, and
with which rule, stays with the owner. Condition 4's note above is the input.

**What is not authorized next:**
- no further WO-3 run;
- no full-data run;
- no gate, threshold, champion or serving change.
