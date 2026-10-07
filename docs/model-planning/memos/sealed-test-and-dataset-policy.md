# Memo — the sealed test partition, and when 25M stops being enough

**Date:** 2026-09-04. Covers D-005 and D-007 in the
[decision register](../03-decision-register.md), and is the written half of
[M0-08](../work-items.md).

**Status.** D-005: this note states the policy the program operates under; the owner ratifies it by
approving the change, and any later move of the trigger is an owner decision recorded here. Nothing
below relaxes [guardrail 4](../01-program-guardrails.md) — it says what that guardrail means when
somebody actually has to act on it. D-007 is **open**. There is a recommendation and a list of
conditions that would settle it; the recommendation is not the decision, and a plan item cannot make
it one.

## What is sealed, and how much of it there is

[ADR 0001](../../adr/0001-evaluation-protocol.md) puts the cutoff `T` at the 80th-percentile
interaction, gives the holdout the 28 days from `T`, and reserves everything after that. The
implementation is [`src/data/split.py`](../../../src/data/split.py) — one pure function over the
ratings frame, with ties landing in the later slice so a row at exactly `T` cannot be trained on and
scored in the same run.

| | Boundary (epoch) | UTC | Rows | Share |
|---|---:|---|---:|---:|
| Train | `t < 1466837397` | → 2016-06-25 06:49:57 | 20,000,075 | 80.00% |
| Holdout | `[1466837397, 1469256597)` | 2016-06-25 → 2016-07-23 | 129,683 | 0.52% |
| **Sealed** | `t >= 1469256597` | 2016-07-23 → 2019-11-21 | 4,870,337 | 19.48% |

Numbers from [`docs/eda.md`](../../eda.md) §7–§8, measured on DVC revision
`c3ce6309f6f0ec347a9e0a662c640021.dir`.

The first thing to notice is that the split is *never materialized*. There is no test table, no test
parquet, no `--partition` flag anywhere in `src/`. Postgres holds all 25,000,095 rows and the
partition exists only as a predicate that every process re-derives. Sealing is therefore a property
of **behaviour**, not of storage: nothing is locked, and a one-line change to any trainer would open
it silently. That is the fact the rest of this note is built around.

## Re-verifying the audit

The D-005 row says a repository audit found no test evaluation. That claim carries the whole
position, so it was re-run on 2026-09-04 against `da5b88d`, against `feat/sasrec` (the unmerged
lineage carrying the newest trainers), and against `origin/main` at `646462c`.

**What holds.** Every reference to `split.test` in `src/`, `synthetic/`, `pipelines/` and
`notebooks/` — on all three lineages — is the same row-count line in a trainer's startup log:

```python
logger.info("Train=%s Holdout=%s Test=%s (cutoff=%d)", ..., f"{len(split.test):,}", split.cutoff)
```

Six trainers on `origin/main` (`popularity`, `cf`, `itemitem`, `last_item`, `twotower`, `ranker`),
five on `da5b88d` and `feat/sasrec`. `git log --all -p -S"split.test"` over those directories returns
exactly one distinct line of source across the entire history of every branch — that one. No commit
ever added and later removed an evaluation path. `src/evaluation/` has no entry point that can
receive the test frame: `evaluate()` takes a holdout mapping, and `ProtocolManifest` carries
`train_cutoff`, `holdout_start` and `holdout_end` but no test boundary at all. The SASRec trainer
fits on `split.train` and scores against `split.holdout`; the ranker samples positives from
`split.train` and builds its `FeatureIndex` from the same frame.

**Three things that weaken it, none fatal.**

*The sealed rows are handed around as a live object.* Every trainer calls
`synth_cold.prepare(split, ...)` with the whole `TemporalSplit`, test frame included, and
`FeatureIndex.build` receives the train frame only because the caller chose to pass it. There is no
type, no wrapper and no assertion between a trainer and the sealed rows. The audit proves nobody has
reached for them; it does not prove anyone would notice if a future run did. That gap is what the
[run-template declaration](../experiments/template.md) exists to close, and it is the reason the
declaration asks for a measured timestamp rather than a checkbox.

*One live path already aggregates over sealed-window rows.* `src/features/materialize.py` defaults
`as_of` to `datetime.now(UTC)`, and `src/release/bootstrap.py` calls `materialize(settings)` with no
`as_of` at all. Against the full table that computes user and item aggregates over every rating up to
now, which includes all 4.87M sealed rows. For **serving** this is correct and uninteresting — a
production feature value should summarise everything that has happened. It matters because ADR 0009
still commits ranker training to `get_historical_features`, and the day training reads a
materialized snapshot instead of `FeatureIndex`, features computed over the sealed window enter the
training set. That is contamination through the back door, with no `split.test` reference to grep
for. It is open as D-009 and costed in
[`feature-source-boundary.md`](feature-source-boundary.md); this memo only records that the sealed
partition is one of the things that decision is about.

*The protocol manifest cannot answer the question.* Phase 0 session 2 lists a "sealed-test flag"
among the fields protocol identity should carry
([`../phases/00-reconcile-and-foundation.md`](../phases/00-reconcile-and-foundation.md)), and
`ProtocolManifest` does not have one. So today a reviewer establishes "this run did not read test"
by reasoning from absence — no test metric was logged, therefore none was computed. That is sound
and it is weak, and it is why the template now asks the run to state the claim positively. Adding
the field to the manifest is code, not documentation; it belongs with M0-03's follow-up and is not
attempted here.

*And one limit the audit cannot reach at all.* This is an audit of committed code and history. It
says nothing about an uncommitted notebook, a psql session, or a glance at post-2016 data that
influenced a choice. Only the owner can close that, and the contamination procedure below is what
happens if the answer is ever "yes".

## Declared reads of the sealed partition

The policy's value is that nothing touches this partition silently, which includes reads that are not
model evaluations. This is the log.

**2026-09-05 — catalog reachability by year. Owner-approved in advance.** Computed: the fraction of
test-era interactions whose target movie appears in the training frame, overall and by calendar year.
No model was run, no metric was scored, no model or configuration choice was informed by the result.
It was requested to bound a question the holdout cannot answer — whether a model trained on pre-2016
data can reach what people watched years later.

| | |
|---|---:|
| Test rows | 4,870,337 over 30,335 users, 55,019 distinct movies |
| Rows on movies present in train | 4,433,120 (91.02%) |
| Rows on movies train never saw | 437,217 (8.98%) |
| Distinct movies train never saw | **24,488** |

| Year | Test rows | On movies present in train |
|---|---:|---:|
| 2016 | 669,007 | 98.28% |
| 2017 | 1,689,935 | 93.76% |
| 2018 | 1,310,761 | 88.65% |
| 2019 | 1,200,634 | 85.72% |

Two readings, and the second is the more useful one.

**The structural ceiling decays slowly.** Reachability falls from 98.3% to 85.7% across three and a
half years. That is a bound on recall, not a prediction of it — actual quality would degrade faster,
because tastes and popularity move even among films the model knows. But catalog turnover alone does
not collapse the model, which is a genuinely reassuring answer and was not obvious beforehand.

**It also sizes the cold-item problem properly.** 24,488 distinct movies appear in the test era that
training never contained — against the 3,376 zero-rating movies that ADR 0017's offline slice can
see. So the production case that ADR motivates is roughly seven times larger than the offline
population used to justify it, and it is genuinely about newly-added films rather than deep-catalog
obscurity. The ADR's own caveat — that the offline population and the production need are different
things — is confirmed rather than softened by this.

This read consumed no evaluation window. The 28-day final window defined in ADR 0001's 2026-09-05
amendment remains untouched and unspent.

## Sealed, operationally

1. No run of record reads any interaction with `timestamp >= 1469256597` for fitting, scoring,
   feature construction, hyperparameter choice, early stopping, threshold setting, or slice
   definition. Logging its row count is not a read; the count is already public in this memo.
2. Development evidence comes from the 28-day holdout and, once M0-07 lands, from rolling-origin
   backtest windows that all end at or before `holdout_end`. Repeated decisions against one fixed
   holdout is the real overfitting risk here (R-02), and rolling windows — not the test partition —
   are the answer to it.
3. Every run of record declares the partition it read and the latest event timestamp that entered
   it. The mechanism is the [experiment template](../experiments/template.md); a run whose
   declaration is missing or contradicted by its own logged parameters is **invalid**, not merely
   weak, on the same footing as a leakage failure.
4. Nothing about the seal is negotiable for convenience. "The holdout is small" and "the cold slice
   is noisy" are arguments for a different development split, never for opening the test partition
   early.

## The unseal trigger

**The owner decides, once, in writing.** Not a gate, not a threshold, not an automated condition —
there is no rule that should be allowed to open this partition without a person choosing to.

The owner may unseal when, and only when, all of the following are already true and recorded:

- a candidate bundle has reached **serving eligible** as
  [guardrail §Model-development versus serving eligibility](../01-program-guardrails.md) defines it:
  it clears the stage-local gate, the end-to-end NDCG guardrail, artifact export equivalence,
  latency, reliability and audit checks;
- the DVC revision, derived snapshot hash, model family, configuration, seed set, protocol manifest,
  every threshold and tolerance, and the artifact checksums are **frozen and committed** — frozen
  meaning that changing any of them after the read requires a new release candidate and a new
  window, not an edit;
- the owner has named the bundle a release candidate with an identifier, and the unseal commit is
  recorded in this memo and in the decision register.

The purpose of the read is to estimate how the frozen candidate behaves on data nobody tuned
against. It is not a gate — it cannot promote a model that failed the holdout gates, and a pass does
not add evidence for a model that already passed them. It is a number published as-is.

### What "the test partition" means at read time

This needs saying because ADR 0001 does not, and the ambiguity would otherwise be discovered
mid-unseal. The sealed region is 4.87M rows across **3.4 years**, and post-cutoff rating velocity is
~60% higher than pre-cutoff ([`docs/eda.md`](../../eda.md) §8). Scoring a model trained to a 2016
cutoff across 2016–2019 measures catalog drift and staleness at least as much as model quality, and
the result would not be comparable to any 28-day holdout number the program has produced.

The recommendation, for the owner to confirm at unseal time: the final evaluation window is the
**28 days immediately after `holdout_end`** — `[1469256597, 1471675797)`, 2016-07-23 to 2016-08-20 —
which is the same shape, the same duration, and the same evaluator as the development protocol, so
the numbers mean the same thing. The remaining ~3.3 years stay sealed. That is not hedging; it is
what makes "a failed release returns to development with a new future test window"
([`../workstreams/evaluation-and-gates.md`](../workstreams/evaluation-and-gates.md)) a real option
rather than a phrase. A program that spends all 3.4 years on one read has no second window and no
way to ever measure a later candidate cleanly.

### The read is one-shot

One window, one run, one publication, whatever the outcome. The result goes into `docs/results.md`
and the scorecard with its run IDs and protocol hash, including if it is bad. After the read:

- no configuration, threshold, feature, seed, or architecture change may cite the test number as its
  reason — that is tuning on test with extra steps, and it is the failure mode the seal exists to
  prevent;
- the window that was read is spent. A later candidate is measured on a later window, and its number
  is not comparable to the first;
- if the result is bad, the honest move is to record it, return to development on holdout and
  rolling windows, and treat the disagreement between holdout and test as its own finding worth
  understanding.

## What unsealing costs

Worth stating plainly, because "we can always unseal later" is only true once.

- **A window, permanently.** One 28-day period of clean, never-tuned-against data — on the order of
  130k interactions, since the holdout's 129,683 rows cover the 28 days immediately before it at the
  same post-cutoff velocity. The sealed region holds roughly forty-four such windows, but each is
  further from the training cutoff than the last, so the *good* ones are scarce — every window spent
  moves the next candidate's estimate further into drift territory.
- **The option to be surprised.** The value of a sealed set is entirely in the fact that no choice
  was informed by it. Read it early, and every subsequent decision is made by someone who knows the
  number, whether or not they cite it.
- **A freeze.** The preconditions above are not paperwork; they mean modeling stops on that bundle
  while the read happens. Unsealing casually means paying that cost over and over.

Against those, the cost of *not* unsealing is small: holdout plus rolling backtests is enough to
choose between models. The asymmetry is why the trigger sits at "release candidate", not "curiosity".

## If it turns out to be contaminated

Contamination is any case where the sealed window influenced a decision that is still standing —
committed code reading past `holdout_end`, a materialized feature source whose `as_of` is at or
after it, or the owner recalling a manual look at post-cutoff data that shaped a model choice.

The procedure, in order:

1. **Declare it in writing, dated, in this memo and the decision register.** Before deciding what to
   do about it. A contamination that is known and recorded is a bounded problem; one that is
   quietly fixed is a permanent asterisk on every number the program has published.
2. **Scope it.** Which runs, which decisions, and through which path. The three vectors above are the
   ones known to exist; the identifying evidence is the run's protocol manifest, its feature source
   and as-of, and its code SHA.
3. **Retire the affected window.** Every sealed window whose data reached a standing decision is
   burned. Define a new final window strictly after the last contaminated timestamp, and record the
   new boundary here.
4. **Mark, do not delete, the affected runs.** Tag them contaminated with the reason, keep them for
   audit, and exclude them from aggregates and gates — the same treatment
   [`../experiments/README.md`](../experiments/README.md) gives an invalid run.
5. **Re-decide anything that rested on them.** A promotion or a stop that was justified by a
   contaminated run has no justification until it is re-derived on clean evidence.
6. **Close the path.** If the vector was code, it gets a test. If it was a materialization default,
   the fix is an explicit `as_of` on the training path, not a note asking people to remember.

The expensive step is 3, and it is expensive in proportion to how late the contamination is found.
That is the argument for the template declaration being a per-run habit rather than a pre-release
audit.

## D-007 — 25M or 32M (open)

**Recommendation: stay on MovieLens 25M.** Not decided, and deliberately not decided here.

The case for staying is that a dataset migration invalidates everything. `T` moves, so train,
holdout and the sealed window all move; the catalog fingerprint changes; every recorded number in
`docs/results.md` becomes a measurement of a different protocol; the ADR 0011 cold-start cohort is
anchored to the current cutoff and would have to be regenerated (`CohortCutoffMismatchError` exists
precisely to make that failure loud). The program's whole value is comparability across weeks of
runs, and migrating spends that to gain rows.

The conditions that would settle it — any one is enough to reopen:

- **A named slice is underpowered and the underpowering is the dataset's fault.** The live candidate
  is the cold slice: 2,641 holdout users in total, of whom 701 were brand new at the EDA snapshot's
  threshold of 5, and 710 count as cold under the current threshold of 10 — the population the
  ranker's cold tolerance was measured over. A 5% guardrail resting on ~700 users is itself noisy.
  But note *why* the slice is small: it is the 28-day holdout window and MovieLens's ≥20-ratings
  floor, not the row count, and 32M carries the same floor. **Widening the holdout window or adding
  rolling origins (M0-07) is the cheaper lever, and it should be tried and shown insufficient
  before a migration is considered.** If it is tried and the slice is still underpowered, that is a
  real trigger.
- **A model hypothesis needs events the 25M window does not contain.** The 25M data ends
  2019-11-21. A rung that depends on post-2019 behaviour, on a larger or more recent catalog, or on
  denser recent sequences has a genuine claim. SASRec does not — sequence models need long
  histories, and 25M has 21.5 years of them.
- **Item cold-start becomes the object of study.** 3,376 titles in the 25M catalog have no ratings
  at all. A rung specifically about new-item retrieval might want the newer catalog. That is a
  hypothesis with its own ADR, not a data-hygiene argument.

What a migration would require if it is ever taken: a new data ADR; re-verification of the published
32M counts and date range against GroupLens's own release notes (they are not measured anywhere in
this repository and must not be cited as if they were); a fresh DVC revision and derived snapshot
hash; a recomputed split with new boundaries recorded in `docs/eda.md`; a regenerated cold-start
cohort; and an explicit statement that pre-migration and post-migration numbers are not comparable,
enforced by the protocol manifest refusing the comparison rather than by a footnote.

The default until then is the cheap one: **carry both dataset identity fields in every run of
record**, which the manifest already does through `raw_data_revision` and `derived_snapshot_hash`, so
that if the migration ever happens the boundary is visible in the data rather than reconstructed
from memory.

## How we would know we are wrong

*About the seal being intact.* The audit is a grep over committed code. It would be wrong if the
sealed window reached a decision by a route that has no `split.test` in it — the materialization
path is the known one, an uncommitted notebook is the unknowable one. The signal that we were wrong
is a holdout-to-test disagreement at unseal time that is *smaller* than it should be: a model that
matches its holdout number too closely on data it has supposedly never seen is evidence that it has.
The cheap ongoing check is the template's latest-event-timestamp declaration, which catches the
feature path that a grep does not.

*About the trigger being at the right place.* Setting it at "serving eligible release candidate"
assumes the holdout and rolling windows are sufficient to choose between models. We would learn that
is wrong if a candidate that clears every offline gate is visibly worse in production, repeatedly —
that would mean the development split stopped being representative and the program needs a
mid-course held-out read, not that the seal was the wrong idea. The response would be to define an
intermediate window and spend it deliberately, not to abandon sealing.

*About the 28-day final window.* Matching the holdout's shape makes the number comparable, and buys
that comparability with a window that sits only four weeks past the training cutoff — so it measures
quality with almost no staleness. If the real question at release time turns out to be "does this
model survive a year of drift", a 28-day window answers the wrong question. We would know from the
release candidate's own purpose: if the bundle is being deployed to serve for months without
retraining, the honest final window is longer, and that is an owner decision to make at unseal time
with the trade-off — comparability against realism — stated openly.

*About staying on 25M.* The recommendation is wrong if a slice's confidence interval stays too wide
to decide with after M0-07's rolling windows are in place. That is a measurement, not an opinion, and
it is available before any migration is committed to — which is exactly why D-007 should stay open
until M0-07 has run rather than being settled on judgement now.

## Proposed amendment 2026-10-05 — the trigger under serving parked (not approved)

**Status: proposed, awaiting the owner's written approval. Until that approval is recorded here, the
unseal trigger above stands unchanged and the sealed partition stays closed.**

The trigger requires a bundle that has reached **serving eligible**. Under the owner's 2026-10-05
decision D3 nothing is being served, so no bundle can reach serving eligibility during Phase A, and
the one-time read in work order WO-8 of
[`../phase-a-work-orders.md`](../phase-a-work-orders.md) could never be triggered. The proposed
wording replaces the first condition of the trigger with:

> a frozen offline release candidate that passed the WO-5 and WO-6 gates.

Everything else in the trigger is unchanged: the owner decides once, in writing; the DVC revision,
derived snapshot hash, model family, configuration, seed set, protocol manifest, thresholds,
tolerances and artifact checksums are frozen and committed; and the owner names the release
candidate with an identifier. WO-8 adds that the list of models scored in the same pass — the release
candidate plus every baseline in the README table — is approved in the same writing. The window is
the one ADR 0001's 2026-09-05 amendment already sets, `[1469256597, 1471675797)`, 2016-07-23 to
2016-08-20; each frozen configuration is retrained on all ratings before `1469256597` and scored
once; every number is published as it comes out, including a bad one; and the window is then spent.

What the change gives up, stated before it is approved: the serving-eligible condition included
artifact export equivalence, latency, reliability and audit checks, and the proposed wording drops
them. A model read on the sealed window under this wording has not been shown to serve. The number is
still an honest offline estimate on data no decision has used, which is what the read is for; it is
not evidence that the model is ready to serve.

## Contamination record — 2026-10-05 (O-25)

**Declared under step 1 of the procedure above, before anything is decided about it.** The owner's
answer is [O-25](../owner-decisions.md), and the run record is
[`../experiments/wo1-restore-trainer-of-record.md`](../experiments/wo1-restore-trainer-of-record.md).

**What happened.** WO-1's preflight found the leak in the pilot path's split. A pilot keeps every
interaction of 6% of users (`subsample_users`, seed 42: 9,752 users, 1,517,399 ratings), and the
trainer then ran `temporal_split` on that *subsample*. The subsample's own 80th-percentile cutoff
is **1471288304** and its `holdout_end` is **1473707504**. The cutoff falls 23.5 days *after* the
full split's sealed boundary, 1469256597. There is no `split.test` anywhere on this path. The
boundary moved with the population, which none of the three known vectors in the section above
describes.

**Which rows.** The 6% split's train and holdout against the boundary and against the one-time final
window `[1469256597, 1471675797)` that ADR 0001's 2026-09-05 amendment reserves for WO-8:

| Slice | Rows | At or after 1469256597 | Inside the WO-8 window | Later sealed rows |
|---|---:|---:|---:|---:|
| train (fitted) | 1,213,918 | 4,870 | 4,870 | 0 |
| holdout (scored) | 7,528 | **7,528 (all)** | 1,126 | 6,402 |

**Which runs.** Every run on the seed-42 6% subsample before O-25. They are identified by their
recorded split, train 1,213,918 / holdout 7,528 rows, or by the 9,752-user sample:

- SASRec pilots, 2026-09-04/05: the BCE-vs-gBCE pilot (`docs/experiments/sasrec/pilot-6pct.json`)
  and the reference values 0.3186 / 0.3103 / 0.3258 / 0.2957.
- The 6% item-item incumbent in the M0-14 seed-dispersion study (warm recall@500 0.3587;
  `experiments/tolerance/surrogate-seed-noise-6pct.json`).
- SASRec all-positions pilot `f837955c832440069dd8c1316a2ad0c6` and the retained attempt
  `833812eea8a341e9953285b4145cf9b8` (2026-09-11).
- Two-tower v1 12-cell pilot sweep (`docs/experiments/twotower-sweep/pilot.json`; run ids in
  `docs/results.md`).
- Two-tower v2 bounded pilot, 2026-09-04 (`v2-pilot.json`).

The 0.5% pilot sample's split (cutoff 1466662482, `holdout_end` 1469081682) stays inside the
boundary and is not affected. Full-data runs compute the full split and are not affected.

**Decisions that rested on these runs.** Under step 5 these are listed here and not re-decided here:

- ADR 0016's choice of BCE over gBCE for the frozen v1 cell. It was made on the 6% pilot.
- The two-tower pilot findings and ADR 0015's Gate 1 arm reading.
- ADR 0020's 6% leg of the cell-0 control. Its full-data leg fired stop rule 2 independently.

**The WO-8 window.** 4,870 fitted rows and 1,126 scored rows of `[1469256597, 1471675797)` entered
pilot runs. No full-data model and no gate verdict was fitted or scored on them. Whether the window
is still fit for the one-time read in WO-8 (step 3, retire the window) is the owner's decision. This
note records the fact and does not make that call.

**Closing the path (step 6).**

- **Closed for SASRec.** On `feat/wo1-restore-trainer-of-record` (PR #194), `run_once` splits a
  subsample at the full frame's cutoff (`temporal_split(..., cutoff=temporal_cutoff(full))`). It
  also refuses any run whose fitted or scored rows reach the full frame's boundary
  (`SealedPartitionError`). Both are under test. The new 6% pilot protocol hash is
  `sha256:faf2828d08a0b0ecf23993fcfaf037134359017e20c7601b53da7e2ebecc22bc`.
- **Still open.** The two-tower, item-item and last-item trainers subsample and then compute their
  own quantile in the same way. WO-1 does not change them. Any pilot they run before they are fixed
  repeats this read.
- **Tagging (step 4).** The affected MLflow runs live on the shared tracking server and have not
  been tagged yet.

### Scope, re-measured across every recorded subsample — 2026-10-05

Step 2 asks which runs. The declaration above found them through the 6% seed-42 split. This sweep
re-derives what every recorded subsample read, using the logic those runs ran: draw the users, then
split the draw at its own 80th-percentile cutoff. It covers every cells JSON with a
`sample_fraction` (SASRec and two-tower), the tolerance study, the 0.5% pilot, and every run in the
shared tracking store whose logged cutoff differs from the full split's 1466837397. It reads the
pinned CSV snapshot. Every computed cutoff and row count matches what the runs logged, except for
one draw the CSV cannot reproduce, described below the table.

| Sample (fraction, subsample seed) | Specs and runs | Cutoff | `holdout_end` | Latest fitted | Latest scored | Sealed rows read |
|---|---|---:|---:|---:|---:|---|
| 0.1%, 42 | SASRec smoke `bd2a9200…` (no cells file) | 1438726569 | 1441145769 | 1438726317 | 1440979410 | none |
| 0.5%, 42 | `sasrec/pilot.json` (six runs) | 1466662482 | 1469081682 | 1466662479 | 1469075882 | none |
| 6%, 42 | `sasrec/pilot-6pct.json`, `tolerance/surrogate-seed-noise-6pct.json`, `sasrec/all-positions-pilot-6pct.json`, `twotower-sweep/pilot.json`, `twotower-sweep/v2-pilot.json`, the two 2026-09-05 two-tower diagnostics, the 6% item-item incumbent | 1471288304 | 1473707504 | 1471288301 | 1473706430 | 4,870 fitted, 7,528 scored |
| 1%, 42 | ranker smoke runs `c5d76476…`, `ec710ab7…`, `80061438…` (no cells file) | 1473304598 | 1475723798 | 1473304591 | **1475668075** | 1,792 fitted, 1,054 scored |
| 1%, another draw | ranker smoke run `b5550b18…` | 1469030884 | 1471450084 | before 1469030884 | 1471446464 | 1,824 scored |
| 6%, 42, cut at the full split (O-25) | the five WO-1 runs and the gBCE re-run below | 1466837397 | 1469256597 | 1466819964 | 1469247943 | none |

**The 1% ranker runs are new to this record, and they reach furthest.**
`src.training.sasrec_ranker`'s `prepare_shared`, which the score-feature runner also loads through,
subsamples at its own `SUBSAMPLE_SEED` 42 and splits the draw at the draw's own quantile, exactly as
the 6% path did. Four smoke runs used it on 2026-09-05. They proved the ranker runners end to end and
informed no decision. The three on the seed-42 draw reach furthest: their latest scored rating is
**1475668075** (2016-10-05 11:47:55 UTC), 22.7 days after the 6% split's `holdout_end`. Of the 1,792
sealed rows they fitted, 652 lie in the old WO-8 window. None of the 1,054 rows they scored does.

`b5550b18…` logged a different derived-snapshot hash from the other three (`3af47050…` against
`c9b85551…`), so it drew a different 1% of users, and the pinned CSV does not reproduce that draw.
Its fitted rows all precede its cutoff, which is before the boundary. Its 36 evaluated users and
their 1,844 holdout rows are reproduced exactly from its per-user export. 1,824 of those rows are
sealed, all inside the old window, and the latest is 1471446464.

The two-tower OpenMP validation run (`ed694c47…`, 2026-09-05) used the 6% split by its own record and
was logged to a throwaway store. The all-positions attempt `833812ee…` used the same split.

### The tag list (step 4), and the rule for tagged runs

The owner's 2026-10-05 decision: every affected run carries `sealed_window_contaminated=true` and
`sealed_window_declared=2026-10-05` in the shared tracking store. The list is every run that fitted
or scored on a per-subsample cutoff past the boundary. The original run ids were preserved when the
September runs were imported.

**Rule going forward: a tagged run is kept, and is never used as a comparison.** It is not a gate
incumbent or challenger. It is not an input to a tolerance or seed-dispersion study, not a reference
value for a pilot, and not part of any aggregate. Its numbers stay in
[`../../results.md`](../../results.md) for audit, under a dated mark.

| Group | Runs | Count |
|---|---|---:|
| Two-tower v1 learning-rate and temperature pilot, 2026-08-30 | `425d5b966d4b47c893c8aec05c7ee75a`, `f55e543fcda944e5854df61769f8aabd`, `a0f48c0ae4c2477c8b865418807408e3`, `dee1f21169cf4de8b979b4fe0f868dac`, `6a35f8688e504848b7e811b8007fcb06`, `bed3da944c6a410ba852d1e4ec23d9c6`, `58f3cfc00fec4ce0a1f6d1e795ad9ddc`, `e53cfa588229410ba7d349ef920eddda`, `be91e8dfce96432c94e3d2ac09d11a74`, `fbf5ed6693474deba5e4853e6894d5fd`, `72a482de7faa450dbc947b4ba4e9bdcb`, `36b4c21602ac46b9ad32cefd396a310c` | 12 |
| Two-tower v2 Gate 1 pilot, 2026-09-04 | `3b41a19854b94b329fd9424a0e65f773`, `7e803d6c93d3480aa3c1ff50b824d6ff`, `736d1156cd1a414aba1fdb5614a0aeab`, `dfa5143725f346859522a72f170a8f19`, `2348ef2b16cc49bd944df4964a9dc6e9` | 5 |
| Two-tower FAISS row-mapping diagnostic, 2026-09-05 | `8a22ed513b8f457eb0d5f93b826dc82a` | 1 |
| SASRec BCE-versus-gBCE pilot, 2026-09-04: the valid pair, then four diagnostic-only runs from before commit `1d189a8` | `0c600f9dd15e47a99cb9fa364b23ed02`, `fb63a3ae96c64205ba5e57e5ca4b0611`, `b4b3a7ec2bc8483ea6c1ca9350b524dd`, `3d6bb37e2bcc4173912db9960d71fdf5`, `bf95be79d4154722bfde98323161dd9f`, `2706e0e6cb5c48d590a132640823ee95` | 6 |
| SASRec all-positions pilot, 2026-09-11 | `f837955c832440069dd8c1316a2ad0c6` | 1 |
| Ranker smoke runs on the 1% samples, 2026-09-05 | `c5d76476e6244c2b8db96f16a70863d1`, `ec710ab7ddf442b5803306c572d4dbfe`, `8006143897dd4ac18044f47d5fc2fb0e`, `b5550b184f6d48f181d5fd7d5206494d` | 4 |

**Tag status: set 2026-10-05 on all 29 runs in the shared store.** Each carries
`sealed_window_contaminated=true` and `sealed_window_declared=2026-10-05`. A search of the store
returns exactly these 29, and none of the WO-1 runs.

**On the list but untaggable**, because they are not in the store:
- `833812eea8a341e9953285b4145cf9b8`, the all-positions attempt. It went to an earlier server whose
  artifact root the host could not write. Only its local model archive survives.
- `ed694c47caa04e9b89bda5195c052693`, the OpenMP validation run, logged to a throwaway store.
- The M0-14 seed-dispersion runs (`sasrec-noise6-bce-neg32-s7`, `-s13`, `-s21`) and the 6% item-item
  incumbent they were compared with. No run id for them is recorded in the tree or in PR #153, and
  the store holds no run under those names.

**Not on the list:**
- The five WO-1 runs. They are cut at the full split (O-25) and are clean.
- The 0.1% and 0.5% runs. Their splits end before the boundary.
- `982b5cdb903f4c54ba1831aa9cc59a1e`, an empty ranker run left `RUNNING` on 2026-09-05. It logged no
  parameter and no metric, so nothing shows it read a split.

### What rested on the 6% pilots (step 5)

Each decision below was taken, at least in part, on a pilot that read the sealed window. The status
column says what now stands in for that evidence.

| Decision | Evidence on the contaminated split | Status |
|---|---|---|
| **BCE over gBCE for SASRec v1.** ADR 0016's pilot outcome; `full.json` froze the v1 cell on it. | BCE `0c600f9d…` 0.3186 against gBCE `fb63a3ae…` 0.2937 warm recall@500, seed 42, 115 warm users: BCE ahead by 8.5%. | **Re-run on the clean protocol, once, 2026-10-05. The choice holds.** gBCE run `4f87185e…` reads warm recall@500 0.3355 against WO-1's BCE seed-42 pilot `7baeb7d0…` at 0.3625, same seed and protocol: BCE ahead by 8.1%, against 8.5% in September. gBCE also sits below all four clean BCE seeds (0.3611–0.3936), 2.4 of their standard deviations under their mean. It is one gBCE seed on 108 warm users, so it confirms the direction, not the size of the gap. |
| **The all-positions verdict.** ADR 0016's W28 note and the 6% leg of ADR 0020's cell-0 control. | All-positions `f837955c…` 0.1822 against copied-prefix `0c600f9d…` 0.3186, −42.82%. | **Re-measured by WO-3**, not yet run: its fast-trainer pilots are judged against WO-1's clean reference set. The full-data leg (`fd2ee9f6…` 0.4856 against 0.5092, −4.62%) is clean and fired ADR 0020's stop rule 2 on its own, so O-22 stands. |
| **The seed-noise and tolerance study.** M0-14, and O-5's working answer. | SASRec seeds 7, 13 and 21: 0.3103, 0.3258 and 0.2957 (relative range 9.71%), with 0.3186 at seed 42. The 6% item-item incumbent read 0.3587. | **Superseded** by WO-1's four clean seeds, 0.3625, 0.3611, 0.3714 and 0.3936: mean 0.372174, sample sd 0.015013. No full-data claim used the old spread as a tolerance. |
| **The two-tower pilots.** ADR 0006's learning-rate and temperature sweep, ADR 0015's Gate 1 arms, and the FAISS row-mapping diagnostic. | v1 sweep band 0.0392–0.0520 over 12 cells; v2 arms 0.0398–0.0445; diagnostic `8a22ed51…` 0.3759. Popularity read 0.1974 and item-item 0.3619 on the same split. | **Correctness evidence only.** They found the τ = 1.0 loss floor and the FAISS row-to-id defect, which are properties of the code, not of the split. ADR 0006's τ = 0.05 proposal took its τ from this pilot and is still a proposal. Gate 1's stop was overtaken by O-10's full-data run, `2d7f1a49…` (0.5113), which is clean and is the two-tower's standing number. |

**No full-data verdict changes.** Every gate verdict, every promotion and every number of record was
fitted and scored on the full split, and the full split never crosses the boundary. What changes is
pilot-scale evidence and the window reserved for WO-8.

**Closing the path, continued (step 6).** The sweep adds one path to the "still open" list above:
`src.training.sasrec_ranker.prepare_shared`, which the ranker runners' smoke runs load through. It
subsamples and splits the same way. So four trainers now compute their own cutoff on a subsample:
`twotower`, `itemitem`, `last_item` and `sasrec_ranker`.

## Proposed amendment 2026-10-05 — retire the WO-8 window and set a new one (not approved)

**Status: proposed. The owner approves by merging the pull request that adds this section.** Until
then no window is approved for the one-time read. The 2026-09-05 window is contaminated, as recorded
above, and the window below is only a proposal. Nothing reads either one.

This is step 3 of the contamination procedure. The matching ADR note is
[ADR 0001's amendment of 2026-10-05](../../adr/0001-evaluation-protocol.md#amendment-2026-10-05-proposed--the-final-window-moves-past-every-subsampled-read).

**Retired:** `[1469256597, 1471675797)`, 2016-07-23 06:49:57 to 2016-08-20 06:49:57 UTC, 126,304
ratings. Pilot runs fitted on 5,394 of its ratings and scored 2,950: 8,327 distinct ratings, 6.6% of
the window. It can no longer be read as data no decision has used.

**Proposed:** `[1475668076, 1478087276)`.
- Start: 1475668076, 2016-10-05 11:47:56 UTC.
- End: 1478087276, 2016-11-02 11:47:56 UTC.
- 28 days, 106,904 ratings. This is a row count, which is not a read.

**Why the start is there.** It is one second after the latest timestamp any recorded subsampled run
read: 1475668075, a holdout rating scored by the 1% ranker smoke runs. No rating any recorded run
fitted or scored lies at or after 1475668076. The start is later than the 6% split's `holdout_end`
(1473707504) only because the sweep found the 1% runs.

**How the read trains.** Each frozen configuration is retrained on all ratings before 1475668076,
then scored once on the window. That is the rule WO-8 already uses, applied to the new start: the
window sits immediately after its own training data, as the holdout does. This replaces "all ratings
before 1469256597" in WO-8's procedure and in this memo's 2026-10-05 trigger proposal.

**The 74 days in between are spent.** `[1469256597, 1475668076)` holds 318,523 ratings over 74.2
days. Its one remaining use is as training data for the one-time read. It is not a development
window, and no decision is evaluated on it.

**What stays sealed.** Everything from 1478087276 on: 4,444,910 ratings to 2019-11-21. That leaves
39 whole 28-day windows after this one. The development boundary does not move: "Sealed,
operationally" above still holds every run to `timestamp < 1469256597`.

**What the move costs:**
- The read's models train on 102 more days than the holdout models: up to 1475668076 rather than
  `T`, 1466837397. Its number keeps the holdout's shape (28 days straight after training), but it
  describes a different month, on models trained on more data. It is not a like-for-like twin of any
  holdout number.
- The window holds 106,904 ratings against the old window's 126,304, so its interval is a little
  wider.

**What keeps the window clean.** The four trainers listed under step 6 still split a subsample at the
subsample's own cutoff. The 1% draw at seed 42 already reads up to the second before this window, and
another fraction or seed can land later. **Proposed rule:** none of the four runs on a subsample
until it cuts at the full split's boundaries and carries a guard like `SealedPartitionError`.

**How we would know this is wrong.** A run found later whose latest fitted or scored timestamp is at
or after 1475668076 burns this window too. The answer is the same procedure again, with a later
window, recorded here. The cheap check is to repeat this record's sweep over the tracking store
before WO-8 starts.
