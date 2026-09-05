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
| M0-09 | S | Export per-user recall vectors from `evaluate()` | M0-03 | Vector mean reconstructs the published slice mean; round trip through the study loader |
| M0-10 | M | Run the retrieval tolerance study and publish the two fractions | M0-09 | Both tolerances recorded with the runs and the derivation that produced them |
| M0-11 | S | Measure the candidate-mix change the serving-equivalent exclusions cause | M0-05 | Two full ranker runs compared; the effect on ranking metrics stated rather than assumed |
| M0-12 | M | Emit a protocol manifest from every candidate trainer | M0-03, M0-09 | Emitted envelope round-trips through the gate reader; window identity matches the tiling |
| M0-13 | S | Decouple the pilot subsample seed from the training seed | — | Two runs at different training seeds score the identical user population |
| M0-14 | M | Run the surrogate seed-noise study and publish the measured spread | M0-13 | Three same-population runs at one configuration; spread recorded with its transfer assumption |

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

## What has landed

IDs are closed here rather than removed from the tables above, so a reader can still see what the
package was scoped to do. A row is only listed once the PR is on `main`.

| ID | Landed | PR | Note |
|---|---|---|---|
| M0-03 | 2026-09-04 | #125 | Versioned protocol manifest and semantic hash |
| M0-04 | 2026-09-04 | #125 | Retrieval recall@500 gate, four states, no tolerance defaults |
| M0-05 | 2026-09-04 | #126 | Serving-equivalent candidate exclusions in ranker training |
| M0-06 | 2026-09-04 | #126 | Python/Feast parity boundary tested at a materialization timestamp |
| M1-03 | 2026-09-04 | #128 | Last-item transition baseline — built, not yet run |
| M0-08 | 2026-09-04 | #131 | Sealed-test and dataset policy, with the run template enforcing a partition declaration |
| M0-07 | 2026-09-04 | #132 | Rolling-origin windows, leakage assertions, and the clustered user bootstrap |
| M0-09 | 2026-09-04 | #133 | Per-user recall vectors exported from every run, in the shape the study loads |
| M0-12 | 2026-09-04 | #134 | Protocol manifests emitted, plus the sealed-partition field added while it was still free |

Three of these are narrower than their acceptance line reads, and the difference is recorded rather
than glossed. **M0-04** is executable but cannot return a verdict until its tolerances are measured,
which is M0-09 and M0-10. **M0-06** landed the parity test rather than the decision it was scoped to
make: the ADR 0009 amendment that closed the training feature source was withdrawn (#127), and the
question is deferred as D-009 with its alternatives costed.
**M0-07** landed the windows and the aggregation but no run is stamped with a window id yet, and the
bootstrap's interval needs per-user values that only arrive with M0-09.

## Deferred, with the reason

**M0-13 / M0-14 — the surrogate seed-noise study is blocked on a sampling bug, deferred 2026-09-05.**

The one-run-per-configuration policy means a tolerance can only be founded on evaluation-population
sampling noise, not on training stochasticity. The cheap way to recover the missing half was to
measure seed spread once at the 6% pilot scale and carry it as a declared transfer assumption — the
surrogate derivation the tolerance protocol already supports.

That cannot be run as the code stands. `src/training/sasrec.py` draws its subsample with the training
seed:

```python
ratings = subsample_users(ratings, sample_fraction, config.seed)
```

So three runs at seeds 7, 13 and 21 would score three *different* 6% populations, and the measured
spread would confound sample variation with training stochasticity. The tolerance study would refuse
the runs regardless, because its population-equality check would see three different user sets.

This does not affect any comparison made so far: the 6% pilot's BCE and gBCE arms both used seed 42
and therefore shared a population. It only bites when the training seed is the thing being varied,
which is exactly what a noise study does.

The fix is to draw the subsample from a fixed seed independent of the training seed, so the
population is held constant while the model's randomness varies. Until then the retrieval tolerances
rest on the population term alone, and any verdict built on them is precise about sampling noise and
silent about model instability.
## C — Content-based cold-item retrieval (ADR 0017, accepted 2026-09-05)

Approved 2026-09-05 with its roadmap row logged. Sized for the first increment only:
genres and release year, no TMDB ingestion, no serving integration until an offline number exists.

| ID | Size | Work item | Depends on | Acceptance evidence |
|---|---:|---|---|---|
| C-01 | S | Add a cold-item evaluation slice | — | Slice is holdout targets unseen in train; 829 rows / 313 users reproduced from the committed snapshot |
| C-02 | M | Build the content item representation | C-01 | Genre mask plus release year per catalog item, derived not hardcoded; coverage reported including the 14.6% with no genres |
| C-03 | M | Build the content retriever and score it | C-02 | Reachability and recall on the cold-item slice, against the honest baseline of zero |
| C-04 | S | ~~Publish the coverage and relevance result~~ — **done 2026-09-05**, recorded in ADR 0017 | C-03 | Coverage achieved (4,998 cold items, from zero); warm recall 10.3× below item-item at 23.1% of the slate. Not promoted |

The rung's own risk, recorded up front: 313 users is thin, and under the one-run policy there is no
seed spread either, so this rung is far better placed to demonstrate *coverage* — cold items become
reachable at all — than *relevance*. If the slice cannot separate two designs at any plausible effect
size, it is judged on coverage and latency, with relevance recorded as unproven rather than inferred.

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
