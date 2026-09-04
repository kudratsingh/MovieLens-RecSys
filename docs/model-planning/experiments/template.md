# Experiment: <family / question / version>

**Status:** proposed

**Governing ADR:** <link>

**Owner approval:** <link/date or pending>

**Specification version:** <immutable version>

## Decision this experiment informs

State one decision. Example: “Advance one SASRec loss configuration from the 6% pilot to a
full-data run, or stop the family.”

## Hypothesis and falsifier

**Hypothesis:** <specific mechanism and expected effect>

**Falsified when:** <observable stop condition>

## Baselines and controls

| Model/control | Why it is required | Run/spec reference |
|---|---|---|
| Current champion | Promotion comparison | |
| Simple signal baseline | Complexity floor | |
| Negative/control ablation | Mechanism test | |

## Changed and fixed axes

**Changed:** <the one axis or declared factorial design>

**Fixed:** data, split, catalog, labels, routing, exclusions, K, seed policy, architecture fields,
training budget, retrieval mode, and anything else required for interpretation.

## Protocol identity

| Field | Value |
|---|---|
| DVC/raw revision | |
| Derived snapshot hash | |
| Train cutoff / holdout window | |
| Rolling window(s) | |
| Partition(s) read | see [Partition declaration](#partition-declaration) below |
| Label contract | |
| Catalog fingerprint | |
| Cold threshold / routing | 10 / |
| Exclusion policy | |
| Stage / K | |
| Feature or sequence contract | |
| Protocol hash | generated before run |

## Partition declaration

The test partition is sealed, and a claim of "sealed" is worth only what a reviewer can check
independently. Fill the first four rows in before the run; complete the two measured rows from the
finished run's own logs. The policy this enforces — what sealed means, who may unseal, and what
happens if it turns out to have been read — is
[`../memos/sealed-test-and-dataset-policy.md`](../memos/sealed-test-and-dataset-policy.md).

Under ADR 0001 on the current 25M snapshot the sealed boundary is `holdout_end = 1469256597`
(2016-07-23 06:49:57 UTC). Restate the value this run actually computed rather than copying that
number, so a moved split shows up as a mismatch instead of hiding behind a constant.

| Field | Value | How a reviewer checks it |
|---|---|---|
| Partition(s) read | `holdout` / `rolling-backtest:<window id>` / `sealed-test` | anything other than the first two needs the owner unseal approval linked in the next row |
| Owner unseal approval | not applicable | required, with the freeze it depends on already recorded, whenever the row above says `sealed-test` |
| Sealed boundary this run used | `holdout_end` = <epoch> (<UTC date>) | equals the run's logged `holdout_end_timestamp` param, and equals the value `src/data/split.py` derives from the declared DVC revision |
| Feature source and its as-of | `FeatureIndex` (point-in-time per row) / `feast:<materialization as-of>` | a materialized source whose as-of is at or after `holdout_end` summarises sealed rows and contaminates the run even though no `split.test` appears anywhere in it |
| Latest event timestamp that entered fitting | <epoch> (<UTC date>) | strictly less than `holdout_end`; this is the max `timestamp` over every frame the model was fit on, cohort rows and negatives included |
| Latest event timestamp that entered scoring | <epoch> (<UTC date>) | strictly less than `holdout_end`; max `timestamp` over the evaluation targets and over anything used to define slices or thresholds |

**Affirmation.** `<name>, <date>: this run read no interaction at or after the sealed boundary above,
except as declared in this table.`

A run whose declaration is absent, self-contradictory, or contradicted by its logged parameters is
**invalid** rather than weak — the same treatment a failed leakage control gets. If the affirmation
cannot honestly be made, stop and follow the contamination procedure in the policy note before
recording anything else.

## Cohorts and slices

- natural warm/cold/overall;
- synthetic h0/h1/h3/h10 where applicable;
- history-length buckets;
- target popularity deciles;
- item age/cold-item slice if defined;
- tenant/source slices if applicable.

## Metrics

**Primary:** <metric, slice, K, materiality threshold>

**Guardrails:** <slice and end-to-end non-regression>

**Diagnostics:** <coverage, reachability, novelty, calibration, resource metrics>

Include exact formulas or links to tested shared implementations.

## Grid and seeds

| Cell | Changed fields | Seeds | Advance/stop rule |
|---|---|---|---|
| | | 42, 7, 13 or approved bounded policy | |

No cell may be added after results are visible without a new spec and hypothesis.

## Compute and storage budget

- hardware and environment:
- maximum wall-clock per cell/seed:
- maximum total spend or machine-hours:
- peak-RAM ceiling:
- disk/artifact ceiling:
- automatic termination conditions:

## Pre-run correctness checklist

- [ ] Governing ADR is approved for this work.
- [ ] Protocol fingerprint is generated and matches baselines.
- [ ] Temporal/equal-time leakage tests pass.
- [ ] Tiny-overfit or model-specific correctness gate passes.
- [ ] Baseline and control implementations are fixed.
- [ ] Stop rule and compute cap are approved.
- [ ] The partition declaration's pre-run rows are filled in, and the planned run reads no
      interaction at or after the sealed boundary.
- [ ] The feature source is point-in-time per row, or its materialization as-of is before the
      sealed boundary.
- [ ] Output paths and MLflow experiment are immutable/known.
- [ ] Another running worktree or training process will not be disturbed.

## Commands

```bash
# Exact commands, environment variables, thread pins, and spec path.
```

## Expected artifacts

- MLflow params, metrics, tags, and run IDs;
- protocol/data/code/environment manifests;
- checkpoint/final artifact hashes, if produced;
- raw per-user or per-slice evaluation artifact needed for uncertainty;
- profiler/resource report;
- scorecard draft.

## Verdict

Complete only after the run.

**Validity:** valid / invalid / superseded

**Partition affirmation:** intact / breached — <the two measured timestamps, copied back into the
partition declaration>

**Decision:** advance / stop / repeat narrowly / inconclusive

**Rule application:** <numbers against predeclared rule>

**Runs:** <IDs>

**What is not authorized next:** <explicit boundary>
