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
