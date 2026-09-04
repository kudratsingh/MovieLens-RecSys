# Experiment operating model

Every model result starts as a committed specification copied from
[`template.md`](template.md). A spec is immutable after its first run of record; a correction or
new cell receives a new spec/version and explains why.

## Experiment lifecycle

1. **Proposed:** hypothesis, baseline, changed axis, protocol, stop rule, and compute cap drafted.
2. **Approved to run:** governing ADR permits the work and required decisions are closed.
3. **Running:** run IDs allocated; changes require invalidating and restarting under a new spec.
4. **Measured:** artifacts and metrics complete; comparability checks pass.
5. **Decided:** advance, stop, repeat narrowly, or inconclusive for one named reason.
6. **Recorded:** `results.md`, ADR note, roadmap row, and scorecard agree.

## Required controls by experiment type

### Learned retrieval

- popularity and current retrieval champion;
- a simple model that isolates the new signal;
- warm/cold/overall and policy attribution;
- recall@500 primary, NDCG@500 diagnostic;
- coverage, reachability, popularity/item slices;
- seed policy and exact/ANN retrieval check;
- end-to-end ranker guardrail before promotion.

### Sequential model

- strict-prefix and equal-time canaries;
- last-item transition or approved recency baseline;
- shuffled-order control;
- sequence length/truncation and unknown-item evidence;
- sampler rejection/collision/distribution evidence.

### Ranker

- immediate incumbent trained/evaluated on comparable candidates;
- serving-equivalent exclusions and point-in-time features;
- overall NDCG@10 +3% and warm/cold tolerances;
- calibration/score-distribution and candidate-source slices;
- three-seed aggregate for a positive claim.

### Re-ranking or multi-objective

- relevance/utility baseline and every individual objective;
- Pareto or weight frontier, not a post-hoc selected point;
- predeclared relevance and safety guardrails;
- label missingness/censoring and calibration.

## Naming

Use a descriptive family and immutable version, for example:

`sasrec/pilot-v3`, `sasrec/full-v1`, `ranker/sequence-features-v1`.

MLflow run names may be friendly, but the spec path, protocol hash, code SHA, and run ID are the
identity. Never rely on `latest`.

## Run validity

A run is invalid—not merely weak—when it has leakage, a mismatched protocol, incomplete metrics,
corrupt artifacts, unknown code/data identity, or a failed correctness control. Keep the run for
audit, tag it invalid/superseded, and exclude it from aggregates.

An interrupted run with params but no final metrics cannot enter a gate. A negative run that
passes validity checks is valuable evidence.

## Result record

The durable result contains:

- the question and verdict in the first paragraph;
- baseline and candidate table;
- run IDs, spec, protocol/data/code hashes;
- seed aggregation and uncertainty;
- resource measurements;
- diagnostics and failed/omitted work;
- exact stop/promotion rule application;
- next action and work explicitly not authorized by the result.
