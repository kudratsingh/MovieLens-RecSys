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
| Test sealed? | yes |
| Label contract | |
| Catalog fingerprint | |
| Cold threshold / routing | 10 / |
| Exclusion policy | |
| Stage / K | |
| Feature or sequence contract | |
| Protocol hash | generated before run |

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
- [ ] Test partition remains sealed.
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

**Decision:** advance / stop / repeat narrowly / inconclusive

**Rule application:** <numbers against predeclared rule>

**Runs:** <IDs>

**What is not authorized next:** <explicit boundary>
