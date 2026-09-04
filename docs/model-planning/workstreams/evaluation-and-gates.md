# Evaluation and promotion-gate workstream

## Purpose

Make every model comparison answer one reproducible question. The evaluation layer must prevent
invalid comparison, quantify uncertainty, separate stage-local quality from system quality, and
keep the final test partition from becoming another tuning set.

## Protocol manifest

Every evaluation result should embed or reference one canonical manifest.

### Semantic identity fields

A mismatch in any of these fields makes two results not comparable:

| Area | Required fields |
|---|---|
| Data | raw DVC revision, derived snapshot hash, rating/event schema version |
| Time | train cutoff, holdout start/end, backtest-window ID, timezone/timestamp unit |
| Labels | label-contract version, positive/negative/unknown/censored rules |
| Population | eligible users, catalog fingerprint, unknown-item policy |
| Routing | cold threshold, learned/fallback policy, history qualification |
| Filtering | positive history, seen, dismissal, target and candidate exclusion policy |
| Stage | retrieval/ranking/re-ranking, K, candidate-source identity |
| Features | feature/sequence contract and point-in-time lookup semantics |
| Metrics | metric implementation version, relevance definition, slice definitions |

### Run identity fields

These identify reproduction but need not make scientifically identical runs incomparable:

- code SHA and dirty-worktree flag;
- dependency/environment/image fingerprint;
- model configuration and seed;
- hardware and thread/accelerator configuration;
- MLflow run and artifact identities;
- start/end time, wall-clock, peak RSS/GPU memory.

Canonical JSON serialization should sort keys, normalize timestamps/paths, and hash the semantic
section separately from run identity. Tests should prove dictionary order and irrelevant display
metadata cannot move the semantic hash.

## Metric definitions

Metric functions remain in `src/evaluation/`; these definitions are the proposed contract.

### Recall@K

For each eligible user:

`recall_u@K = |relevant_u ∩ topK_u| / |relevant_u|`

Report the unweighted mean across users. Also retain per-user values for bootstrap intervals. A
user with no relevant item in the evaluation window is ineligible, not assigned zero.

- Retrieval primary: recall@500.
- End-to-end supporting metric: recall@10.
- Never compare results at different K.

### NDCG@K

Use binary relevance under ADR 0002 unless a later label ADR changes it. Compute DCG from the
ordered top K and divide by the ideal DCG for that user's number of relevant items. Report the
unweighted mean across eligible users.

- Ranking primary: NDCG@10.
- Retrieval NDCG@500 is diagnostic only.

### Catalog coverage

`unique eligible items retrieved across users / eligible catalog size`

Report coverage at the same K and population as recall. Also report count, since a ratio can hide
a catalog change.

### Target reachability

`number of relevant targets appearing anywhere in the candidate set / number of relevant targets`

This differs from user-averaged recall when users have different target counts. Report both and
name the weighting.

### Popularity and novelty diagnostics

- Assign each eligible item a training-only popularity rank/decile.
- Report retrieved exposure by decile and mean/median percentile.
- If novelty is used, define it from training-only interaction probability, for example
  `-log2(p_train(item))`, with smoothing fixed in the protocol.
- These diagnose head collapse; they do not independently promote a model.

### Diversity diagnostics

Do not add a generic “diversity” score without D-011. Candidate definitions include intra-list
embedding similarity, genre coverage, and catalog exposure. Choose one primary definition and a
relevance guardrail before M6.

## Required slices

- warm, cold, and overall under threshold 10;
- learned versus fallback serving policy;
- synthetic h0/h1/h3/h10;
- natural history-length buckets such as 10–19, 20–49, 50–99, and 100+;
- target popularity deciles;
- item age and cold-item slice after its data contract is accepted;
- source contribution for mixed retrieval;
- temporal backtest window;
- tenant only when sample size supports a meaningful estimate.

Every slice reports user/target counts. Empty or underpowered slices are marked unavailable, not
zero or passing.

## Uncertainty

### Seeds

- Positive stochastic claims use seeds 42, 7, and 13.
- Aggregate only complete, protocol-compatible runs.
- Report mean, min/max, and relative range.
- A missing or failed seed blocks a positive gate unless the ADR predeclares a different policy.

### User bootstrap

Retain per-user metrics and bootstrap users with a fixed seed to produce a confidence interval for
candidate-minus-incumbent delta. Pair users when both models evaluated the same population. Record
replicate count and percentile/BCa method. Bootstrap evidence supports the gate; it does not change
the owner-approved +3% threshold after results are seen.

### Rolling temporal windows

Add at least three development windows before the sealed test period. Each window trains only on
prior events and evaluates a fixed future interval. Report:

- primary metric per window;
- mean and worst window;
- catalog/user counts and distribution drift;
- whether the direction of improvement is consistent.

A model whose fixed-holdout gain reverses across windows is not ready for promotion without an
explicit explanation and decision.

## Retrieval gate approved on 2026-09-04

Implemented by `src/evaluation/manifest.py`, `src/evaluation/retrieval_gate.py`, and
`make gate-retrieval`. The operator contract is
[`../contracts/evaluation-protocol.md`](../contracts/evaluation-protocol.md).

The executable gate should perform these checks in order:

1. Candidate and incumbent are retrieval-stage results at K=500.
2. Semantic protocol hashes match.
3. Required seeds are complete and slice populations agree.
4. Candidate mean warm recall@500 is at least `incumbent × 1.03`.
5. Candidate cold and overall recall do not regress beyond retrieval-specific measured tolerances.
6. Supporting diagnostics contain no invalid/leakage/popularity-collapse finding that triggers a
   predeclared hard guardrail.
7. For serving promotion, the paired LightGBM system preserves NDCG@10 within ADR 0001 tolerances.

Using the recorded item-item warm score of 0.400144 gives an illustrative floor of 0.412148. The
implementation calculates against the compatible incumbent and does not hard-code this example.

Retrieval-specific cold/overall tolerances must be measured from repeated stochastic runs or a
documented deterministic-incumbent comparison before the gate is complete. Do not reuse ranking
NDCG tolerances merely because numbers already exist.

## Ranking gate

Preserve ADR 0001:

- overall mean NDCG@10 improves by at least 3% relative;
- warm regression is no worse than 6%;
- cold regression is no worse than 5%;
- comparison is seed-aggregated when either side is stochastic;
- K/protocol mismatch returns not comparable.

Future multi-objective or re-ranking gates require their own ADRs; they cannot silently reuse one
scalar ranking threshold.

## Test-set seal

The repository audit found no code/history evidence of evaluating `split.test`; trainers only log
its row count. Treat it as sealed unless the owner recalls external use.

Before unsealing:

1. Freeze data snapshot, model family/config, seeds, protocol, artifacts, and all thresholds.
2. Reach serving eligibility on holdout/backtests.
3. Record an owner-approved release-candidate identifier and unseal commit.
4. Run once and publish the result regardless of outcome.
5. Do not tune against it. A failed release returns to development with a new future test window.

## Machine-readable gate output

Every gate should emit:

- decision: promote/refuse/not-comparable/incomplete;
- candidate/incumbent run and aggregate IDs;
- semantic protocol hashes;
- primary values, threshold, delta, and per-slice guardrail values;
- required/missing seed list;
- supporting diagnostics and hard-guardrail status;
- code version of the gate;
- human-readable explanation generated from the same structured result.

## Test matrix

- exact threshold boundary and floating-point tolerance;
- candidate better/equal/worse;
- warm pass with cold/overall guardrail failure;
- recall pass with end-to-end NDCG failure;
- K, cutoff, catalog, routing, exclusion, and feature-contract mismatch;
- one missing, failed, invalid, or duplicate seed;
- slice population mismatch;
- deterministic incumbent repeated against stochastic candidate;
- empty/underpowered slice;
- stable structured output and CLI exit codes.

## Exit criteria

- Protocol schema, hash, serialization, and mismatch tests exist.
- Shared metrics and diagnostics have hand-built fixture tests.
- Retrieval and ranking gates are visibly different APIs or require an explicit stage.
- Rolling backtests and paired user bootstrap are available.
- Sealed-test policy is enforced by configuration, not only prose.
- MLflow results carry enough identity for the gate to refuse invalid comparisons.
