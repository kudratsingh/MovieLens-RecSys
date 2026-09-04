# Model-program work items

These are intended to be reviewable PR units. IDs are stable; sequencing may change through the
decision register. `S/M/L` are relative review-and-implementation sizes, not calendar estimates.

## M0 — Reconcile and harden the foundation

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M0-01 | S | Reconcile SASRec, two-tower v2, and ranker parity lineages | — | One intended lineage; diff review; all status statements match |
| M0-02 | S | Normalize stale model status/index docs | M0-01 | CLAUDE, ADR index, status, roadmap, README references agree |
| M0-03 | M | Define a versioned protocol manifest | D-001 | Unit tests cover full identity and reject mismatches |
| M0-04 | M | Add retrieval recall gate and CLI | M0-03, D-001 | Deterministic tests for promote/refuse/not-comparable and seed aggregation |
| M0-05 | S | Apply serving-equivalent candidate exclusions in ranker training | M0-01 | Point-in-time fixture proves seen/dismissed semantics match serving |
| M0-06 | M | Pin Python/Feast training and serving parity boundary | M0-01 | Historical and online values agree on timestamped fixture |
| M0-07 | M | Add rolling-origin evaluation splits | M0-03, D-005 | At least three windows, no overlap/leakage, aggregate uncertainty |
| M0-08 | S | Document sealed-test and 25M/32M policies | D-005, D-007 | Dated decision note and run-template enforcement |

## M1 — Complete SASRec research

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M1-01 | S | Record the current 0.5%/6% pilot runs and invalidate superseded runs | — | Run IDs, commits, metrics, machine, wall-clock, reason for invalidation |
| M1-02 | M | Add deterministic tiny-overfit gate | — | Target recovery threshold passes; causal/leakage controls remain green |
| M1-03 | M | Build last-item transition/recency baseline | M0-03 | Same sample, exclusions, routing, catalog, K and evaluator as SASRec |
| M1-04 | M | Add shuffled-sequence control | M1-02 | Deterministic shuffle preserves items/lengths and measures order value |
| M1-05 | M | Complete SASRec run telemetry | M0-03 | Counts, sampler stats, peak RSS, timing, protocol hash in MLflow |
| M1-06 | M | Add retrieval diagnostics and synthetic cohort | M1-05 | h0/h1/h3/h10, coverage, reachability, head bias and item slices tested |
| M1-07 | L | Replace dense prefix materialization | D-004 | Peak RSS bounded with scale; equality fixtures against current builder |
| M1-08 | M | Replace/profile Python negative sampler | M1-07 | Determinism/exclusion tests; rejection and throughput evidence |
| M1-09 | S | Freeze pilot winner and full-run spec | M1-01..08, D-003 | Checked-in spec changes no unapproved axes |
| M1-10 | L | Run full-data seed set | M1-09, M0-04, D-004 | Seeds 42/7/13 or predeclared negative stop; complete provenance |
| M1-11 | M | Run baselines, rolling windows, and retrieval gate | M1-10, M0-07 | Comparable item-item/popularity/two-tower lines and gate verdict |
| M1-12 | S | Record SASRec closeout/advance decision | M1-11 | Results section, ADR note, roadmap row, scorecard |

## M2 — Productionize a winning learned retriever

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M2-01 | M | Design typed manifest v2 | M1 offline win, D-006 | Schema covers retriever family, weights, vocab, index, protocol and hashes |
| M2-02 | L | Implement SASRec save/load/export | M2-01 | Embedding/candidate equality before and after round trip |
| M2-03 | M | Introduce generic retriever interface | M2-01 | Item-item and SASRec satisfy same request/result contract |
| M2-04 | L | Load SASRec in private model sidecar | M2-02/03 | Fail-closed startup compatibility and fallback tests |
| M2-05 | M | Preserve online sequence/exclusion semantics | M2-04 | Ordered positives, dismissals, unknowns, threshold and full-history exclusions |
| M2-06 | M | Extend prediction audits | M2-04 | Retriever artifact/version, input hash, unknown count, encoder latency |
| M2-07 | M | Run isolated encoder benchmark | M2-04 | p50/p95/p99; p99 <15 ms on named topology |
| M2-08 | L | Run learned-serving and unchanged k6 gates | M2-05..07 | Exact artifact served; authenticated p99 <100 ms; zero correctness errors |
| M2-09 | M | Prove paired retriever/ranker guardrail | D-002, M2-02 | End-to-end NDCG verdict on candidates produced by exported retriever |
| M2-10 | S | Publish serving-eligibility scorecard | M2-08/09 | All gate links and rollback artifact recorded |

## M3 — Model factory and model observability foundation

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M3-01 | M | Decide registry ownership and lifecycle states | D-008 | ADR defines MLflow artifact truth and Postgres tenant assignment |
| M3-02 | M | Define immutable training snapshot task | M0-03/06 | Idempotency key and data/feature fingerprints |
| M3-03 | L | Build retriever train/evaluate Prefect flow | M1 interface stable | Retry-safe multi-seed runs produce one aggregate decision |
| M3-04 | L | Build ranker train/evaluate Prefect flow | M0-05/06 | Candidate/exclusion/feature contracts bound in lineage |
| M3-05 | M | Register and publish immutable bundle | M2-01/02, M3-01 | Registered version points to exact evaluated checksums |
| M3-06 | L | Implement gated tenant promotion task | M3-03..05 | Failed/undecided gate cannot mutate champion; approval is explicit |
| M3-07 | M | Add locks, resume, retry, and rollback tests | M3-06 | Duplicate flow is idempotent; concurrent promotion is serialized |
| M3-08 | M | Define model-quality telemetry baseline | M3-05 | Coverage, fallback, unknowns, embeddings, scores and feature baselines stored |

## M4 — Sequence-aware ranking

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M4-01 | S | Propose and approve Rung 3 ADR | M1-12 | Alternatives, gate, latency, stop rule, roadmap approval |
| M4-02 | M | Freeze sequence representation contract | M4-01 | Timestamped vector/attention schema and artifact identity |
| M4-03 | M | Add sequence features to LightGBM | M4-02 | Point-in-time feature parity and ablation against current eight features |
| M4-04 | M | Evaluate LightGBM-plus-sequence | M4-03 | Three-seed NDCG@10 gate and slice diagnostics |
| M4-05 | L | Build DIN target-attention ranker if needed | M4-04 stop rule | Candidate-aware batching, calibration, deterministic tests |
| M4-06 | L | Extend to TransAct only if DIN leaves a gap | M4-05 result | Approved ablation and measured incremental value |
| M4-07 | M | Artifact/latency/promotion checks | Winning model | Exact export, batch latency, end-to-end gate and scorecard |

## M5–M8 — Later roadmap rungs

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| M5-01 | M | Decide objectives, labels, utility, and bias policy | D-010 | ADR 0002 amendment and label audit |
| M5-02 | L | Establish independent-task baselines then MMoE | M5-01 | Per-task calibration/discrimination plus Pareto/utility report |
| M5-03 | M | Gate combined ranking and slices | M5-02 | No objective hidden by a scalar aggregate |
| M6-01 | M | Approve source-mixing/re-ranking ADR | D-011/12 | Source budget, dedupe, diversity metric, guardrails |
| M6-02 | L | Implement attributed candidate union | M6-01 | Source marginal recall, overlap, oracle ceiling, deterministic dedupe |
| M6-03 | M | Train source-aware ranker and MMR baseline | M6-02 | Relevance/diversity frontier and latency evidence |
| M7-01 | L | Add assignment, exposure, outcome, propensity contracts | External Phase 6 work | Join/replay/SRM tests and privacy/retention decision |
| M7-02 | L | Implement IPS/SNIPS/DR evaluation | M7-01 | Synthetic-policy recovery and confidence interval calibration |
| M7-03 | L | Run bounded contextual-bandit experiment | M7-02 | Safety constraints, regret/utility, instant rollback |
| M8-01 | M | Approve bounded frontier spike | D-013 | Dataset/compute/value hypothesis and stop rule |
| M8-02 | L | Compare one generative/foundation approach | M8-01 | Same protocol, downstream representation value, honest cost report |

## Verification baseline

Each model PR selects the relevant subset; a result PR records why anything was omitted.

```bash
ruff check .
black --check .
mypy src/
pytest tests/unit/ -v
pytest tests/feature_parity/ tests/learned_serving/ -v
git diff --check
```

Current research commands include:

```bash
OMP_NUM_THREADS=1 python -m src.training.sasrec_sweep \
  docs/experiments/sasrec/pilot-6pct.json
make train-popularity
make train-itemitem
make train-sasrec
make train-ranker
```

`make gate` remains permanently ranking/NDCG-only. Retrieval comparisons use
`make gate-retrieval`; missing canonical protocol metadata, a partial seed set, or absent measured
retrieval tolerances produces no promotion verdict.
