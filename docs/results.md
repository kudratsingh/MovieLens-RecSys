# Offline results

Every number on this page was measured on one machine on one day, by running the
project's own training entrypoints against the full MovieLens 25M database. None
of it is copied from a paper, an estimate, or an earlier session. Where a run did
not finish, this file says so instead of reporting a partial number.

**Measured:** 2026-08-29
**Dataset:** MovieLens 25M, DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir` (1 156 670 716 bytes, 7 files) — the `md5` recorded in [`data/raw/ml-25m.dvc`](../data/raw/ml-25m.dvc)
**Cold-start cohort:** ADR 0011 `v1`, md5 `9e0c978eff3d45f0985e9e0bbe0551d7`, fingerprint `ae4475f0e063dd4b430092100491838737ee03c8554e68b78cc551efa2e6cfe2`
**Evaluation:** every metric comes from [`src/evaluation/`](../src/evaluation/) — the trainers were run, nothing was computed by hand (non-negotiable #5)

> **Later sessions appended to this page.** Everything down to "Caveats worth
> writing down" is the 2026-08-29 measurement and is left exactly as it was
> written. [The 2026-08-30 session](#2026-08-30--the-two-tower-finished-and-both-cold-start-routing-policies-were-run)
> adds two things that change how one section above should be
> read: **the two-tower did run to completion**, so "The two-tower did not run
> to completion" records that session's outcome, not the model's, and both
> cold-start routing policies were measured, so "Routing: what the cohort
> actually measured" now has a counterfactual beside it.
>
> [A second 2026-08-30 session](#2026-08-30-second-session--the-learning-rate-and-budget-sweep)
> at the bottom runs the learning-rate and budget sweep the first one said was
> the cheapest next experiment. It changes how that session's two-tower result
> should be read — **not the number, which stands, but its cause.** The flat
> loss curve there was not a model that had finished learning, and the sweep
> says what it was instead.

## The machine, and what it was doing at the time

| | |
|---|---|
| CPU | Apple M3, 8 cores (8 physical / 8 logical) |
| Memory | 16 GiB |
| OS | macOS 26.5.2 (build 25F84) |
| Python | 3.11.16 |
| Postgres | 16.15, in Docker (`linux/arm64`), the repo's `docker-compose.yml` stack |
| MLflow | server image `v2.13.0` with the Postgres backend store; client 3.15.1 |
| Libraries | numpy 2.4.6, pandas 2.3.3, scipy 1.17.1, implicit 0.7.3, lightgbm 4.7.0, torch 2.13.0, faiss-cpu 1.15.0 |

Every job ran with `nice -n 15`, thread caps of 4 on `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS` (1 for the
two-tower — see the caveats), one at a time, never concurrently.

**The host was shared and heavily loaded for part of the session** — an unrelated
workload held the 1-minute load average between 34 and 50 on an 8-core machine, and
these jobs were deliberately the ones yielding, so **every wall-clock below is an
upper bound, not a benchmark**. The 25M-row `pd.read_sql` every trainer opens with
shows the spread plainly: 464 s under load, 116 s for the same read once it cleared.
The metrics are unaffected: the popularity and item-item runs here are bit-identical
to separate runs made earlier the same day on the same data and seed — all 20 and all
38 logged metrics respectively, compared value by value, with no differences.
(One local detail, in case a log excerpt confuses: MLflow was published on host port
5001, because macOS Control Center holds 5000.)

## The data and the split

Read from Postgres, split by [`src/data/split.py`](../src/data/split.py) with no
random component anywhere (non-negotiable #1). Every trainer printed the same three
numbers:

| | Rows | Share |
|---|---:|---:|
| Train (`timestamp < T`) | 20,000,075 | 80.00% |
| Holdout (`[T, T + 28 days)`) | 129,683 | 0.52% |
| Test (reserved, untouched) | 4,870,337 | 19.48% |
| **Total** | **25,000,095** | **100.00%** |

`T = 1466837397` = 2016-06-25 06:49:57 UTC; holdout ends at `1469256597` =
2016-07-23 06:49:57 UTC. **2,641 users have holdout activity — 1,939 warm and 702
cold** at ADR 0001's `< 5 training interactions` threshold. The test slice was not
read by any run on this page.

The ADR 0011 cold-start cohort was attached to the training frame by every run:
2,000 synthetic users across history buckets {0, 1, 3, 10}, 7,000 history rows —
**0.035% of train** — and no synthetic user appears in holdout, so nothing in the
warm / cold / overall columns moves because of it. The parquet was regenerated from
scratch on this machine and reproduced the committed DVC md5
(`9e0c978eff3d45f0985e9e0bbe0551d7`) and cohort fingerprint exactly, which is the
determinism claim ADR 0011 makes, independently checked here.

## Phase 1 baselines — the recommender end to end, K = 10

MLflow experiment `phase-1-baselines`. Both models return a top-10 list per holdout
user and are scored by `src/evaluation/protocol.evaluate` at the default `K = 10`.
Slice sizes are the same for both: **1,939 warm users, 702 cold**.

| Model | Run | Warm recall@10 | Warm NDCG@10 | Cold recall@10 | Cold NDCG@10 | Overall recall@10 | Overall NDCG@10 |
|---|---|---:|---:|---:|---:|---:|---:|
| Popularity | `popularity-baseline` | 0.0163 | 0.0309 | 0.0638 | 0.4881 | 0.0290 | 0.1525 |
| CF / ALS | `cf-als-baseline` | **0.0338** | **0.0578** | 0.0638 | 0.4880 | **0.0418** | **0.1722** |

| Model | Run id | Config | Fit | Recommend | Wall-clock |
|---|---|---|---:|---:|---:|
| Popularity | `d18737c2180a42f28a0a6255fd00d02e` | 34,461 items ranked | 5.7 s | 0.1 s | 503.6 s |
| CF / ALS | `d961e6d9ba214edb9283266777aebf40` | 64 factors, 15 iterations, reg 0.01, seed 42; 139,383 × 34,461 | 177.8 s | 5.1 s | 310.8 s |

**On the warm slice — the only place these two policies actually compete — ALS
roughly doubles the baseline**: recall@10 0.0163 → 0.0338, NDCG@10 0.0309 → 0.0578,
over the same 1,939 users. In absolute terms it is still small: holdout averages 49
interactions per user (129,683 / 2,641), so a per-user recall of 0.034 is on the
order of one or two correct titles in ten slots. An overall NDCG@10 of 0.17 is what
"no learned re-ranking" looks like on this data, and every later stage is measured
against it.

**The cold columns are identical because they are the same policy.** `CFModel`
embeds the popularity fallback for users it has no factors for (ADR 0001), so 701
of the 702 cold users get literally the popularity list. The fourth decimal
differs only because one holdout user has between 1 and 4 training interactions —
enough to be in the ALS user index, not enough to be warm — and ALS serves that
single user instead.

### CF per-policy attribution

The overall row above mixes two policies, so the run also scores each policy over
the users it actually served:

| Policy | Users | recall@10 | NDCG@10 |
|---|---:|---:|---:|
| ALS-served | 1,940 | 0.0338 | 0.0579 |
| Popularity fallback | 701 | 0.0638 | 0.4885 |

**Read down this table and the fallback appears to beat the model — 8× on NDCG,
nearly 2× on recall. It does not; the two rows are different people.** The fallback
row is brand-new users whose 28-day holdout is their whole first burst of activity:
a large `len(relevant)` depressing recall, and an easy popular-film target inflating
NDCG. The ALS row is established users with a handful of holdout items each. Without
this split a policy difference reads as a model comparison; the comparison that *is*
valid is the warm row above, where the users are fixed and only the policy changes.

## Candidate stage — retrieval only, K_CANDIDATES = 500

MLflow experiment `phase-2-candidates`. These models retrieve 500 items and are
scored on whether the holdout items survived into that set — recall@500, per
[ADR 0003](adr/0003-two-stage-architecture.md). **These numbers are not comparable
with the K = 10 table above**; a different K answers a different question.

| Model | Run | Warm recall@500 | Warm NDCG@500 | Cold recall@500 | Cold NDCG@500 | Overall recall@500 | Overall NDCG@500 |
|---|---|---:|---:|---:|---:|---:|---:|
| Item-item cosine | `itemitem-cosine` | 0.4001 | 0.1392 | 0.5290 | 0.4392 | 0.4344 | 0.2190 |
| Two-tower | — | \* | \* | \* | \* | \* | \* |

\* **The two-tower did not produce a run.** See below; nothing is reported for it
because nothing was measured.

| Model | Run id | Config | Fit | Recommend | Wall-clock |
|---|---|---|---:|---:|---:|
| Item-item cosine | `65faeebb5e0545dcaba9ae703cc67af0` | `k_neighbors=200`; 139,383 × 34,461 | 22.5 s | 1.5 s | 172.3 s |

### Item-item per-policy attribution

| Policy | Users | recall@500 | NDCG@500 |
|---|---:|---:|---:|
| Item-item-served | 1,940 | 0.4003 | 0.1394 |
| Popularity fallback | 701 | 0.5288 | 0.4393 |

**Item-item's warm recall@500 of 0.4001 is the number ADR 0004 said it would supply.**
[ADR 0004](adr/0004-item-item-before-two-tower.md) deliberately left the two-tower's
promotion threshold unpinned — "the threshold itself is decided when the two-tower
lands (it depends on item-item's actual number)". This is the actual number, and it
is the incumbent a two-tower has to beat.

Read alongside the K = 10 table: **retrieval keeps roughly 40% of a warm user's
holdout items inside 500 candidates, and the ranker's job is to get some of them into
the top 10 that the CF baseline could only fill at 0.034.** Those two numbers
together are the case for the two-stage split — the recall the ranker has to work
with is an order of magnitude larger than anything a single top-10 model was
delivering.

### The two-tower did not run to completion

Both attempts are reported, because both are measurements.

**Attempt 1, at `OMP_NUM_THREADS=4` — the setting every other run on this page used —
crashed with a segmentation fault inside `libomp.dylib`.** 100 s of wall-clock, most
of it the data read; the fault landed about 18 seconds after `Fitting two-tower
model ...`, on the first parallel region the training loop opened. The stack is in
the caveats below. It left MLflow run `43fd9ddc51bc46a09a02953cf3aa32f3` with its 15
parameters and **zero metrics**.

**Attempt 2, at `OMP_NUM_THREADS=1` — the workaround — ran, and could not have
finished.** It loaded the data, attached the ADR 0011 cohort, materialised
**19,867,692 (history, positive) pairs across 139,383 users and 34,461 items**, and
started training. **Epoch 1 of 3 took 2,264 s — 37 min 44 s.** Three epochs alone
therefore need ≈ 6,790 s (113 minutes) before the FAISS index is built or a single
holdout user is scored, against a 5,400 s (90-minute) budget — so the budget was
decided by epoch 1's clock, not by the timer. The process was in fact terminated at
**3,599 s, one hour in and inside epoch 2**, by the harness running it, half an hour
short of a budget it was already going to miss. Configuration was ADR 0006's,
unchanged: embedding dim 64, batch 4,096, 3 epochs,
Adam at 1e-3, sampled softmax with log-uniform negative correction, FAISS IVF-Flat
(`nlist=100`, `nprobe=10`), seed 42. It left MLflow run
`5094b121799c43eeb8dd5bf6a4720c75` — same 15 parameters, and exactly one metric, the
epoch-1 mean loss of 10.3542. Both runs sit in `phase-2-candidates` with status
`RUNNING`, which is the honest record of what happened: a killed process never
closes its run, and neither has an evaluation metric to report.

**So ADR 0004's item-item-versus-two-tower comparison on the full dataset stays
open.** The incumbent's number is now on this page — warm recall@500 of 0.4001 — but
the challenger has not been measured against it. The obstacle is specific and worth
naming: the OpenMP collision forces the run single-threaded, and one core is not
enough for ~14,600 sampled-softmax steps in ninety minutes on this machine. A
plausible next step is to defer the `faiss` import in
`src/models/candidates/twotower.py` until after the training loop, so torch's OpenMP
runtime is alone in the process while the epochs run — but that is a code change that
needs its own PR to test, not a line in the one reporting the measurement.

## Ranker — the two-stage path end to end, K = 10

MLflow experiment `phase-2-ranker`. LightGBM LambdaRank over the item-item
candidate stage's 500 candidates, cut to a top-10.

| Run | Warm recall@10 | Warm NDCG@10 | Cold recall@10 | Cold NDCG@10 | Overall recall@10 | Overall NDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| `lgbm-lambdarank-itemitem-candidates` | 0.0394 | 0.0554 | 0.0793 | 0.5631 | 0.0500 | 0.1904 |

Run id `1d898b02fcc842b6a7283dc6eb9117ad`. LambdaRank, `num_leaves=63`,
`learning_rate=0.05`, `min_data_in_leaf=20`, `num_boost_round=200`, `lambda_l2=1.0`,
`seed=42`, 20 negatives per positive, positives sampled from the trailing 30 days of
train (limit 20,000). Wall-clock **283.9 s**: candidate fit 29.1 s, feature index
8.0 s, training-set build 19.9 s, ranker fit 3.5 s, and 151.5 s to rank all 4,641
users end to end.

### Against the baselines it is supposed to beat

Relative to CF/ALS, over the identical holdout:

| Slice | Metric | CF/ALS | Ranker | Relative |
|---|---|---:|---:|---:|
| Warm | recall@10 | 0.0338 | 0.0394 | **+16.5%** |
| Warm | NDCG@10 | 0.0578 | 0.0554 | **−4.2%** |
| Cold | recall@10 | 0.0638 | 0.0793 | +24.4% |
| Cold | NDCG@10 | 0.4880 | 0.5631 | +15.4% |
| Overall | recall@10 | 0.0418 | 0.0500 | +19.7% |
| Overall | NDCG@10 | 0.1722 | 0.1904 | +10.6% |

**The two-stage path finds more of the right titles but does not order the warm ones
any better.** Warm recall@10 rises 16.5% while warm NDCG@10 *falls* 4.2%. Against
ADR 0001's gate — "≥ +3% relative NDCG@10 on the holdout" — the overall figure clears
it comfortably at +10.6% and the warm slice fails it. ADR 0001 does not say which of
those the gate reads; it says the holdout, which is the overall number. That is worth
settling before Phase 4 automates the gate, because here the aggregate is carried by
the cold slice and hides a warm-slice regression — the mirror image of the
cold-start masking the per-slice reporting rule was written to prevent.

**The cold slice improves for a real reason.** A cold user's candidates come from
`ItemItemModel`'s popularity fallback, and LightGBM then re-ranks those 500 into a
top-10. So the cold column is not the popularity list any more; it is the popularity
list reordered by the ranker, and NDCG@10 goes 0.4881 → 0.5631 for it.

**Retrieval keeps far more than the ranker surfaces.** Item-item holds 40.0% of a
warm user's holdout items inside 500 candidates; the ranker delivers 3.9% of them
inside 10. Compressing the list 50× costs about 90% of the retained relevance —
which is the number a better ranker has to attack, and the reason the stages are
scored separately.

### What the ranker learned from

11,374 LambdaRank groups over 238,854 rows. **8,626 of the 20,000 sampled positives
were dropped** because the item-item top-500 did not contain the positive at all —
so the run also measures that the candidate stage covers **56.9%** of sampled
training positives (`filter_seen=False`, train-window positives; not the same
population as the holdout recall@500 above). Dropping them is deliberate per ADR
0005: a positive the candidate stage never retrieves is a pattern serving cannot
reproduce. The run also carries `candidate_leakage_compromise = true` — ADR 0005's
own note that the candidate model is fit on all of train including the window the
positives come from, tagged so a later analysis can exclude these runs rather than
silently mix them with clean ones.

### Feature importance (LightGBM total gain)

| Feature | Gain | Share |
|---|---:|---:|
| `user_interaction_count` | 37,150.6 | 17.6% |
| `item_popularity_30d` | 32,791.2 | 15.6% |
| `user_days_active` | 28,773.5 | 13.7% |
| `item_popularity_all_time` | 25,660.2 | 12.2% |
| `user_genre_affinity` | 25,644.7 | 12.2% |
| `item_age_days` | 22,121.8 | 10.5% |
| `item_popularity_7d` | 21,254.5 | 10.1% |
| `user_days_since_last_interaction` | 17,291.1 | 8.2% |

Gains are raw totals as LightGBM reports them; the share column is each feature's
fraction of the 210,687.7 total, computed here for reading only. **No feature
dominates** — the largest is 17.6%, well under ADR 0005's "> 60% of feature
importance" tripwire, and all eight columns of the feature contract carry real
weight. The three popularity windows together are 37.8%, worth remembering next to
the cold-slice improvement above: much of what this ranker knows is how popular a
title is right now.

## Cold-start coverage (ADR 0011)

[ADR 0011](adr/0011-cold-start-coverage.md)'s cohort is 2,000 fixed-seed synthetic
users — 500 at each history size {0, 1, 3, 10}, popularity-weighted items, one
held-out target each. Because each user holds out exactly one item, a bucket's recall
is the **fraction of its 500 users whose one target appeared in the returned list** —
the cleanest reading any slice on this page has. Each trainer scores the cohort at
its own K, so buckets are comparable within a table and not across them.

### Recall per history bucket

At K = 10, alongside the Phase 1 and ranker holdout numbers:

| Model | Run | h0 | h1 | h3 | h10 |
|---|---|---:|---:|---:|---:|
| Popularity | `popularity-baseline` | 0.0340 | 0.0420 | 0.0160 | 0.0240 |
| CF / ALS | `cf-als-baseline` | 0.0340 | 0.0160 | 0.0080 | 0.0180 |
| LightGBM ranker | `lgbm-lambdarank-itemitem-candidates` | 0.0300 | 0.0160 | 0.0240 | 0.0220 |

At K_CANDIDATES = 500, alongside the candidate-stage numbers:

| Model | Run | h0 | h1 | h3 | h10 |
|---|---|---:|---:|---:|---:|
| Item-item cosine | `itemitem-cosine` | 0.4760 | 0.1440 | 0.2880 | 0.3900 |

**Popularity's buckets are flat and non-monotone** (0.0340 / 0.0420 / 0.0160 / 0.0240)
— exactly what a policy that ignores history should look like, and the control the
other rows are read against. It is also the only model here that logs no routing
tag: `PopularityModel` *is* the fallback, so asserting "did this user go to the
fallback?" would be a tautology.

**CF's h0 is identical to popularity's, to the last digit** (0.0340, NDCG 0.017886…).
That is not a coincidence and it is worth stating: a zero-history user is not in the
ALS user index, so `CFModel` hands them the popularity list unchanged. The cohort
reproduces the fallback wiring as a number rather than as a claim about the code.

**The ranker's h0 is 0.0300, not 0.0340, for the same reason its holdout cold slice
improved**: its zero-history users get the same popularity candidates, but LightGBM
reorders them before the cut to 10. Here the reordering costs two users out of 500,
which is inside the sampling noise at this bucket size and should not be read as a
regression.

### Routing: what the cohort actually measured

`synth_cold_expected_fallback_served_h*` is derived from `COLD_START_THRESHOLD = 5`,
not from what any model does — so buckets 0, 1 and 3 are *expected* to be
fallback-served (500 each) and bucket 10 is not. What every learned model on this
page actually did:

| Bucket | Expected fallback-served | Measured — CF, item-item and the ranker alike |
|---|---:|---:|
| h0 | 500 | 500 |
| h1 | 500 | **0** |
| h3 | 500 | **0** |
| h10 | 0 | 0 |

`synth_cold_routing_ok = false` on every learned run. The cause is in the predicates:
each model's `was_served_by_*` reduces to *"is this user in the fitted index at all?"*,
and a user with a single interaction is in that index. So a 1- or 3-interaction user
takes the learned path offline, while ADR 0001 and the deployed serving path both
send them to the popularity fallback.

**The h0-versus-h1 pair is the clean comparison** — identical target distribution,
differing only in history size and therefore in routing. Item-item at K = 500 reads
**h0 0.4760 (fallback-served) against h1 0.1440 (learned-path-served)**: a
1-interaction user served one film's cosine neighbours does roughly a third as well
as the popularity fill the threshold says they should have got. h3 recovers to 0.2880
and h10 to 0.3900 as history accumulates, but neither catches the zero-history bucket.

That is evidence for a direction, not a verdict — h0 is flattered by the
popularity-weighted target distribution ADR 0011's own Risks section warns about, and
a popularity-weighted target is exactly what a popularity fallback is good at. The
honest reading is that the offline models' fallback boundary is not the protocol's.
**Reported here, not repaired here**: closing it moves every warm/cold and per-policy
number on this page, so it is its own unit of work on the Phase 3 platform-track
backlog.

## How to read this

- **These are single-seed, single-machine, offline numbers.** One run per model, one
  seed, one dataset version, scored by the protocol in [ADR 0001](adr/0001-evaluation-protocol.md).
  There are no confidence intervals here because there is only one sample per cell,
  so read the size of a gap before reading its direction: the warm-slice doubling
  between the two Phase 1 baselines is large enough to name, a change in the fourth
  decimal is not.
- **The candidate stage and the ranker are judged on different metrics on purpose.**
  Per [ADR 0003](adr/0003-two-stage-architecture.md), candidate generation answers
  "did the right items make the cut?" and is scored on recall@500; ranking answers
  "in what order?" and is scored on NDCG@10. Comparing a candidate model's
  recall@500 with the ranker's recall@10 is comparing two different questions.
- **Recall divides by the number of relevant items, not by K — which is why recall
  and NDCG can disagree violently.** `recall_at_k` in
  [`src/evaluation/metrics.py`](../src/evaluation/metrics.py) is
  `hits_in_top_k / len(relevant)`, uncapped: a user with 40 holdout items has a
  recall@10 ceiling of 0.25 before the model does anything. `ndcg_at_k` uses binary
  gain, a `1/log2(rank+1)` discount, and an ideal DCG **truncated at K**, so the same
  user's NDCG@10 ceiling is still 1.0. Any slice whose users hold out many more than
  K items shows a low recall beside a much larger NDCG — a property of the
  definitions, not a result. The popularity baseline's cold slice (recall 0.0638,
  NDCG 0.4881) is the extreme case.
- **The promotion gate is documented, not automated.** ADR 0001 pins "≥ +3% relative
  NDCG@10 on the holdout ... automated via the evaluation module — never eyeballed",
  but scopes it to the Phase 4 Prefect DAG, and `pipelines/` is still empty. Nothing
  here was gated by anything; no model was promoted on the strength of these numbers.
- **ADR 0004's item-item-vs-two-tower threshold was never pinned** — it "is decided
  when the two-tower lands (it depends on item-item's actual number)". This page
  supplies item-item's actual number.
- **Online latency is not here.** The p99 < 100 ms SLO is measured under synthetic
  load by the k6 gate and lives in [ADR 0010](adr/0010-synthetic-load-k6.md).

## Reproducing this

```bash
# 1. The stores. Postgres must already hold the 25M ratings.
docker compose up -d postgres mlflow
docker compose exec -T postgres psql -U recsys -d movielens -c 'SELECT count(*) FROM ratings;'
#   count
# ----------
#  25000095

# 2. The ADR 0011 cohort, if data/synthetic/cold_start/v1/users.parquet is absent.
#    Deterministic: regeneration reproduces the DVC-recorded md5 exactly.
make synth-cold-cohort

# 3. The runs, in this order, one at a time.
make train-popularity
make train-cf
make train-itemitem
make train-ranker

# The two-tower needs OMP_NUM_THREADS=1 on a macOS wheel set where torch and
# faiss each bring their own libomp — otherwise it segfaults (see the caveats).
# At one thread it does not finish in ninety minutes on the machine above.
OMP_NUM_THREADS=1 make train-twotower

# 4. Read them back.
open http://localhost:5000            # MLflow: phase-1-baselines,
                                      # phase-2-candidates, phase-2-ranker
```

Every trainer reads ratings from Postgres, applies `src/data/split.py`'s temporal
split, attaches the cold-start cohort if it finds the parquet, fits, recommends,
scores through `src/evaluation/protocol.evaluate`, and writes one MLflow run.
Nothing writes back to the ratings table.

## Caveats worth writing down

**The two-tower run segfaults on this machine with more than one OpenMP thread.**
The first attempt, with the same `OMP_NUM_THREADS=4` every other run on this page
used, died 100 seconds in — immediately after `Fitting two-tower model ...` — with
`make: *** [train-twotower] Segmentation fault: 11`. The crash report puts the
faulting thread inside `libomp.dylib`:

```
EXC_BAD_ACCESS (SIGSEGV) — KERN_INVALID_ADDRESS at 0x8
  libomp.dylib  __kmp_suspend_64<false, true>(int, kmp_flag_64<false, true>*)
  libomp.dylib  kmp_flag_64<false, true>::wait(kmp_info*, int, void*)
  libomp.dylib  __kmp_hyper_barrier_release(...)
  libomp.dylib  __kmp_fork_barrier(int, int)
  libomp.dylib  __kmp_launch_thread
```

Same failure mode PR #82 fixed for the unit suite: two OpenMP runtimes in one process
— here `torch` and `faiss-cpu`, each shipping its own `libomp` — colliding the moment
a parallel region opens a thread team. Same fix: pin the thread count to 1. It is a
property of this laptop's wheel set, not of the model, and it affects no number above
— the other four runs never open a torch/faiss parallel region.

**`make data-download` could not run on 2026-08-29 — GroupLens's TLS certificate had
expired.** `https://files.grouplens.org/datasets/movielens/ml-25m.zip` presents a
certificate valid `notBefore=Aug 28 00:00:00 2025 GMT`, `notAfter=Aug 28 23:59:59 2026 GMT`
(subject `CN=files.grouplens.org`, issuer `CN=InCommon ECC Server CA 2`) — lapsed the
previous day. `src/data/download.py` uses `urllib.request` with default verification,
so it fails closed, correctly. The archive was fetched out of band and verified
against the md5 in `data/raw/ml-25m.dvc` before use; that hash is what makes the
workaround safe and is the reason this page can name a dataset version at all. No
code change was made — the fix belongs on GroupLens's side, and disabling
verification to route around an expired certificate would be the worse bug.

**`make data-ingest` is impractically slow at 25M rows and was not the loading path
used here.** `src/data/ingest.py` does `df.to_sql(..., method="multi", chunksize=10_000)`,
turning 25,000,095 rows into ~2,500 multi-row `INSERT`s built in Python; a measured
run reached roughly 15% of the ratings table in 36 minutes. The database behind these
results was loaded with Postgres `COPY`, which finished in under two minutes. Recorded
rather than fixed — changing the ingestion path is its own unit of work with its own
tests — but anyone reproducing this from raw CSVs should know the committed target
takes hours where `COPY` takes minutes.


## 2026-08-30 — the two-tower finished, and both cold-start routing policies were run

Everything above this line is the 2026-08-29 session and is unchanged. This is a
second measurement session on the same machine, and it settles two things that
session left open. Same rules: every metric comes out of
[`src/evaluation/`](../src/evaluation/) via the trainers, and where something did
not run this page says so.

**Measured:** 2026-08-30
**Dataset, split and cohort:** identical to the session above — MovieLens 25M at
DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir`, `T = 1466837397`, train
20,000,075 / holdout 129,683 / test 4,870,337 untouched, 2,641 holdout users
(1,939 warm, 702 cold), ADR 0011 cohort `v1` at fingerprint `ae4475f0e063…`.
**Machine:** the same Apple M3 / 16 GiB / macOS 26.5.2 box and the same library
set (numpy 2.4.6, pandas 2.3.3, scipy 1.17.1, implicit 0.7.3, lightgbm 4.7.0,
torch 2.13.0, faiss-cpu 1.15.0, MLflow client 3.15.1). Postgres 16 in Docker on
host port 5433; MLflow on 5001.
**Load:** the host was much quieter than on 2026-08-29 — a 1-minute load average
between 2.0 and 4.6 on 8 cores, against the 34–50 that session's jobs were
yielding to. The 25M-row `pd.read_sql` every trainer opens with shows it: 40–50 s
here against 116–464 s there. Still a shared machine, so the wall-clocks below are
still upper bounds, just much tighter ones. Every job ran under `nice -n 15`, one
at a time, thread caps of 4 (1 for the two-tower).
**Cohort determinism, checked again:** the parquet was regenerated from scratch in
a fresh working tree before any run here and reproduced the committed DVC md5
`9e0c978eff3d45f0985e9e0bbe0551d7` and the fingerprint above exactly — an
independent second confirmation of ADR 0011's determinism claim, on a different
checkout.

Two independent things were measured:

1. **The two-tower ran to completion** — the run the previous session could not
   finish inside its budget. That closes the comparison
   [ADR 0004](adr/0004-item-item-before-two-tower.md) set up.
2. **Both cold-start routing policies were run** on item-item, CF/ALS and the
   two-tower, through a new opt-in switch that changes nothing by default. That
   is the evidence behind
   [`cold-start-routing-decision.md`](cold-start-routing-decision.md).

### 1. The two-tower ran to completion

**4,687.9 s of fit** — 78 minutes — for three epochs over the same **19,867,692
(history, positive) pairs across 139,383 users and 34,461 items** the 2026-08-29
attempt reported before it was killed. Configuration was
[ADR 0006](adr/0006-two-tower-retrieval-architecture.md)'s, unchanged: embedding
dim 64, batch 4,096, 3 epochs, Adam at 1e-3, sampled softmax with log-uniform
negative correction over 16,384 sampled negatives, history window 50, FAISS
IVF-Flat (`nlist=100`, `nprobe=10`), seed 42.

Nothing was changed to make it finish. What changed was the machine. At a load
average of 2–4 rather than 34–50 an epoch costs roughly 20 minutes instead of the
37 min 44 s the previous session measured, and three of them plus the FAISS build
and the holdout scoring fit inside 78 minutes. The 2026-08-29 budget was not
wrong about its arithmetic; it was measuring a contended host.

#### Against item-item — the comparison ADR 0004 set up

Both at `K_CANDIDATES = 500`, same holdout, same split, both routing on index
membership (the default), so the model is the only difference:

| Model | Run | Warm recall@500 | Warm NDCG@500 | Cold recall@500 | Cold NDCG@500 | Overall recall@500 | Overall NDCG@500 |
|---|---|---:|---:|---:|---:|---:|---:|
| Item-item cosine | `itemitem-cosine` | **0.4001** | **0.1392** | 0.5290 | 0.4392 | **0.4344** | **0.2190** |
| Two-tower | `twotower-sampled-softmax` | 0.0466 | 0.0146 | 0.5281 | 0.4387 | 0.1746 | 0.1273 |
| | *relative* | **−88.36%** | **−89.53%** | −0.17% | −0.11% | −59.81% | −41.86% |

**The two-tower loses, and not narrowly.** On the warm slice — the only place the
two retrieval policies actually compete — it returns 11.6% of item-item's
recall@500. **ADR 0004's promotion gate is not cleared under any threshold that
ADR could plausibly have named, and item-item remains the champion candidate
generator.** ADR 0004 carries the dated note; nothing was promoted.

The cold columns are nearly equal because they are nearly the same policy: 701 of
the 702 cold users are served by the embedded `PopularityModel` under both models,
so those columns measure the fallback, not retrieval.

For scale — since a recall number in isolation is exactly what ADR 0004's
rationale #1 says not to trust — drawing 500 of the 34,461 train items uniformly
at random has an expected recall@500 of **0.014509**. Item-item is at 27.6× that.
The two-tower is at **3.21×**.

#### Per-policy attribution

| Model | Policy | Users | recall@500 | NDCG@500 |
|---|---|---:|---:|---:|
| Item-item | item-item-served | 1,940 | 0.4003 | 0.1394 |
| Item-item | popularity fallback | 701 | 0.5288 | 0.4393 |
| Two-tower | two-tower-served | 1,940 | 0.0466 | 0.0146 |
| Two-tower | popularity fallback | 701 | 0.5288 | 0.4393 |

The two fallback rows are identical to the last digit, which is the check that
the two models' embedded fallback is the same object doing the same thing.

#### The loss curve, which is the part worth keeping

Mean sampled-softmax loss per epoch: **10.3542 → 10.2726 → 10.2718**. The second
epoch bought 0.0816 and the third bought 0.0008. The model stopped moving after
one pass, and it stopped somewhere that retrieves barely better than chance.

That governs how the table above should be read. **This is a measurement of
two-tower v1 at ADR 0006's configuration, not a finding that learned retrieval
loses to co-occurrence.** Three epochs at `lr=1e-3` over ~14,600 steps is a thin
budget, chosen before anyone had run the model at this scale, and a loss that
flattens after epoch 1 is a hyperparameter symptom before it is an architectural
one. The cheapest next experiment is a training-budget and learning-rate sweep,
not a new model — recorded in ADR 0004's note and in
[`modeling-roadmap.md`](modeling-roadmap.md), where it also removes Rung 1's
stated skip condition.

The two earlier attempts stay in `phase-2-candidates` with status `RUNNING`, as
the 2026-08-29 section describes them. Three further abandoned attempts sit
alongside them and are listed here rather than quietly dropped, because a run
that exists and is not reported is worse than one reported and dismissed:
`2f6521f056824c77a5899545c9084589` (two epochs, from a checkout where the ADR 0011
parquet was absent, so it carries no `synth_cold_*` parameters and is not
comparable with anything here), and `976eb3e5c43a497e9b7b46926a1246c5` and
`aa4249d3ad0642f1af7556a37d7bd3ac`, both from this session and both marked
`KILLED` — the first stopped deliberately once its log showed the cohort parquet
was missing, the second reaped inside epoch 3 by the process supervisor running
it, which turned out to close background jobs on an hour boundary. The successful
threshold run below was launched into its own session with `os.setsid()` for
exactly that reason, which is the second time on this page a two-tower run has
been lost to something other than the model.

### 2. Both cold-start routing policies, measured

`src/models/candidates/routing.py` adds an **opt-in, default-off** switch: every
candidate model takes `cold_start_threshold: int | None`, where `None` — the
default — is the index-membership rule these models have always used, and an int
applies ADR 0001's `COLD_START_THRESHOLD`. The trainers read it from
`SYNTH_COLD_ROUTING` (`index` or `threshold`). Unset reproduces `main`.

**Checked, not assumed.** The index-policy item-item run below and the
`65faeebb5e0545dcaba9ae703cc67af0` run of record from 2026-08-29 agree on every
logged metric to the last digit MLflow stores — warm recall@500
`0.4001438271370617`, warm NDCG@500 `0.13924022499505487`, overall
`0.43438668830444793` / `0.21896241214812664`. Every one of the four short runs
below was made twice in this session on two separate checkouts, and each pair
agrees on **all 38 logged metrics** with no differences at all.

#### Item-item cosine, K_CANDIDATES = 500

| Slice | index | threshold | change |
|---|---:|---:|---:|
| Warm recall@500 | 0.400144 | 0.400144 | **0.00%** |
| Warm NDCG@500 | 0.139240 | 0.139240 | **0.00%** |
| Cold recall@500 | 0.528969 | 0.528527 | −0.08% |
| Cold NDCG@500 | 0.439164 | 0.438946 | −0.05% |
| Overall recall@500 | 0.434387 | 0.434269 | −0.03% |
| Overall NDCG@500 | 0.218962 | 0.218905 | −0.03% |

#### CF / ALS, K = 10

| Slice | index | threshold | change |
|---|---:|---:|---:|
| Warm recall@10 | 0.033841 | 0.033841 | **0.00%** |
| Warm NDCG@10 | 0.057850 | 0.057850 | **0.00%** |
| Cold recall@10 | 0.063780 | 0.063829 | +0.08% |
| Cold NDCG@10 | 0.487981 | 0.488107 | +0.03% |
| Overall recall@10 | 0.041799 | 0.041812 | +0.03% |
| Overall NDCG@10 | 0.172182 | 0.172216 | +0.02% |

#### Two-tower, K_CANDIDATES = 500

| Slice | index | threshold | change |
|---|---:|---:|---:|
| Warm recall@500 | 0.046581 | 0.046581 | **0.00%** |
| Warm NDCG@500 | 0.014575 | 0.014575 | **0.00%** |
| Cold recall@500 | 0.528085 | 0.528527 | +0.08% |
| Cold NDCG@500 | 0.438672 | 0.438946 | +0.06% |
| Overall recall@500 | 0.174569 | 0.174686 | +0.07% |
| Overall NDCG@500 | 0.127304 | 0.127376 | +0.06% |

**The two-tower's cold slice moves up under the threshold where item-item's moves
down, and it is the same user both times.** Under index membership that one
1-to-4-interaction holdout user is served by the learned path; item-item served
them at recall@500 0.6552 — better than the 0.5288 the fallback gives — while the
two-tower served them at 0.0345, far worse. So routing them to popularity costs
item-item a little and buys the two-tower a little. It is one user, and it is an
anecdote rather than a finding; it is recorded because it explains a sign flip
that would otherwise look like an inconsistency.

Two further checks fall out of this pair. The threshold run's **cold row is
identical to item-item's threshold cold row to the last digit** (0.5285270914041289
/ 0.4389458974142213), which it must be: under the threshold every one of the 702
cold users is fallback-served, so that row is the popularity model and nothing
else, whichever learned model sits in front of it. And the run's three epoch
losses are **10.3542 → 10.2726 → 10.2718**, identical to the index run's, which
is the check that routing changes only where a request goes and never what the
model learned.

#### Per-policy attribution under both policies

| Model | Policy | Learned-served | recall | NDCG | Fallback-served | recall | NDCG |
|---|---|---:|---:|---:|---:|---:|---:|
| Item-item @500 | index | 1,940 | 0.400275 | 0.139354 | 701 | 0.528789 | 0.439278 |
| Item-item @500 | threshold | 1,939 | 0.400144 | 0.139240 | 702 | 0.528527 | 0.438946 |
| CF/ALS @10 | index | 1,940 | 0.033841 | 0.057869 | 701 | 0.063822 | 0.488542 |
| CF/ALS @10 | threshold | 1,939 | 0.033841 | 0.057850 | 702 | 0.063829 | 0.488107 |
| Two-tower @500 | index | 1,940 | 0.046574 | 0.014575 | 701 | 0.528789 | 0.439278 |
| Two-tower @500 | threshold | 1,939 | 0.046581 | 0.014575 | 702 | 0.528527 | 0.438946 |

**One holdout user moves. That is the whole difference.** 702 holdout users are
cold at ADR 0001's threshold; under index membership 701 of them are
fallback-served, so exactly **one** holdout user has between one and four training
interactions. The 2026-08-29 section already noticed that user — it is why the CF
and popularity cold columns differ in the fourth decimal — and this session
measures the consequence: **on MovieLens's natural holdout the routing policy is
worth at most 0.08% on any figure, and exactly 0.00% on every warm figure.**

#### ADR 0011 cold-start coverage, both policies

This is the population the decision is actually about, and the only one with users
in the 1–4 interaction band. Recall per bucket, 500 users each:

| Model / policy | K | h0 | h1 | h3 | h10 | `synth_cold_routing_ok` |
|---|---:|---:|---:|---:|---:|---|
| Popularity (control) | 10 | 0.0340 | 0.0420 | 0.0160 | 0.0240 | *no predicate* |
| CF/ALS — index | 10 | 0.0340 | 0.0160 | 0.0080 | 0.0180 | false |
| CF/ALS — threshold | 10 | 0.0340 | **0.0420** | **0.0160** | 0.0180 | **true** |
| Item-item — index | 500 | 0.4760 | 0.1440 | 0.2880 | 0.3900 | false |
| Item-item — threshold | 500 | 0.4760 | **0.4600** | **0.4560** | 0.3900 | **true** |
| Two-tower — index | 500 | 0.4760 | 0.1040 | 0.1260 | 0.1280 | false |
| Two-tower — threshold | 500 | 0.4760 | **0.4600** | **0.4560** | 0.1280 | **true** |

Fallback-served counts. `expected` derives from `COLD_START_THRESHOLD = 5`, not
from what any model does, so it is one row for every model:

| | h0 | h1 | h3 | h10 |
|---|---:|---:|---:|---:|
| expected | 500 | 500 | 500 | 0 |
| index — all three learned models | 500 | 0 | 0 | 0 |
| threshold — all three learned models | 500 | 500 | 500 | 0 |

Three things to read off this.

**The cohort sees what the holdout cannot.** Routing h1 to the fallback moves
item-item's recall@500 from 0.1440 to 0.4600 — 3.2× — and h3 from 0.2880 to
0.4560. On the holdout the same change was worth 0.08%. ADR 0011's premise was
that synthetic cold users cover a region MovieLens's natural distribution does
not; this is the first measurement that demonstrates it rather than asserting it.

**The fallback really is the popularity list.** Under threshold routing CF's h0,
h1 and h3 buckets read 0.0340 / 0.0420 / 0.0160 — the popularity control's numbers
on all three, to four decimals. h10 does not match (0.0180 against 0.0240) and
should not: a 10-interaction user is above the threshold and ALS serves them under
either policy. That is the embedded-fallback wiring reproduced as a measurement.

**The direction is not a verdict.** ADR 0011's own Risks section warns that the
cohort's targets are popularity-weighted, so every fallback-served bucket is
flattered by construction. What these rows establish is that the two policies
produce very different outcomes for 1- and 3-interaction users, and that the
holdout cannot tell you which is better. Which one is right is the open decision
in [`cold-start-routing-decision.md`](cold-start-routing-decision.md); this page
supplies the numbers and does not take it.

### Runs and wall-clocks for this session

| Model | Policy | Run id | Fit | Recommend | Wall-clock |
|---|---|---|---:|---:|---:|
| Item-item cosine | index | `ab1fe49dc21e4c07abc15775fd0cd12d` | 19.7 s | 1.2 s | 62.9 s |
| Item-item cosine | threshold | `006224c40e6c4d31ac94e0c199b4205c` | 20.1 s | 1.1 s | 69.8 s |
| CF / ALS | index | `8b8b86d755e44025be95957c66ecdc91` | 45.4 s | 0.8 s | 89.1 s |
| CF / ALS | threshold | `c491a823bae34e3cbebbe5b8d06e9e45` | 40.5 s | 0.7 s | 86.7 s |
| Two-tower | index | `5628ab0b24c448a78c6f93440e6360b1` | 4,687.9 s | 3.1 s | 4,694.0 s \* |
| Two-tower | threshold | `3286c9683f634d428a3481ff9e4b5644` | 3,620.5 s | 2.1 s | 3,670.7 s |

\* The two-tower's index run is the one job on this page whose end-to-end process
wall-clock was not captured, because it was started before this session's timing
wrapper existed. 4,694.0 s is the span of its MLflow run, which for this trainer
covers fit, retrieval, evaluation and logging; the ~45 s Postgres read precedes it.
Every other row is `time`'s `total`.

**The two two-tower rows are the same computation and differ by 23% in fit time**
— 4,687.9 s against 3,620.5 s — while producing bit-identical epoch losses. Both
ran at one OpenMP thread under `nice -n 15`; the only difference is what else the
laptop was doing. That is the clearest single illustration on this page of why
every wall-clock here is labelled an upper bound, and why none of them is a
benchmark of anything but this machine on this morning.

The popularity control is the 2026-08-29 run `d18737c2180a42f28a0a6255fd00d02e`
already reported above. It has no routing policy to vary, so it was not re-run.

### Reproducing this session

Same prerequisites as the block above — Postgres holding the 25M ratings and the
ADR 0011 parquet present. Then:

```bash
# The default policy: identical to what `main` has always done.
make train-itemitem
make train-cf
OMP_NUM_THREADS=1 make train-twotower     # torch and faiss each ship a libomp

# The same three under ADR 0001's threshold. Nothing else changes.
SYNTH_COLD_ROUTING=threshold make train-itemitem
SYNTH_COLD_ROUTING=threshold make train-cf
SYNTH_COLD_ROUTING=threshold OMP_NUM_THREADS=1 make train-twotower
```

`SYNTH_COLD_ROUTING` accepts `index` (the default, equivalent to leaving it unset)
and `threshold`. Anything else raises rather than falling back to the default — a
typo would otherwise produce a run labelled with a policy it did not use. A
threshold run is named `<base>-threshold-routing` in MLflow and tagged
`cold_start_routing_policy`, so the runs this page cites by name stay findable.

### What was not run, and why

- **No ranker run under the threshold policy.** `src/training/ranker.py` reads the
  same switch and passes it to its candidate model, so a threshold-policy ranker
  run is one environment variable away — but the routing change is worth 0.03% at
  K = 10 on the models that were run, and the ranker's own comparison against
  CF/ALS is unaffected by it. It would settle nothing the candidate runs have not,
  so it was left for whoever takes the decision. The ranker figures above remain
  the 2026-08-29 run.
- **No re-seeded runs.** Everything on this page, in both sessions, is one run per
  cell at seed 42. The 4.16% warm-NDCG question in
  [`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) needs a
  noise floor before it can be settled, and that means three to five seeds of the
  same model. Not done here.
- **No two-tower re-training at a larger budget.** The loss curve says that is the
  interesting experiment, and ADR 0004's note names it — but changing ADR 0006's
  configuration is a modelling decision with an approval gate
  ([`modeling-roadmap.md`](modeling-roadmap.md)), not something to slip into the
  run that measures the configuration as pinned.

## 2026-08-30, second session — the learning-rate and budget sweep

The [session above](#2026-08-30--the-two-tower-finished-and-both-cold-start-routing-policies-were-run)
measured two-tower v1 at ADR 0006's configuration and found warm recall@500 of
0.0466 against item-item's 0.4001, on a loss curve — 10.3542 → 10.2726 →
10.2718 — that stopped moving after one epoch. It said, in as many words, that
this was "a hyperparameter symptom before it is an architectural one" and that
the cheapest next experiment was a training-budget and learning-rate sweep.
This is that sweep, and its last bullet — "no two-tower re-training at a larger
budget" — is what this section closes.

**No architecture changed.** The towers, the loss family, the sampler, the
retrieval index and the routing are ADR 0006's, and every default in
`TwoTowerConfig` is still the value that ADR pins.

**Measured:** 2026-08-30
**Dataset, split and cohort:** identical to both sessions above — MovieLens 25M
at DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir`, `T = 1466837397`, train
20,000,075 / holdout 129,683 / test 4,870,337 untouched, 2,641 holdout users
(1,939 warm, 702 cold), ADR 0011 cohort `v1` attached to every full-dataset run.
**Threshold regime:** this section sits under the banner at the top of the page
with everything above it. It was measured on `main` at `a5f8d20` — *before*
[#119](https://github.com/kudratsingh/MovieLens-RecSys/pull/119) raised
`COLD_START_THRESHOLD` from 5 to 10 and made ADR 0001's threshold the default
offline routing rule — so warm/cold slicing is at 5, routing is index
membership, and the slice is 1,939 warm / 702 cold. That is deliberate rather
than incidental: the sweep's entire method is comparison against v1, item-item
and popularity, and all three are on this regime. Reproducing these numbers on
current `main` needs `SYNTH_COLD_ROUTING=index` *and* the threshold back at 5;
re-running at 10 moves the slicing and the served population together and would
need its own dated section. The MLflow run names here carry a sweep label
rather than a policy suffix, which under the pre-#119 convention means index
routing.
**Machine:** the same Apple M3 / 8 cores / 16 GiB / macOS 26.5.2 (25F84) box,
Python 3.11.16, numpy 2.4.6, pandas 2.3.3, torch 2.13.0, faiss-cpu 1.15.0,
MLflow client 3.15.1, Postgres 16 in Docker on host port 5433, MLflow on 5001.
**Load:** the host was shared throughout and its 1-minute load average, sampled every
two minutes across the whole 5½-hour session, ran **2.04 to 41.09 with a mean
of 4.49 over 161 samples** on 8 cores. The spike is one unrelated workload
between roughly 06:45 and 07:00 that also drove swap to 12.4 GB of 13.3; it
lands inside the pilot and is visible in two of its cell wall-clocks. The
full-dataset runs were made at a load average between 3 and 6.
Every job ran under `nice -n 15` at one thread on `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and `VECLIB_MAXIMUM_THREADS`, one at
a time, never concurrently. The wall-clocks are upper bounds, as on every
session above.
**Cohort determinism, checked a third time:** this worktree had only the DVC
pointer, so the parquet was regenerated from scratch before any full-dataset
run and reproduced the recorded md5 `9e0c978eff3d45f0985e9e0bbe0551d7`, its
45,337-byte size and the fingerprint `ae4475f0e063…` exactly. That is now three
independent confirmations of ADR 0011's determinism claim on three checkouts.

### What was parameterised, and what was not

Every `TwoTowerConfig` field is now readable from a `TWOTOWER_*` environment
variable, and the whole config is logged to MLflow, so each run below is
reproducible from its own params rather than from a diff. **No default moved.**
A unit test (`test_adr_0006_defaults_are_unchanged`) asserts each one against
ADR 0006, so a future edit has to be deliberate.

Four fields are new. Three are conveniences at their v1-equivalent values —
`early_stopping_patience = 0` (v1 always ran exactly `epochs` passes),
`faiss_exact = False` (ADR 0006 pins IVF-Flat, and deliberately: its
Alternatives section rejects exact search at eval time precisely so the offline
number carries IVF's approximation loss), and `correct_positive_logit = True`
(what v1 did). The fourth is the one worth reading carefully.

**`logit_temperature`, default 1.0 — v1's implicit value.** ADR 0006 pins two
things that interact and never names the constant that reconciles them. Both
towers are L2-normalized, so a raw logit is a cosine bounded to `[-1, 1]`: two
nats of range, total, for the model to express a preference in. The log-uniform
correction then subtracts `log P(item)` from that logit, and measured over the
real train split's 34,461 items with ADR 0006's own sampler those corrections
run from **2.713 nats** for the most-watched title to **12.794** for a title
seen once — **a 10.081-nat spread of fixed, unlearnable offset sitting on top
of a two-nat signal.** A temperature divides the cosine before the correction
is applied, and is the standard way every cosine-similarity two-tower makes the
learned part competitive with the prior. It is a hyper-parameter of the loss,
not a change of architecture; at τ = 1.0 the arithmetic is bit-identical to
v1's, which a test also asserts.

### The pilot: 6% of users, chosen by seed

Twelve configurations at 78 minutes each does not fit in a day, so the orders of
magnitude were found on a subsample first. The subsample keeps **every
interaction of 6% of users** (9,752 of 162,541, drawn at seed 42), not 6% of
rows: the user tower is a mean-pool over a history, so thinning rows would
shorten every history and the pilot would be measuring a different model.
Splitting that subsample on time by the same rule gives train 1,213,918 /
holdout 7,528, 1,205,602 training pairs, 8,316 users and 19,005 items — and a
warm slice of **116 users**, which is the pilot's main limitation and is
returned to below. Every cell trains and scores on the same people; only the
configuration differs. The grid is committed at
[`docs/experiments/twotower-sweep/pilot.json`](experiments/twotower-sweep/pilot.json).

The pilot is worth trusting for one specific reason: **its v1 cell reproduces
v1's pathology.** At ADR 0006's configuration the subsample scores warm
recall@500 of 0.0463 after one epoch against the full run's 0.0466 — the same
number at 6% of the data.

Three reference lines on the pilot's own 116 warm users, measured through the
same `src/evaluation/` call the cells use, so the two-tower numbers have
something to be a fraction *of*:

| Reference on the pilot subsample | Warm recall@500 | Warm NDCG@500 | × chance |
|---|---:|---:|---:|
| Chance — 500 of 19,005 items drawn uniformly | 0.026309 | — | 1.00× |
| **Popularity** — the two-tower's own embedded fallback | **0.1974** | 0.0721 | 7.50× |
| **Item-item cosine** — the reigning champion | **0.3619** | 0.1301 | 13.76× |

Item-item scores 0.3619 here against 0.4001 on the full dataset, so the
subsample is a slightly harder version of the same problem rather than a
different one. On the full dataset chance is 500 / 34,461 = **0.014509**;
item-item sits at 27.6× it and two-tower v1 sat at 3.21×.

All twelve cells, three epochs each, same 116 warm users. The last three sit
outside the learning-rate × temperature grid and are discussed separately below.
`× chance` is against the pilot's 0.026309.

| Cell | lr | τ | negatives | Warm recall@500 | × chance | Warm NDCG@500 | Final loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| `lr1e-4-t1.0` | 1e-4 | 1.0 | 16,384 | 0.0392 | 1.49× | 0.0119 | 10.7418 |
| `lr1e-3-t1.0` — **ADR 0006 as written** | 1e-3 | 1.0 | 16,384 | 0.0443 | 1.68× | 0.0137 | 10.0212 |
| `lr1e-2-t1.0` | 1e-2 | 1.0 | 16,384 | 0.0459 | 1.74× | 0.0141 | 9.9432 |
| `lr1e-4-t0.1` | 1e-4 | 0.1 | 16,384 | 0.0518 | 1.97× | 0.0141 | 9.7127 |
| `lr1e-3-t0.1` | 1e-3 | 0.1 | 16,384 | 0.0413 | 1.57× | 0.0149 | 8.5407 |
| `lr1e-2-t0.1` | 1e-2 | 0.1 | 16,384 | 0.0495 | 1.88× | 0.0178 | **8.2335** |
| `lr1e-4-t0.05` | 1e-4 | 0.05 | 16,384 | 0.0448 | 1.70× | 0.0134 | 10.5740 |
| `lr1e-3-t0.05` | 1e-3 | 0.05 | 16,384 | 0.0406 | 1.54× | 0.0137 | 8.8088 |
| `lr1e-2-t0.05` | 1e-2 | 0.05 | 16,384 | **0.0520** | **1.98×** | 0.0182 | 8.3082 |
| `lr1e-3-t1.0-nopos` — negatives corrected only | 1e-3 | 1.0 | 16,384 | 0.0443 | 1.68× | 0.0137 | 18.4928 |
| `lr1e-3-t0.05-neg4096` | 1e-3 | 0.05 | 4,096 | 0.0480 | 1.83× | 0.0156 | 7.4720 \* |
| `lr1e-3-t0.05-exact` — exact retrieval | 1e-3 | 0.05 | 16,384 | 0.0432 | 1.64× | 0.0144 | 8.8088 |

\* Not comparable with the column: with 4,096 negatives the no-opinion loss is
`ln(4,097) = 8.3180` rather than `ln(16,385) = 9.7041`, so that cell's 7.4720
is 0.85 nats under *its* floor, where `lr1e-2-t0.1`'s 8.2335 is 1.47 under
*its* one.

The curves, since a final number hides the thing worth seeing:

| Cell | Loss per epoch | Warm recall@500 per epoch |
|---|---|---|
| `lr1e-3-t1.0` | 10.5581 → 10.1168 → 10.0212 | 0.0463 → 0.0453 → 0.0443 |
| `lr1e-3-t0.05` | 10.4800 → 9.1271 → 8.8088 | 0.0479 → 0.0455 → 0.0406 |
| `lr1e-2-t1.0` | 10.0702 → 9.9483 → 9.9432 | 0.0418 → 0.0488 → 0.0459 |
| `lr1e-4-t1.0` | 11.0532 → 10.9029 → 10.7418 | 0.0206 → 0.0333 → 0.0392 |
| `lr1e-3-t0.1` | 9.6159 → 8.7463 → 8.5407 | 0.0470 → 0.0432 → 0.0413 |
| `lr1e-2-t0.05` | 9.1203 → 8.4276 → 8.3082 | 0.0451 → 0.0498 → 0.0520 |
| `lr1e-4-t0.05` | 13.4743 → 11.5506 → 10.5740 | 0.0306 → 0.0425 → 0.0448 |
| `lr1e-2-t0.1` | 8.7120 → 8.3112 → 8.2335 | 0.0488 → 0.0518 → 0.0495 |
| `lr1e-4-t0.1` | 11.4941 → 10.3662 → 9.7127 | 0.0269 → 0.0404 → 0.0518 |
| `lr1e-3-t1.0-nopos` | 19.0297 → 18.5886 → 18.4928 | 0.0477 → 0.0451 → 0.0443 |
| `lr1e-3-t0.05-neg4096` | 9.1584 → 7.7790 → 7.4720 | 0.0423 → 0.0388 → 0.0480 |
| `lr1e-3-t0.05-exact` | 10.4800 → 9.1271 → 8.8088 | 0.0419 → 0.0434 → 0.0432 |

MLflow run ids, in the order above:
`425d5b96` (`lr1e-3-t1.0`), `f55e543f`, `a0f48c0a`, `dee1f211`, `6a35f8688`,
`bed3da94`, `58f3cfc0`, `e53cfa58`, `be91e8df`, `fbf5ed66`, `72a482de`,
`36b4c216` — all in `phase-2-candidates`, tagged `sweep_label` and
`user_sample_fraction = 0.06`.

### What the pilot found

The nine cells of the learning-rate × temperature grid land in a band from
**0.0392 to 0.0520** — 1.49× to 1.98× the chance line — while their final
losses span **2.5 nats**, from 8.2335 to 10.7418. Popularity, on the same 116
users with the same already-seen filter, is at 0.1974. **The best cell in the
grid retrieves 3.8× worse than the popularity list this model carries inside
itself as its cold-start fallback, and 7.0× worse than item-item.**

Read as a grid, warm recall@500 after three epochs:

| lr \ τ | **1.0** (ADR 0006) | **0.1** | **0.05** |
|---|---:|---:|---:|
| **1e-4** | 0.0392 | 0.0518 | 0.0448 |
| **1e-3** (ADR 0006) | 0.0443 | 0.0413 | 0.0406 |
| **1e-2** | 0.0459 | 0.0495 | 0.0520 |

Three readings, in the order the sweep was opened to test them.

**1. The learning rate was never the problem.** At ADR 0006's τ = 1.0, a decade
below the default gives 0.0392 and a decade above gives 0.0459, against the
default's 0.0443 — a spread of 0.007 on a 116-user slice, which is inside what
that slice can resolve. Nothing in the row is a fix, and the default was
already in the right order of magnitude. **That closes the first of the three
hypotheses,** and it is the one the sweep was named for.

**2. v1's loss was worse than emitting no opinion at all.** With 16,384 sampled
negatives, a model that gives every candidate the identical logit scores
`ln(16,385) = 9.7041`. **No cell at τ = 1.0 ever got under that line at any
learning rate** — the best was 9.9432 — and v1's own full-dataset final loss was
10.2718. Every cell at τ ≤ 0.1 and lr ≥ 1e-3 got under it on the first or
second epoch. That is the ADR 0006 arithmetic showing up as a measurement: an
L2-normalized cosine gives the model two nats to speak in, the log-uniform
correction spans 10.081 across the real catalog, and at τ = 1.0 the model is
outvoted five to one by a prior it cannot argue with. **So the flat loss curve
in the first 2026-08-30 session was not a model that had finished learning. It
was a model with no room left to say anything.**

**3. Giving it room fixes the loss and does not fix retrieval.** This is the
finding worth the sweep, and it is visible twice. Across the grid, 2.5 nats of
objective buys at most 0.013 of recall and leaves every cell in the same band.
Within a cell it is starker: at lr = 1e-3, τ = 0.05 the loss falls hard and
never plateaus — 10.4800 → 9.1271 → 8.8088, still descending when the budget
ran out — while warm recall@500 **falls with it**, 0.0479 → 0.0455 → 0.0406.
The model is not stuck and it is not under-trained. It is optimizing its
objective successfully and retrieving no better, or worse, for it. Spending
more epochs on that buys more of the same, which is what the full-dataset runs
below went and checked.

Two cells sit at the top of the band — `lr1e-2-t0.05` at 0.0520 and
`lr1e-4-t0.1` at 0.0518 — and they have nothing in common but their position in
the table, which is the clearest evidence available that the ordering *inside*
this band is noise and not signal. `lr1e-2-t0.05` was taken to the full dataset
anyway, on the only grounds available: it has the band's best final value *and*
was still climbing on its last epoch (0.0451 → 0.0498 → 0.0520) rather than
falling as its loss improved, which is the strongest claim to more epochs any
cell here has.

### Three cells outside the grid

Each answers a question the grid could not, and none of them moves the band.

**`lr1e-3-t1.0-nopos` — ADR 0006's literal wording.** That ADR says "each
*negative's* logit is corrected"; the code corrects the positive's too, which
is what TensorFlow's `sampled_softmax_loss` does. Turning the positive
correction off costs **8.5 nats** — 19.0297 → 18.5886 → 18.4928 against
10.5581 → 10.1168 → 10.0212 — because a positive with no boost has to out-score
negatives that got between 2.7 and 12.8 nats of one for free. So the code's
reading is the better one, and the ADR's wording is what should change. **Both
cells finish at warm recall@500 of 0.0443, identical to four decimal places.**
Eight and a half nats of objective, and retrieval did not move at all.

**`lr1e-3-t0.05-neg4096` — a quarter of the negatives.** ADR 0006 pins
`num_sampled = 4 × batch_size`. At 4,096 the same three epochs cost **66.6 s
against 210** — the `(B, S)` logit matrix is essentially the entire cost of this
model, so a quarter of the negatives is a quarter of the arithmetic, and the
budget lever is a real one. Its loss lands at 7.4720, but against a different
floor: the no-opinion line for 4,096 negatives is `ln(4,097) = 8.3180`, not
9.7041, so the two losses compare only as distances from their own floors.
Warm recall@500 finishes at 0.0480 — the same band. **The cheap budget exists,
and buys nothing to spend it on.**

**`lr1e-3-t0.05-exact` — no ANN approximation at all.** The one cell that is a
validity check on every other number here rather than an experiment. ADR 0006
chose IVF-Flat at `nprobe = 10` over exact search deliberately, so that the
offline recall would carry the approximation loss that serving will carry — but
it asserted ">0.95 recall vs exact" without measuring it on these embeddings,
and at `nlist = 100` a query sees about a tenth of the catalog. If IVF were
quietly discarding the neighbours the towers had actually learned, every recall
number on this page would be understated and the verdict would be wrong.
It is not. The exact cell trains identically — its per-epoch losses are
bit-identical to its IVF twin's, 10.4800 → 9.1271 → 8.8088, which is also a
free determinism check — and finishes at warm recall@500 of **0.0432 against
the IVF twin's 0.0406**. Exact search was *lower* than IVF at the first two
epochs (0.0419 vs 0.0479, 0.0434 vs 0.0455) and higher at the third, which is
what a difference smaller than the slice's noise looks like. **Removing the
approximation entirely moves warm recall by less than 0.003 and leaves the
model 4.6× below popularity.** ADR 0006's IVF choice is exonerated, and every
recall number on this page is a measurement of the embeddings.

### The full-dataset runs

Two configurations went to the full 20,000,075-row training frame, chosen from
the pilot and committed at
[`docs/experiments/twotower-sweep/full.json`](experiments/twotower-sweep/full.json):

- **`budget-8ep`** — ADR 0006's configuration exactly, given up to eight epochs
  instead of three with early stopping on a loss plateau (patience 2, min-delta
  1e-4; the min-delta is deliberately below v1's third-epoch improvement of
  0.0008, so the run cannot stop at the point it exists to get past). This is
  the *budget* half of the question, asked at the configuration the question
  was asked about. Because the config and the seed are v1's, **its first three
  epoch losses have to reproduce v1's 10.3542 → 10.2726 → 10.2718 exactly**, so
  the run doubles as the check that this PR's memory rewrite moved no number.
- **`lr1e-2-t0.05-8ep`** — the best cell in the pilot (0.0520) and one of the
  few whose recall was still climbing on its last epoch, given the same eight.
  If any combination of learning rate and budget can move this architecture,
  this is where it shows.

First, the reference lines the candidate-stage table above never had. Popularity
had only ever been scored at K = 10 on this page; here it is at
K_CANDIDATES = 500, on the same 1,939 warm holdout users, through the same
`src/evaluation/` call, with the same already-seen filter applied:

| Full dataset, 1,939 warm holdout users | Warm recall@500 | Warm NDCG@500 | × chance |
|---|---:|---:|---:|
| Chance — 500 of 34,461 items drawn uniformly | 0.014509 | — | 1.00× |
| **Two-tower v1** (ADR 0006, 3 epochs) | 0.0466 | 0.0146 | 3.21× |
| **Popularity** — the two-tower's own embedded fallback | **0.2310** | 0.0867 | **15.92×** |
| **Item-item cosine** — the champion | **0.4001** | 0.1393 | **27.58×** |

**That table is the sweep's context in four rows.** Two-tower v1 does not sit
somewhere between popularity and item-item, which is where a working-but-weaker
retriever would sit. It sits between *chance* and popularity, nearly five times
below the fallback list it carries inside itself for cold users. The item-item
row here was recomputed for this section and reproduces the recorded run to
four decimal places (0.4001, 0.1393), which is the check that these reference
lines were measured the same way as everything else on the page.

| Full dataset, 1,939 warm holdout users | lr | τ | Epochs | Warm recall@500 | × chance | Warm NDCG@500 | Final loss | Fit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Two-tower v1 (first 2026-08-30 session) | 1e-3 | 1.0 | 3 | 0.0466 | 3.21× | 0.0146 | 10.2718 | 4,687.9 s |
| **`budget-8ep`** | 1e-3 | 1.0 | **8** | 0.0451 | 3.11× | 0.0143 | 10.2707 | 10,847.0 s |
| **`lr1e-2-t0.05-4ep`** | 1e-2 | 0.05 | 4 | **0.0591** | **4.07×** | **0.0193** | **8.1262** | 5,458.3 s |
| **Popularity** — the embedded fallback | — | — | — | **0.2310** | **15.92×** | 0.0867 | — | 5.9 s |
| **Item-item cosine** — the champion | — | — | — | **0.4001** | **27.58×** | 0.1393 | — | 19.7 s |

Per-epoch, which is where the answers are:

| Run | Loss per epoch | Warm recall@500 per epoch |
|---|---|---|
| `budget-8ep` | 10.3542 → 10.2726 → 10.2718 → 10.2712 → 10.2711 → 10.2708 → 10.2704 → 10.2707 | 0.0453 → 0.0448 → **0.0466** → 0.0458 → 0.0463 → 0.0453 → 0.0456 → 0.0451 |
| `lr1e-2-t0.05-4ep` | 8.3171 → 8.1715 → 8.1424 → 8.1262 | **0.0613** → 0.0576 → 0.0583 → 0.0591 |

MLflow: `ae4269c71a73415b9de30b2c703a69b4` and `751fdbeedf8542989d9b015551f6dae7`,
both in `phase-2-candidates`, both with the ADR 0011 cohort attached at
fingerprint `ae4475f0e063…`.

#### The refactor moved no number, and this is the proof

`budget-8ep` is v1's configuration and v1's seed, so its first three epochs have
to be v1's. They are: **10.3542 → 10.2726 → 10.2718**, matching the recorded run
digit for digit, and its **warm recall@500 at epoch 3 is 0.0466** against v1's
0.04658. Three epoch losses and a recall, over 19,867,692 training pairs, all
identical. The batch-gather and int32-pair-array rewrites in this PR are
therefore purely a memory and speed change, and every comparison on this page
between a swept run and v1 compares configurations rather than code. Resident
set during the fit was about **2 GB**, against a v1 shape that put roughly 8 GB
of Python list objects plus a second 8 GB permuted tensor on a 16 GB laptop.

#### Was three epochs too little? No — eight is slightly worse

Epochs 4 through 8 cost **6,159 more seconds of fit** — an hour and three
quarters — and bought **0.0011 nats**: 10.2718 down to 10.2707, with the eighth
epoch actually *worse* than the seventh. Early stopping never fired, and not
because the model was still learning: the loss kept creeping down in the fourth
decimal, so a patience-2 rule at min-delta 1e-4 never saw two consecutive
non-improvements. Having nowhere useful to go is a different thing from having
somewhere to go slowly.

Warm recall@500 across those eight epochs reads
0.0453 / 0.0448 / **0.0466** / 0.0458 / 0.0463 / 0.0453 / 0.0456 / **0.0451** —
a range of 0.0018 with no trend in it, and the eight-epoch model is *worse* than
the three-epoch one, 3.11× chance against 3.21×. **The training budget was not
the problem, and this is the run that says so at full scale rather than by
extrapolation from a subsample.**

#### The temperature is worth something, and nothing like enough

`lr1e-2-t0.05-4ep` is the best two-tower number this project has measured:
**warm recall@500 0.0591 against v1's 0.0466 (+26.8%) and warm NDCG@500 0.0193
against 0.0146 (+32.2%)**, at 4.07× chance rather than 3.21×. Its loss reaches
**8.1262**, more than 1.5 nats under the `ln(16,385) = 9.7041` line that v1
never got below at all. The objective is fixed; the fix is real and it is
measurable at full scale.

And it changes nothing that matters. **0.0591 is 3.9× below the 0.2310 that the
popularity list this model embeds as its own cold-start fallback scores on the
same 1,939 users, and 6.8× below item-item's 0.4001.** The improvement moves the
model from "far worse than its own fallback" to "far worse than its own
fallback".

The per-epoch curve also repeats the pilot's shape at full scale: recall peaks
at **epoch 1** (0.0613) and then settles lower (0.0576 → 0.0583 → 0.0591) while
the loss falls monotonically. The best retrieval this configuration produced was
after a single pass, before the objective had been optimized properly.

#### What the embedding-spread metric shows

The two full runs fail in visibly different geometries, which is the clearest
argument that the problem is the shape rather than the settings.

| Run | Mean pairwise item cosine | Std |
|---|---|---|
| `budget-8ep` (τ = 1.0) | 0.130 → 0.136 → 0.135 → 0.135 → 0.138 → 0.138 → 0.138 → 0.138 | 0.737 → 0.732 → 0.732 → 0.733 → 0.730 → 0.731 → 0.731 → 0.731 |
| `lr1e-2-t0.05-4ep` (τ = 0.05) | 0.426 → 0.416 → 0.409 → 0.406 | 0.132 → 0.130 → 0.129 → 0.128 |

For scale, 64-dimensional random unit vectors have a pairwise-cosine mean near 0
and a standard deviation near `1/√64 = 0.125`. **At τ = 1.0 the catalogue
spreads across a very wide angular range (std 0.73, six times random) around a
near-zero mean** — strongly anisotropic, packed along a few dominant directions.
**At τ = 0.05 it does the opposite: a narrow cone (std 0.128, essentially the
random value) around a mean cosine of 0.41** — every item pointing broadly the
same way, which is the representation-collapse shape contrastive training is
known for.

In both cases the geometry is settled within the first epoch and barely moves
afterwards: eight epochs at τ = 1.0 change the mean by 0.008 and the standard
deviation by 0.006. **Two very different degenerate geometries, one bad
retrieval number each, and neither of them is something a learning rate or an
epoch count reaches.**

#### ADR 0011 cold-start coverage

Both runs carried the cohort (2,000 users, 7,000 history rows, fingerprint
`ae4475f0e063…`):

| Run | Bucket | Users | recall@500 | NDCG@500 | Fallback-served | Expected |
|---|---|---:|---:|---:|---:|---:|
| `budget-8ep` | h0 | 500 | 0.4760 | 0.0823 | 500 | 500 |
| `budget-8ep` | h1 | 500 | 0.1040 | 0.0140 | 0 | 500 |
| `budget-8ep` | h3 | 500 | 0.1320 | 0.0201 | 0 | 500 |
| `budget-8ep` | h10 | 500 | 0.1260 | 0.0174 | 0 | 0 |
| `lr1e-2-t0.05-4ep` | h0 | 500 | 0.4760 | 0.0823 | 500 | 500 |
| `lr1e-2-t0.05-4ep` | h1 | 500 | 0.0940 | 0.0124 | 0 | 500 |
| `lr1e-2-t0.05-4ep` | h3 | 500 | 0.1000 | 0.0136 | 0 | 500 |
| `lr1e-2-t0.05-4ep` | h10 | 500 | 0.1240 | 0.0169 | 0 | 0 |

`synth_cold_routing_ok` is **false** for both, exactly as for every
index-routing run above and for the same reason: under index membership a user
with one training interaction is in the index, so h1 and h3 take the learned
path where ADR 0001's threshold says they should take the fallback. That is the
mismatch [#119](https://github.com/kudratsingh/MovieLens-RecSys/pull/119) went
on to close; these runs predate it.

Two rows are worth reading anyway. **h0 is 0.4760 in both runs, identical to
item-item's h0** — all three are the popularity fallback doing the same thing,
which is the check that the buckets are wired correctly. And h1 / h3 / h10 at
0.1040 / 0.1320 / 0.1260 and 0.0940 / 0.1000 / 0.1240 sit far below item-item's
0.1440 / 0.2880 / 0.3900 on the same buckets — and the temperature run, which
is the better model on the natural holdout, is the *worse* one here.
**Everywhere the two-tower's learned path actually runs, it loses.**

### The verdict

**No configuration in this sweep beats item-item's warm recall@500 of 0.4001,
and none comes close. ADR 0004's promotion gate is not cleared — the two-tower
does not beat item-item at all, so no threshold that ADR could plausibly have
named is in question — and item-item remains the champion candidate generator.**
Nothing was promoted. That is the same verdict the first 2026-08-30 session
reached; what is new is that it now rests on fourteen runs across three decades
of learning rate, three temperatures, two negative-sample counts, both readings
of the correction, both retrieval indexes and up to eight epochs, rather than
on one run at one configuration.

The three hypotheses the sweep was opened to separate now have separate
answers.

**"A wrong learning rate" — no.** At ADR 0006's τ = 1.0, `lr ∈ {1e-4, 1e-3,
1e-2}` gives warm recall@500 of 0.0392 / 0.0443 / 0.0459. A decade either side
of the default moves the metric by less than 0.007 on a 116-user slice — inside
what that slice resolves — and all three sit between 1.5× and 1.8× chance. The
default was already in the right order of magnitude.

**"Too little training" — no, and this is the useful part.** v1's loss did not
flatten because the model had converged. It flattened because it had **run out
of range to speak in.** ADR 0006 pins L2-normalized towers, so a logit is a
cosine two nats wide, and a log-uniform correction, which measures 10.081 nats
wide across the real train split — and it names no temperature to reconcile
them. The proof that this is what happened is that **v1's final loss, 10.2718,
is worse than `ln(1 + 16,384) = 9.7041`, the loss of a model that emits the
identical logit for every candidate**; no cell at τ = 1.0 ever got under that
line at any learning rate. Restoring the range with a temperature does exactly
what the diagnosis predicts — at τ = 0.05 the full-dataset loss reaches 8.1262,
more than 1.5 nats under that line — **and retrieval follows only a little
way.** That run is the best two-tower number this project has: warm recall@500
**0.0591 against v1's 0.0466, +26.8%**, and NDCG@500 0.0193 against 0.0146.
It is a real gain on a real fix, and it moves the model from 3.21× chance to
4.07× — against popularity's 15.92× and item-item's 27.58×. More
epochs buy a little more loss and no more recall, which the eight-epoch
full-dataset run at ADR 0006's own configuration then confirmed directly:
epochs 4 through 8 bought 0.0011 nats over an hour and three quarters of extra
fit, and left warm recall@500 at **0.0451 against the third epoch's 0.0466.**
Nearly three times v1's budget made the model very slightly worse.

**"The architecture as specified is weak on this data" — this is what is
left.** The sweep drove the training objective down by as much as 8.5 nats
(`nopos` → v1) and by 2.1 nats between the two full-dataset runs, and bought
warm recall@500 of 0.0591 where the popularity fallback scores 0.2310. Two cells make the point without any interpretation
needed: turning off the positive correction changes the loss by 8.5 nats and
leaves warm recall and NDCG **identical to four decimal places**; and replacing
IVF-Flat with exact search — the one thing that could have meant every number
here was understated — moves recall by 0.0026. Meanwhile, on the full dataset,
**the popularity list this model embeds as its own cold-start fallback scores
0.2310 against v1's 0.0466**, and item-item scores 0.4001.

So, plainly, as the honest input to the next decision: **the two-tower as ADR
0006 specifies it does not beat item-item on MovieLens 25M at any learning rate
or budget this sweep could afford — and at its best swept configuration it
still retrieves 3.9× worse than the popularity list it carries inside itself
as its own cold-start fallback.** The failure is not explained by the
learning rate, and it is not explained by the training budget. The one
configuration change that is clearly right on its own terms — a temperature,
which repairs an objective that was provably worse than silence — is proposed
as a new default in [ADR 0006's note](adr/0006-two-tower-retrieval-architecture.md),
because a model should be able to fit its own loss whatever else is true of it.
**It is not proposed as a route to clearing ADR 0004's gate, because it is not
one.** What to do about a two-tower that behaves this way is a modelling
decision with an approval gate, and it belongs to the owner, not to this page.

### Runs and wall-clocks for this session

| Job | What | Fit | Wall-clock |
|---|---|---:|---:|
| Pilot sweep | 12 cells at 6% of users, one MLflow run each, one 25M-row read shared | 65.9 – 596.0 s per cell | **2,973 s** (49 min 33 s) |
| Full-dataset references | popularity + item-item at `K_CANDIDATES = 500`, for the table above | — | 89 s |
| `budget-8ep` | ADR 0006's config, full dataset, all 8 epochs run | **10,847.0 s** | 10,861.8 s |
| `lr1e-2-t0.05-4ep` | best pilot cell, full dataset, 4 epochs | **5,458.3 s** | 5,466.1 s |

The pilot's 2,973 s covers all twelve cells *and* the single 60.1-second
`read_sql` they share — which is the whole reason the sweep runner exists,
since twelve separate `make train-twotower` invocations would have paid that
read twelve times.

**The per-cell spread is contention, not configuration.** `lr1e-3-t1.0` fit in
234.0 s and `lr1e-3-t0.05` in 596.0 s doing the identical shape of work, because
an unrelated workload on the same laptop took the 1-minute load average from
2.04 to 41.09 during the second one. Same caveat as every session above: these
are upper bounds, not benchmarks. **The metrics are unaffected** — they are
seed-deterministic, and the sweep produced its own proof of that, since
`lr1e-3-t0.05` and `lr1e-3-t0.05-exact` differ only in retrieval index and
logged bit-identical per-epoch losses (10.4800 → 9.1271 → 8.8088).

The one row where a wall-clock says something about the model rather than the
laptop is `lr1e-3-t0.05-neg4096` at **65.9 s against its 16,384-negative twin's
596.0** (or 210-ish uncontended): the `(B, S)` logit matrix is essentially the
entire cost of training this model, so `num_sampled` is the budget dial, and a
quarter of the negatives is roughly a quarter of the arithmetic. It buys
nothing in recall — see the diagnostics above — but it is the lever to reach
for if a future rung wants more epochs per hour.

Two more notes on cost, since they are the difference between this sweep being
affordable and not. The memory rewrite in this PR (batch gather instead of a
permuted copy; a preallocated int32 pair array instead of 19.9M Python lists)
took the full-dataset fit's resident set to **about 2 GB**, against a v1 shape
that put roughly 8 GB of list objects and a second 8 GB permuted tensor on a
16 GB laptop. And the per-epoch recall scoring this PR adds costs a fraction of
a second per epoch on the pilot and a few seconds on the full dataset; it is
excluded from every `fit` figure above, so these remain comparable with the
runs made before it existed.

**One run is in the experiment and is not in the table above.**
`53b0d41e478a421691c9d5b574031732`, named
`twotower-sampled-softmax-lr1e-2-t0.05-8ep`, is marked `KILLED`. The
full-dataset grid was launched with its second cell at eight epochs; when the
first cell turned out to cost 21–26 minutes per epoch and ran all eight, eight
more would not have fitted in the day. It was stopped about a minute into its
fit — before its first epoch — and relaunched at four epochs as
`lr1e-2-t0.05-4ep`, so nothing was measured and then discarded. It is listed
here rather than quietly dropped, on the same principle the 2026-08-30 session
above applies to its own abandoned attempts.

### Reproducing this session

Same prerequisites as the sessions above — Postgres holding the 25M ratings,
and the ADR 0011 parquet present (`make synth-cold-cohort` regenerates it
deterministically if a checkout has only the DVC pointer). Every hyperparameter
is an environment variable now, and both grids are committed, so every cell is
reproducible from its own MLflow params:

```bash
# The pilot: 12 cells at 6% of users, one MLflow run each.
OMP_NUM_THREADS=1 python -m src.training.twotower_sweep \
    docs/experiments/twotower-sweep/pilot.json

# The full-dataset runs. full.json's second cell was stopped for budget and
# relaunched from full2.json at four epochs; both files say so.
OMP_NUM_THREADS=1 python -m src.training.twotower_sweep \
    docs/experiments/twotower-sweep/full.json
OMP_NUM_THREADS=1 python -m src.training.twotower_sweep \
    docs/experiments/twotower-sweep/full2.json

# Any single configuration, without a grid. This is v1 exactly:
OMP_NUM_THREADS=1 make train-twotower

# ...and this is v1 with room to speak:
TWOTOWER_LOGIT_TEMPERATURE=0.1 OMP_NUM_THREADS=1 make train-twotower
```

`TwoTowerConfig.from_env` reads `TWOTOWER_EMBEDDING_DIM`,
`TWOTOWER_HISTORY_WINDOW`, `TWOTOWER_BATCH_SIZE`, `TWOTOWER_NUM_SAMPLED`,
`TWOTOWER_EPOCHS`, `TWOTOWER_LEARNING_RATE`, `TWOTOWER_LOGIT_TEMPERATURE`,
`TWOTOWER_CORRECT_POSITIVE_LOGIT`, `TWOTOWER_EARLY_STOPPING_PATIENCE`,
`TWOTOWER_EARLY_STOPPING_MIN_DELTA`, `TWOTOWER_FAISS_NLIST`,
`TWOTOWER_FAISS_NPROBE`, `TWOTOWER_FAISS_EXACT` and `TWOTOWER_SEED`. An unset
or empty variable takes ADR 0006's default, so a clean environment reproduces
the run this page has always reported. Two more belong to the run rather than
the model: `TWOTOWER_USER_SAMPLE_FRACTION` (the seeded pilot subsample) and
`TWOTOWER_RUN_LABEL` (the suffix on the MLflow run name).

### What was not run, and why

- **One seed per cell, again.** Everything here is seed 42, and the pilot's
  116-user warm slice carries real noise: a spread of ±0.005 between two
  two-tower cells is not a result, and the ordering *within* the two-tower
  family should not be read as one. The conclusions below rest on differences
  of 4× to 10×, which no plausible noise floor closes. The noise-floor work the
  previous session named is still owed.
- **No embedding-dimension or history-window sweep.** ADR 0006 defers both to a
  future ADR, and neither is a candidate explanation for a model that loses to
  its own popularity fallback. Both are cheap to run now that the trainer is
  env-driven; neither was run here.
- **No hard negatives, no side features, no attention.** All three are Rung 1
  in [`modeling-roadmap.md`](modeling-roadmap.md), and all three are
  architecture. This sweep was a hyperparameter and budget experiment and
  stayed one.
- **No re-run of item-item, CF or the ranker.** None of them changed. The
  item-item and popularity reference lines above were computed on the pilot
  subsample and on the full dataset through `src/evaluation/` for this section,
  because the candidate-stage table above had no popularity row at
  K_CANDIDATES = 500 to compare a candidate generator against.
- - **Dropped for budget: a full-dataset run at τ = 0.1 and the default learning
  rate.** That cell had the pilot's best loss at ADR 0006's *own* learning rate,
  and it is the minimal one-parameter change a new default would be — so it is
  the configuration the ADR note's proposal would most like to have measured at
  scale. The day held two full runs at roughly two hours each; the slots went to
  the budget question at ADR 0006's exact configuration, and to the pilot's
  best-scoring cell. The proposal therefore rests on the pilot for its choice of
  τ and on the full runs for its claim about what a temperature does and does
  not buy, and the note says so.
- **Dropped for budget: a full-dataset exact-retrieval run.** The pilot's answer
  — 0.0432 exact against 0.0406 IVF, a difference of 0.0026 against a gap to
  popularity of 4.6× — was decisive enough not to spend another two hours
  confirming it at scale.
- **The ADR 0011 cohort is not attached to any pilot cell.** The parquet is
  anchored to the full split's cutoff and `synth_cold.prepare` rightly refuses to
  attach itself to a different one, so a subsampled run skips it and says so in
  its log. Every full-dataset run has it, and the per-bucket table above is from
  those.
