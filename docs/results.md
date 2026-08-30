# Offline results

Every number on this page was measured on one machine on one day, by running the
project's own training entrypoints against the full MovieLens 25M database. None
of it is copied from a paper, an estimate, or an earlier session. Where a run did
not finish, this file says so instead of reporting a partial number.

**Measured:** 2026-08-29
**Dataset:** MovieLens 25M, DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir` (1 156 670 716 bytes, 7 files) — the `md5` recorded in [`data/raw/ml-25m.dvc`](../data/raw/ml-25m.dvc)
**Cold-start cohort:** ADR 0011 `v1`, md5 `9e0c978eff3d45f0985e9e0bbe0551d7`, fingerprint `ae4475f0e063dd4b430092100491838737ee03c8554e68b78cc551efa2e6cfe2`
**Evaluation:** every metric comes from [`src/evaluation/`](../src/evaluation/) — the trainers were run, nothing was computed by hand (non-negotiable #5)

> **2026-08-30 — every number on this page predates the threshold change.**
> All of it was scored with `COLD_START_THRESHOLD = 5`, and every run whose name
> carries no policy suffix was scored under **index-membership** routing. The
> owner then took both decisions at once
> ([ADR 0001's amendment](adr/0001-evaluation-protocol.md#amendment-2026-08-30--the-cold-start-threshold-is-10-online-and-offline)):
> the threshold is **10**, and the threshold — not index membership — is the
> offline routing rule too, which makes `threshold` the default policy and the
> plain run name. Nothing here is retracted: the head-to-head tables in
> ["Both cold-start routing policies, measured"](#2-both-cold-start-routing-policies-measured)
> are still the right comparison between the two policies, because both arms were
> run at the same threshold on the same data. What is no longer true of a *future*
> run is the labelling — an unsuffixed run from now on is a threshold run, and an
> index run is named `<base>-index-routing`. Re-running any of these at threshold
> 10 would move the warm/cold slicing as well as the routing, so the two are not
> directly comparable to the rows below and would need their own dated section.
>
> **Two later sessions appended to this page.** Everything down to "Caveats worth
> writing down" is the 2026-08-29 measurement and is left exactly as it was
> written. [The first 2026-08-30 session](#2026-08-30--the-two-tower-finished-and-both-cold-start-routing-policies-were-run)
> adds two things that change how one section above should be
> read: **the two-tower did run to completion**, so "The two-tower did not run
> to completion" records that session's outcome, not the model's, and both
> cold-start routing policies were measured, so "Routing: what the cohort
> actually measured" now has a counterfactual beside it.
> [The second](#2026-08-30--the-promotion-gates-noise-floor-measured) re-runs
> CF/ALS and the ranker at three seeds each and changes how the whole page
> should be read: **the ranker's warm NDCG@10 moves by 28.7% of its own mean on
> the seed alone, and its overall NDCG@10 by 5.8% — wider than ADR 0001's +3%
> promotion threshold.** Every single-run gap on this page smaller than that is
> a direction and not a result.

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

> *As of the owner's decision later the same day, the polarity is reversed:
> `threshold` is the default and `index` is the opt-out, so an unsuffixed run
> from now on is a threshold run. The runs in this section were made before
> that, and their names are as recorded.*

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
- **No re-seeded runs.** Everything in the first two sessions is one run per cell
  at seed 42. The 4.16% warm-NDCG question in
  [`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) needs a
  noise floor before it can be settled, and that means three to five seeds of the
  same model. Not done here — **done in the session below**, which measured it and
  found the warm question was inside the floor.
- **No two-tower re-training at a larger budget.** The loss curve says that is the
  interesting experiment, and ADR 0004's note names it — but changing ADR 0006's
  configuration is a modelling decision with an approval gate
  ([`modeling-roadmap.md`](modeling-roadmap.md)), not something to slip into the
  run that measures the configuration as pinned.


## 2026-08-30 — the promotion gate's noise floor, measured

The two sessions above are unchanged. This third one exists because both of them
ended on the same sentence — *"no re-seeded runs"* — and the promotion gate could
not be written without them.

[`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) asked which
slice ADR 0001's gate reads, recommended reading the aggregate with a per-slice
non-regression clause, and said the clause's tolerance **must be set from measured
variance rather than judgement**. The owner took that option on 2026-08-30. This
section is the measurement it depends on, and the gate it produced is
[`src/evaluation/gate.py`](../src/evaluation/gate.py).

**Measured:** 2026-08-30
**Dataset, split and cohort:** identical to both sessions above — MovieLens 25M at
DVC version `c3ce6309f6f0ec347a9e0a662c640021.dir`, `T = 1466837397`, train
20,000,075 / holdout 129,683 / test 4,870,337 untouched, 2,641 holdout users
(1,939 warm, 702 cold). The ADR 0011 cohort was regenerated from scratch in this
worktree before any run and reproduced the committed DVC md5
`9e0c978eff3d45f0985e9e0bbe0551d7` and fingerprint `ae4475f0e063…` exactly — a
third independent confirmation, on a third checkout.
**Machine:** the same Apple M3 / 16 GiB / macOS 26.5.2 box and library set.
Postgres 16 in Docker on host port 5433, MLflow on 5001.
**Routing policy:** index membership — the `main` default — on every run below, so
these numbers sit on the same axis as the rest of the page.
**Threads and load:** `nice -n 15`, thread caps of 4, one job at a time. The host
was quiet for the first five runs (1-minute load average 2.8–3.9 on 8 cores) and
**heavily loaded for the sixth** — an unrelated workload took the load average to
39 just as `ranker seed=13` started. As everywhere else on this page the
wall-clocks are upper bounds; the metrics are not affected, and the reproduction
check below is what establishes that rather than assuming it.

**What changed in the code.** `TRAIN_SEED` ([`src/training/seeds.py`](../src/training/seeds.py)),
read by the two trainers that have a stochastic component. Unset it and every
number in the two sessions above comes back unchanged; the run also keeps the
MLflow run name those sessions cite, so only a re-seeded run is renamed
(`<base>-seed<n>`) and tagged `train_seed`.

### Which models have a noise floor at all

Only two, and this is worth stating rather than leaving as an omission:

| Model | Stochastic? | Re-run at three seeds? |
|---|---|---|
| Popularity | No — ranks items by training-window count | No. Deterministic by construction |
| Item-item cosine | No — `CosineRecommender` over a fixed matrix | No. Deterministic by construction |
| CF / ALS | **Yes** — ALS initialises its factor matrices at random | **Yes**, seeds 42 / 7 / 13 |
| LightGBM ranker | **Yes** — which positives are sampled, which negatives fill each group, and LightGBM's own tie-breaking | **Yes**, seeds 42 / 7 / 13 |

A seed parameter on a deterministic model would be a knob that changes nothing,
and "we measured a spread of exactly zero" is not a measurement. The two-tower is
stochastic too and was not re-seeded here: at ~78 minutes a run, three seeds is
four hours, and ADR 0004's verdict on it does not turn on a noise floor.

### The check that had to pass first

Before a spread across seeds means anything, a re-run *at the same seed* has to
reproduce. Both do, exactly:

| Model | Run of record (2026-08-29) | Re-run at seed 42 (2026-08-30) | Logged metrics compared | Differences |
|---|---|---|---:|---:|
| CF / ALS | `d961e6d9ba214edb9283266777aebf40` | `ddc2bd983f52402baf14eb8977de1de4` | 38 | **0** |
| LightGBM ranker | `1d898b02fcc842b6a7283dc6eb9117ad` | `bc4cbcc4e1ba4e02973fb41449e86635` | 36 | **0** |

Every metric MLflow stores, value for value, on a different day in a different
worktree. The ranker also rebuilt the identical training set — 11,374 groups,
238,854 rows, 8,626 positives dropped. **So everything below is seed-to-seed
variation and nothing else.**

### The six runs

NDCG@10, the metric ADR 0001's gate reads:

| Model | Seed | Run id | Warm NDCG@10 | Cold NDCG@10 | Overall NDCG@10 |
|---|---:|---|---:|---:|---:|
| CF / ALS | 42 | `ddc2bd983f52402baf14eb8977de1de4` | 0.057850 | 0.487981 | 0.172182 |
| CF / ALS | 7 | `2a2a21770d744629848dac93e98049bb` | 0.059478 | 0.488002 | 0.173383 |
| CF / ALS | 13 | `ecd38b19bdb54e029d207e05f674c059` | 0.057657 | 0.488165 | 0.172090 |
| Ranker | 42 | `bc4cbcc4e1ba4e02973fb41449e86635` | 0.055444 | 0.563104 | 0.190384 |
| Ranker | 7 | `3509c50fa25247c19ccf769ced6f6213` | **0.049222** | 0.549324 | 0.182153 |
| Ranker | 13 | `36ada5d9548d497389936b0327df9a47` | **0.065491** | 0.545592 | 0.193106 |

Recall@10 over the same runs, since the gate is not the only reader of this page:

| Model | Seed | Warm recall@10 | Cold recall@10 | Overall recall@10 |
|---|---:|---:|---:|---:|
| CF / ALS | 42 | 0.033841 | 0.063780 | 0.041799 |
| CF / ALS | 7 | 0.034323 | 0.063780 | 0.042153 |
| CF / ALS | 13 | 0.033333 | 0.063829 | 0.041439 |
| Ranker | 42 | 0.039422 | 0.079337 | 0.050032 |
| Ranker | 7 | 0.034207 | 0.081655 | 0.046819 |
| Ranker | 13 | 0.044944 | 0.076624 | 0.053365 |

Wall-clocks, all upper bounds, with the 1-minute load average each job started at:

| Model | Seed | Load at start | Wall-clock |
|---|---:|---:|---:|
| CF / ALS | 42 | 2.83 | 88 s |
| CF / ALS | 7 | 3.65 | 89 s |
| CF / ALS | 13 | 2.98 | 88 s |
| Ranker | 42 | 3.91 | 258 s |
| Ranker | 7 | 3.75 | 372 s |
| Ranker | 13 | **39.40** | 499 s |

### The spread

Relative range is `(max − min) / mean`; relative sd is `stdev / mean`. Three
samples, so both are coarse estimates and the range is the one the tolerance uses.

| Model | Slice | Min | Max | Mean | Range | sd |
|---|---|---:|---:|---:|---:|---:|
| CF / ALS | warm | 0.057657 | 0.059478 | 0.058328 | 3.12% | 1.72% |
| CF / ALS | cold | 0.487981 | 0.488165 | 0.488049 | **0.04%** | 0.02% |
| CF / ALS | overall | 0.172090 | 0.173383 | 0.172552 | 0.75% | 0.42% |
| Ranker | warm | 0.049222 | 0.065491 | 0.056719 | **28.68%** | 14.47% |
| Ranker | cold | 0.545592 | 0.563104 | 0.552673 | 3.17% | 1.67% |
| Ranker | overall | 0.182153 | 0.193106 | 0.188548 | **5.81%** | 3.02% |

Three things fall out of this table, and the third is the one that matters.

**CF/ALS's cold slice barely moves (0.04%) because it is not really ALS.** 701 of
the 702 cold users are served by the embedded popularity fallback, which is
deterministic, so that row is measuring the fallback's stability and not the
model's. It is the control that says the harness itself contributes no noise.

**The ranker's warm slice moves by 28.68% of its own mean.** Not the model, not
the data, not the protocol — the seed. The cause is in the trainer's own log:
the seed decides which ≤20,000 positives are sampled from the trailing 30 days
(`RANKER_POSITIVE_LIMIT`), roughly 8,600 of those are dropped for missing the
item-item top-500, and what is left is ~11,350 LambdaRank groups. Seed 42 built
11,374 groups from 20,000 sampled positives; seed 7 built 11,313. **A re-seed is
a different training set, not a different tie-break**, which is exactly the kind
of variation a promotion gate has to be robust to.

**The ranker's *overall* NDCG@10 spread is 5.81% — wider than ADR 0001's own +3%
promotion threshold.** That is the finding with the longest reach on this page.
ADR 0001 chose +3% in May with the reasoning *"noise from retraining randomness
alone can exceed 1%"*; on this pipeline it reaches nearly six times that on the
aggregate the gate reads. **A single seeded run of the ranker cannot establish a
+3% aggregate improvement.** Nothing has been promoted on one, and nothing
should be.

### The tolerance, and what it is worth

Stated once and applied mechanically: **the tolerance for a slice is 2× the
largest relative range observed on that slice, rounded up to the next whole
percentage point, with a floor of 0.5%.**

- *2× rather than 1×* because the gate reads a difference between two
  independently seeded runs, so it carries both runs' noise, not one run's.
- *Rounded up* because a tolerance sitting exactly on an observed maximum will
  refuse a model for a wobble the next re-seed would have produced — and a range
  over three samples underestimates the true range in any case.
- *A 0.5% floor* because anything finer is below the resolution at which this
  page publishes and compares these numbers.

| Slice | Largest range observed | 2× | **Tolerance** |
|---|---:|---:|---:|
| Warm | 28.68% (ranker) | 57.37% | **58%** |
| Cold | 3.17% (ranker) | 6.34% | **7%** |

Those two numbers are the defaults in
[`src/evaluation/gate.py`](../src/evaluation/gate.py).

**A 58% warm tolerance is not a view about how much regression is acceptable. It
is what this pipeline's measurement precision licenses, and saying so is the
point.** No tighter warm clause could refuse a real regression without also
refusing good models at random — the very failure a gate exists to avoid. So the
warm clause is, today, effectively non-binding, and the aggregate clause is doing
the work. That is a defect in the offline pipeline, recorded rather than dressed
up.

**More seeds are not the cheap fix.** The standard error of a three-seed mean is
still 8.4% relative on the ranker's warm slice; averaging enough runs to bring the
floor under 3% would take on the order of a hundred. The cheap fix is a larger
ranker training sample — `RANKER_POSITIVE_LIMIT` is 20,000 against a trailing
window holding far more — and it is now a measurable change rather than a
preference. Named on the Phase 3 platform backlog.

### What the gate says about the runs already on this page

The comparison the memo was written about — the LightGBM ranker as challenger,
CF/ALS as incumbent — through `python -m src.evaluation.gate` at the tolerances
above:

```
$ make gate CANDIDATE=1d898b02fcc842b6a7283dc6eb9117ad \
            INCUMBENT=d961e6d9ba214edb9283266777aebf40
PROMOTE — ndcg@10
  overall: overall ndcg@10 gained 10.57% (0.172182 → 0.190384) against a required +3.00%
  warm: warm ndcg@10 regressed 4.16% (0.057850 → 0.055444, n=1939), within the 58.00% tolerance
  cold: cold ndcg@10 improved 15.39% (0.487981 → 0.563104, n=702)
```

**It would promote — because the −4.16% warm regression is inside the measured
floor, by a wide margin.** The memo's option (c) said the verdict would be
"rejected at any tolerance under 4.16%; promoted above it", and the measurement
says the floor is thirteen times 4.16%. So the warm regression that made the
question urgent is, on the evidence, not a result at all.

The clearest way to see that is to run the same comparison seed by seed —
challenger and incumbent both at 42, both at 7, both at 13:

| Seed pair | Overall | Warm | Cold | Verdict |
|---:|---:|---:|---:|---|
| 42 | +10.57% | **−4.16%** | +15.39% | promote |
| 7 | +5.06% | **−17.24%** | +12.57% | promote |
| 13 | +12.21% | **+13.59%** | +11.76% | promote |

**The same experiment reports the warm effect as −4%, −17% and +14%. Its sign is
not determined by the data.** The 2026-08-29 run happened to land in the middle.

Averaging the three runs per model — the comparison a Phase 4 DAG should actually
be making — gives the most trustworthy reading available today:

| Slice | CF/ALS (mean of 3) | Ranker (mean of 3) | Relative |
|---|---:|---:|---:|
| Warm NDCG@10 | 0.058328 | 0.056719 | **−2.76%** |
| Cold NDCG@10 | 0.488049 | 0.552673 | +13.24% |
| Overall NDCG@10 | 0.172552 | 0.188548 | **+9.27%** |

Seed-averaged, the warm regression shrinks from −4.16% to −2.76% and the aggregate
gain from +10.57% to +9.27%. The gate promotes on these too — and here it promotes
for a reason worth having, since the aggregate's +9.27% is comfortably outside the
5.81% single-run spread that a one-seed comparison could not clear.

**Nothing has been promoted.** `pipelines/` is still empty, the gate is a library
and a CLI rather than a DAG, and the served bundle is unchanged. What this section
establishes is that the gate now has a defensible rule and a measured number, and
that the number is currently large enough to be a finding in its own right.

### Reproducing this

Same prerequisites as the sections above — Postgres holding the 25M ratings and
the ADR 0011 parquet present. Then:

```bash
# Three seeds per stochastic model, one job at a time. Unset TRAIN_SEED is 42,
# which reproduces the runs of record above metric for metric.
TRAIN_SEED=42 make train-cf
TRAIN_SEED=7  make train-cf
TRAIN_SEED=13 make train-cf

TRAIN_SEED=42 make train-ranker
TRAIN_SEED=7  make train-ranker
TRAIN_SEED=13 make train-ranker

# The gate over any two runs. Exit 0 promotes, 1 refuses, 2 declines to decide
# (two different K values, or a killed run with parameters and no metrics).
make gate CANDIDATE=<run id> INCUMBENT=<run id>
```

`TRAIN_SEED` is read by `train-cf` and `train-ranker` only; `train-popularity` and
`train-itemitem` are deterministic and ignore it.
