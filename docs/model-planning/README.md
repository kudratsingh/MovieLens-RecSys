# Model development program

**Snapshot:** 2026-09-04 at `1d189a8` on the isolated planning branch.

This folder turns the high-level ladder in [`../modeling-roadmap.md`](../modeling-roadmap.md)
into an executable program for model research, evaluation, training data, artifacts,
promotion, model serving, and model-specific observability. It deliberately excludes
frontend product work, general hosting, and non-model infrastructure.

## Authority and update rules

These documents do not replace the project's existing sources of truth:

1. [`../../CLAUDE.md`](../../CLAUDE.md) defines the project constraints and non-negotiables.
2. [`../adr/`](../adr/) records approved decisions. A plan item cannot override an ADR.
3. [`../modeling-roadmap.md`](../modeling-roadmap.md) governs rung approval and ordering.
4. [`../results.md`](../results.md) is the append-only measurement record.
5. This folder defines execution order, work packages, acceptance criteria, and agendas.
6. [`../status/`](../status/) records what has actually landed.

Every phase below is a plan until its governing ADR is approved. Completing a research
phase does not promote a model. Promotion requires the relevant offline gate, artifact
equivalence, and—when the model is intended to serve—the unchanged latency and reliability
gates.

## Scope

Included:

- labels, temporal datasets, sampling, feature and sequence construction;
- candidate retrieval, ranking, re-ranking, multi-objective learning, and online learning;
- experiment design, baselines, diagnostics, uncertainty, and promotion gates;
- reproducibility, MLflow lineage, model registry behavior, and Prefect model workflows;
- immutable model artifacts, offline-to-online equivalence, and model-side serving work;
- model quality, feature drift, embedding health, and exposure/outcome logging.

Excluded for now:

- frontend surfaces and usability work;
- host provisioning, DNS, generic container/platform work, and non-model SRE tasks;
- general API features that are not required by a model contract;
- changing an approved label, metric, or architecture without an ADR.

External dependencies are still recorded when model work cannot be honest without them.
For example, bandits require impression and propensity logging plus experiment routing,
even though building the product UI is outside this plan.

## Program map

| Work package | Outcome | Entry condition | Exit condition |
|---|---|---|---|
| M0 — Reconcile and harden the foundation | One coherent model lineage and executable retrieval evaluation contract | Current branch state | Parity debt integrated, docs agree, protocol identity and retrieval gate approved |
| M1 — Complete SASRec research | Defensible SASRec verdict on MovieLens 25M | M0 comparison contract; current pilot may finish sooner | Pilot/full-data stop or advance decision recorded with required controls |
| M2 — Productionize a winning learned retriever | Exact evaluated artifact can be loaded and served | M1 clears offline floor | Round-trip equivalence, encoder p99, service p99, manifest and audit gates pass |
| M3 — Build the model factory | Repeatable snapshot-to-promotion lifecycle | Research interfaces stable | Idempotent Prefect flow registers immutable artifacts and cannot bypass gates |
| M4 — Sequence-aware ranking | Test whether sequence signals improve final ordering | Useful sequence representation exists | LightGBM-plus-sequence verdict, then neural ranker verdict only if justified |
| M5 — Multi-objective ranking | Ranking optimizes an owner-defined utility | Observable labels and utility approved | Per-task and combined gates pass without hiding trade-offs |
| M6 — Multi-retriever mixing and re-ranking | Higher reachable recall with controlled slate quality | At least two useful sources | Union lift and diversity/calibration guardrails pass end to end |
| M7 — Experiments, OPE, and exploration | Trustworthy online comparison and bounded exploration | Exposure/outcome/propensity data exists | SRM, estimator validation, safety and rollback gates pass |
| M8 — Frontier capstone | Evidence-backed go/no-go on generative/foundation work | Earlier representations are reusable; compute approved | Bounded spike produces a clear value/cost decision |

The critical path is:

`M0 -> M1 -> (offline stop OR M2) -> M3 -> M4 -> M5/M6 -> M7 -> M8`

M5 and M6 may swap after an owner decision. M7 cannot begin with historical MovieLens
ratings alone. M8 may remain a reading list without weakening the project.

## Documents

- [`00-current-state.md`](00-current-state.md) — evidence-backed starting point and gaps.
- [`01-program-guardrails.md`](01-program-guardrails.md) — rules every work package inherits.
- [`02-dependency-map.md`](02-dependency-map.md) — critical path and external dependencies.
- [`03-decision-register.md`](03-decision-register.md) — questions requiring owner decisions.
- [`work-items.md`](work-items.md) — PR-sized backlog with acceptance evidence.
- [`risks-and-assumptions.md`](risks-and-assumptions.md) — active risk register.
- [`phases/`](phases/) — detailed scope, agendas, gates, and deliverables by work package.
- [`experiments/`](experiments/) — the experiment contract and reusable run template.
- [`agendas/`](agendas/) — kickoff, pre-run, results, and promotion review agendas.
- [`scorecards/`](scorecards/) — concise per-model decision records linked to raw results.

## How to use the plan

At the start of a work package:

1. Resolve every decision marked `required before start` for that package.
2. Confirm the ADR status permits the work.
3. Copy the experiment template for each new hypothesis.
4. Select only the work items needed for one reviewable PR.
5. Define the stop rule and compute cap before a run starts.

At the end:

1. Run the required verification commands.
2. Add run IDs, hashes, machine, wall-clock, and caveats to `docs/results.md`.
3. Add or update the model scorecard.
4. Record the gate verdict on the governing ADR and roadmap decision log.
5. Update `docs/status/` only for work that actually landed.

## Immediate next checkpoint

The current 6% SASRec BCE/gBCE pilot may finish and be recorded. Before a full-data run
is treated as promotion evidence, M0 must settle the retrieval gate, protocol identity,
branch reconciliation, and the SASRec memory/compute plan. Item-item remains retrieval
champion and LightGBM remains ranking champion until every relevant gate passes.

The concrete first-session sequence is in
[`agendas/immediate-execution.md`](agendas/immediate-execution.md).

Owner direction recorded on 2026-09-04: production targets the exact MovieLens 25M champion;
local or cloud-GPU training is allowed under a pre-run cost estimate; the test partition is
provisionally sealed after a repository audit found no test-metric use; and pilot work may continue
while M0 closes. Retrieval promotion requires a three-seed 3% warm recall@500 improvement, cold and
overall guardrails, and final LightGBM NDCG non-regression. Increasing model capacity or traffic
does not relax the established 15 ms encoder and 100 ms end-to-end p99 gates.
