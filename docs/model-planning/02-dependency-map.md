# Dependency map

## Critical path

| Dependency | Enables | Why it is blocking |
|---|---|---|
| One reconciled Git lineage | Trustworthy status and subsequent PRs | SASRec and ranker parity fixes currently diverge |
| Protocol identity | Valid comparisons | K alone cannot prove two runs answer the same question |
| Retrieval gate | SASRec promotion verdict | Existing gate is ranker-specific NDCG |
| SASRec scale design | Full-data evidence | Dense prefixes and Python sampling risk excessive RAM/time |
| Exact artifact export | Serving eligibility | Current demo artifact is not the measured full-data model |
| Generic retriever manifest/loader | Learned retrieval online | Sidecar is hard-coded for `CandidateIndex` |
| Scalable feature representation | Full-data ranking/serving | User-by-catalog materialization is not viable |
| Stable training/evaluation interface | Prefect automation | Automating a moving experiment contract creates rework |
| Exposure/outcome logging | Online experiments and OPE | Ratings alone do not identify impressions or non-actions |
| Propensity logging | Bandits/OPE | Counterfactual estimators are invalid without action probability |

## Sequencing policy

The already-started SASRec pilot may run in parallel with M0 documentation and gate design.
However:

- no full-data SASRec promotion claim precedes protocol/gate approval;
- no learned retriever serving work precedes an offline win;
- no Prefect promotion flow is considered complete before exact artifact export exists;
- no multi-objective implementation precedes an observable-label and utility decision;
- no bandit or off-policy claim precedes exposure, outcome, and propensity logging.

## Work-package dependencies

### M0 — Reconcile and harden the foundation

Inputs: current SASRec stack, ranker exclusion/parity branch, ADRs 0001/0004/0009/0016.

Produces:

- coherent branch and status lineage;
- protocol manifest and incompatibility checks;
- retrieval gate decision and implementation;
- training/serving exclusion and feature-source parity;
- compute and test-seal decisions.

### M1 — Complete SASRec research

Requires the M0 comparison contract before promotion evidence. Its bounded pilot can finish
while M0 is being completed. Produces a negative closeout or a frozen full-data candidate.

### M2 — Productionize a winning retriever

Requires an M1 offline win. Produces manifest v2, exact export, generic loader, model-server
integration, audits, and latency evidence. If M1 stops, M2 is skipped for SASRec.

### M3 — Model factory

Requires stable model interfaces from M1/M2 and registry ownership decision D-008. Produces
idempotent Prefect workflows, immutable registration, gated promotion, and rollback metadata.

### M4 — Sequence-aware ranker

Requires a useful sequence representation, but not necessarily a promoted SASRec retriever.
First exposes frozen sequence signals to LightGBM. Neural DIN/TransAct follows only when that
lower-complexity step leaves a measured gap.

### M5 — Multi-objective ranking

Requires D-010 (objectives and utility), ADR 0002 amendment, and reliable label availability.
It can precede M6 only if the product utility is clearer than the candidate-source strategy.

### M6 — Multi-retriever mixing and re-ranking

Requires two useful candidate sources and D-011 (diversity/calibration objective). Produces
source-aware union metrics, ranker features, and slate-level guardrails.

### M7 — Experiments, OPE, and exploration

Requires routing/assignment, impression/outcome joins, minimum traffic, and propensity logging.
This work has model scope, but depends on Phase 6 platform capabilities outside this folder.

### M8 — Frontier capstone

Requires D-013 (compute) and a reusable sequence representation. It starts as a bounded research
spike, never as an assumed production replacement.

## Parallel work that is safe

- Protocol-manifest tests can proceed beside SASRec profiling.
- Simple sequential baselines and diagnostics can proceed beside dataset-loader redesign.
- Manifest v2 design can begin after M1's artifact needs are known, but implementation waits
  for an offline winner.
- Rolling temporal backtest support can proceed beside the fixed holdout pilot.
- Model observability schema design can proceed during M3; online claims wait for data volume.

## External handoffs

| Handoff | Owning area | Model requirement |
|---|---|---|
| Full-data deployment target | Platform/infra | Decide whether production serves full 25M artifacts or compact demo fixtures |
| Champion/challenger request routing | Phase 6 platform | Stable assignment, shadow execution, atomic rollback |
| Impression and outcome capture | API/product data | Model/version/rank/assignment join keys and event semantics |
| Participant/product utility input | Product | Defines multi-objective weights and acceptable diversity trade-offs |
| Compute provisioning | Owner/platform | Memory, accelerator, wall-clock and spend caps |

These handoffs are dependencies, not authorization for this model plan to implement frontend or
general infrastructure work.
