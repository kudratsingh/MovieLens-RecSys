# M3 — Model factory and observability foundation

## Objective

Automate a proven model lifecycle without hiding its decision points: immutable snapshot,
training, multi-seed evaluation, gate, registration, bundle publication, explicit approval,
tenant assignment, verification, and rollback.

Prefect orchestrates; it does not become a second source of model truth.

## Entry conditions

- M0 protocol, parity, and gates are stable.
- At least one retriever and the LightGBM ranker have reproducible train/evaluate interfaces.
- Manifest v2 or an equivalent exact-artifact boundary exists.
- D-008 establishes registry and tenant-assignment ownership.

## Flow design

### Flow A — Snapshot

1. Resolve raw DVC revision and source database snapshot.
2. Create immutable train/holdout/backtest references.
3. Produce data-quality counts and schema validation.
4. Bind label, catalog, routing, exclusion, and feature/sequence contracts.
5. Calculate one idempotency key from semantic inputs.

Output: immutable snapshot manifest, never a mutable “latest” path.

### Flow B — Train and evaluate retriever

1. Validate ADR approval and experiment specification.
2. Train each required seed in isolated tasks.
3. Persist exact artifacts and per-seed results.
4. Run holdout, rolling-window, slice, bias, and resource diagnostics.
5. Aggregate only protocol-compatible successful seeds.
6. Execute retrieval gate and record a machine-readable decision.

An interrupted seed may resume from a declared checkpoint; a failed seed cannot silently drop out
of a positive aggregate.

### Flow C — Train and evaluate ranker

1. Resolve the exact retriever artifact/source mix.
2. Construct serving-equivalent candidates and exclusions.
3. Fetch or reconstruct point-in-time features under the M0 contract.
4. Train required seeds, evaluate NDCG@10 and slices, and run ADR 0001 gate.
5. Bind retriever, ranker, and feature versions into one candidate bundle.

### Flow D — Register and publish

1. Register artifacts and scorecards only after complete evaluation.
2. Assign lifecycle state: research-complete, promotion-eligible, serving-eligible, rejected,
   superseded, or archived.
3. Build the immutable serving bundle without retraining.
4. Verify checksums, load equivalence, and target-platform compatibility.
5. Publish by content-derived/versioned identity; never overwrite an existing version.

### Flow E — Approve and promote

1. Require explicit owner approval referencing the gate result and scorecard.
2. Acquire a per-tenant/stage promotion lock.
3. Verify current champion has not changed since evaluation.
4. Atomically update the compatible retriever/ranker/feature bundle assignment.
5. Run readiness and canary checks.
6. Roll back assignment on failure without rebuilding.

## Idempotency and failure behavior

- The same semantic input key reuses successful immutable outputs.
- Retries may append task attempts but cannot create duplicate registered versions.
- A changed code/data/protocol/config hash creates a new run, even if a friendly name matches.
- `rejected` and `not comparable` are terminal decisions for that run.
- Promotion cannot occur when any required task is missing, failed, or inconclusive.
- Concurrent training is allowed; concurrent promotion to the same tenant/stage is serialized.
- The workflow records who approved, what changed, and the exact predecessor for rollback.

## Model observability baseline

M3 establishes signals needed before later online learning:

- request and prediction counts by tenant, model, policy, and fallback reason;
- candidate source counts, overlap, empty/unseeded retrieval, and unknown-item rate;
- retrieved catalog coverage, head/tail distribution, and exclusion pressure;
- sequence lengths, truncation/unknown counts, embedding norms and spread;
- rank-score and high-value feature distributions;
- feature snapshot age, missingness, and online/offline parity probe results;
- encoder/ANN/feature/ranker and end-to-end latency;
- delayed quality joins when a valid outcome exists.

Define minimum-volume rules: low-volume tenants inherit aggregate drift interpretation rather
than receiving noisy “degraded” labels.

## Deliverables

- Prefect snapshot, retriever, ranker, registration, and promotion flows.
- Idempotency, locking, retry, partial-failure, and rollback tests.
- MLflow/Postgres registry ownership implementation.
- Immutable lineage and scorecard artifacts.
- Model-health baseline schema and replay query.
- Operator-facing runbook limited to model lifecycle actions.

## Acceptance criteria

- Re-running the same flow produces no new semantic artifact or tenant assignment.
- A failed, incomplete, mismatched, or unapproved result cannot mutate a champion.
- The promoted manifest hashes exactly match evaluated MLflow artifacts.
- Retriever, ranker, and feature versions transition atomically or the flow rolls back.
- A stale approval fails if the champion changed after evaluation.
- One intentionally broken artifact and one intentionally failed canary prove rollback.
- Model-health records can be replayed to the source prediction audit.
- No Prefect task relies on a mutable local working directory or an untracked config.

## Suggested PR shape

1. Registry ownership ADR and lifecycle state model.
2. Snapshot/protocol task and idempotency foundation.
3. Retriever train/evaluate flow.
4. Ranker train/evaluate flow.
5. Register/publish exact bundle.
6. Approval/promotion/rollback flow.
7. Model-health baselines and replay checks.
