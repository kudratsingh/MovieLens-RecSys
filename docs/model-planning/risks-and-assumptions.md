# Risks and assumptions

## Assumptions in this plan

- The active modeling line is SASRec under accepted ADR 0016.
- Item-item and LightGBM remain champions until explicit gates pass.
- The current 25M split and threshold 10 remain fixed for comparable development evidence.
- The active SASRec pilot may complete before M0, but promotion evidence waits for M0.
- Models continue to be the primary project track; frontend and generic infra are deferred.
- No decision in this folder approves M4 or later work.

## Risk register

| ID | Risk | Likelihood | Impact | Early signal | Mitigation / stop action |
|---|---|---:|---:|---|---|
| R-01 | Temporal or equal-time leakage | Medium | Critical | Implausible lift; shuffle control remains strong | Strict-prefix canaries; boundary tests; invalidate contaminated run |
| R-02 | Holdout overfitting through repeated decisions | High | High | Gains vanish across rolling windows | Rolling-origin backtests; seal test; freeze before unseal |
| R-03 | Incomparable runs produce a false promotion | High | Critical | Same metric name with different K/routing/catalog | Protocol hashes; gate refuses mismatch |
| R-04 | SASRec full run exhausts RAM | High | High | Pilot RSS scales with examples × 50 | Profile; packed/iterable loader; hard memory cap; no launch before D-004 |
| R-05 | Python negative sampling dominates training | High | Medium | Data time exceeds model time; low examples/sec | Vectorize/bound; log rejection and throughput; profile separately |
| R-06 | False negatives weaken learned retrieval | Medium | High | Frequent rejected draws; head items penalized | Exclude prefix/target; log distribution; controlled sampler ablation |
| R-07 | Popularity collapse masquerades as recall | Medium | High | Coverage/novelty drop while recall rises | Coverage/head-bias/reachability guardrails and source attribution |
| R-08 | Sequence model ignores order | Medium | High | Shuffled control matches SASRec | Stop or use representation only if another ablation proves value |
| R-09 | Retrieval gain harms final ranking | Medium | High | recall@500 rises, NDCG@10 falls | D-002 end-to-end guardrail; retrain ranker on new source |
| R-10 | Training/serving candidate skew | High | High | Served exclusions/source mix absent in training | Integrate ranker exclusion fix; parity fixtures |
| R-11 | Feature-store skew | Medium | Critical | Python/Feast/Redis values differ at same timestamp | Three-way parity tests and versioned contract |
| R-12 | Full-scale genre feature explosion | High | Critical | Estimated rows approach users × catalog | Compact aggregates or candidate-time joins; prohibit full cross join |
| R-13 | Evaluated and served artifacts diverge | High | Critical | Demo trainer rebuilds a different model | Exact artifact promotion, checksums, run IDs, round-trip tests |
| R-14 | Manifest evolves without compatibility rules | Medium | High | Loader guesses file types or defaults | Typed schema version; fail closed; migration tests |
| R-15 | Learned encoder breaches latency budget | Medium | High | isolated p99 >=15 ms | Batch/compile/profile; cap sequence/model; do not relax gate |
| R-16 | Seed variance hides regression | Medium | High | verdict changes by seed | Seeds 42/7/13, aggregate decision, measured tolerance |
| R-17 | Prefect retries duplicate registration/promotion | Medium | High | multiple versions or assignments for same snapshot | Idempotency keys, locks, immutable states, rollback test |
| R-18 | Registry split-brain | Medium | Critical | MLflow and tenant row name different champions | D-008 ownership ADR and reconciliation check |
| R-19 | Drift alerts fire on low-volume tenant noise | High | Medium | unstable alerts with small samples | Minimum-volume rules, aggregate fallback, delayed labels |
| R-20 | Online metric cannot join exposure to outcome | High | Critical | clicks/feedback lack assignment or model version | Event schema and replay test before experiment |
| R-21 | OPE is attempted without propensities | Medium | Critical | logged actions lack action probability | Hard prerequisite; refuse estimator input |
| R-22 | Multi-objective proxies are presented as real outcomes | High | High | “completion” inferred from ratings | D-010; explicit proxy labels; prefer real event collection |
| R-23 | Later rungs expand without approval | Medium | Medium | implementation precedes ADR/roadmap row | Kickoff checklist blocks unapproved work |
| R-24 | Stale docs misdirect contributors | High | Medium | current branch contradicts CLAUDE/status/index | M0-02 normalization and snapshot dates |
| R-25 | Compute spend grows through open-ended tuning | Medium | High | new cells added after results are seen | Predeclared grids/caps; new hypothesis required for extension |

## Model-specific stop rules

### SASRec

Stop the current model family when any of these remains true after correctness checks:

- it fails to beat same-sample popularity and the simple sequential baseline by the approved
  pilot rule;
- shuffled histories match ordered histories within measured noise;
- scale work cannot fit the owner-approved memory/time budget;
- full-data warm recall@500 fails the retrieval gate;
- its retrieval gain disappears under serving-equivalent exclusions;
- end-to-end LightGBM NDCG cannot meet the approved guardrail;
- the exported encoder cannot meet isolated or service latency gates.

Each stop produces a scorecard and dated ADR note. It does not trigger another tuning cycle.

### Sequence-aware ranker

Stop before a neural ranker if adding frozen sequence signals to LightGBM closes the target gap.
Stop TransAct if DIN does not establish that candidate-to-history attention is useful. Stop any
winner that cannot preserve calibration, artifact equivalence, or latency.

### Multi-objective and online learning

Stop or defer if labels are proxies without an approved research framing, if traffic cannot
support the declared minimum sample, if exposure/outcome joins are incomplete, or if propensities
are missing. Statistical sophistication cannot repair missing event semantics.

## Risk-review cadence

- Review R-01 through R-16 at every pre-run checkpoint.
- Review R-13 through R-18 at every artifact or promotion checkpoint.
- Review R-19 through R-22 before any model-monitoring or online-experiment claim.
- Close a risk only with a test, measurement, or accepted decision link.
- Add newly discovered failure modes before implementing the fix; the register should explain
  why the next work item exists.
