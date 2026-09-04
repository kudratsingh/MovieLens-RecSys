# Model-program decision register

This register distinguishes assumptions that let planning continue from choices that require
the owner. Recommended defaults are proposals, not approvals. Once decided, record the outcome
in the governing ADR or a dated ADR note and replace `open` here with a link.

| ID | Decision | Needed by | Recommended default | Status |
|---|---|---|---|---|
| D-001 | Exact retrieval promotion gate | Before SASRec full-data verdict | Three-seed mean warm recall@500 >= item-item by 3% relative, cold/overall non-regression within measured tolerances | Approved by owner 2026-09-04 and recorded in ADR 0004; retrieval-tolerance measurement remains required |
| D-002 | End-to-end guardrail for retriever promotion | Before serving any new retriever | Current LightGBM NDCG@10 must not regress outside ADR 0001 tolerances on the new candidate set | Approved by owner 2026-09-04 |
| D-003 | SASRec advance/stop rule | Before the full-data seed-42 run lands | Predeclared bands anchored on item-item's 0.400144 and the pilot's own 12.0% same-sample deficit | Open; options costed in [`memos/d003-full-run-stop-rule.md`](memos/d003-full-run-stop-rule.md) |
| D-004 | SASRec compute budget | Before full-data run | Local machine or a standard single-cloud-GPU run is allowed; estimate cost/time first and keep the three-seed plan bounded | Partially answered 2026-09-04; exact training-time/RAM ceiling follows profiling |
| D-005 | Test-set unseal trigger | Before first claimed release candidate | Treat as sealed; open once after model/config/gates are frozen and serving eligibility passes | Provisionally answered 2026-09-04; repository audit found no test evaluation |
| D-006 | Full 25M model versus compact demo fixture in production | Before M2 architecture | Serve the exact full-data champion; preserve compact bundle only as an explicit demo fixture | Answered 2026-09-04 |
| D-007 | 25M-to-32M migration trigger | Before any dataset expansion | Stay on 25M unless a named slice is underpowered or a model hypothesis needs newer events | Open |
| D-008 | Registry source of truth | Before M3 | MLflow owns immutable runs/artifacts/versions; Postgres owns tenant assignment and rollout state | Open |
| D-009 | Feature representation and the training feature source | Before any full-25M materialization, and before M2 | Compact per-user genre-mask counts (measured exact form) plus on-demand computation over the retrieved slate; no user-by-catalog table | Deferred by owner 2026-09-04 to prioritise modeling; costed in [`memos/feature-source-boundary.md`](memos/feature-source-boundary.md) |
| D-010 | Multi-objective labels and utility | Before M5 | Do not invent completion/click labels from MovieLens; wait for observable product events or constrain the rung to rating-derived research proxies labeled as such | Owner input required |
| D-011 | Re-ranking objective | Before M6 | Choose one primary diversity metric plus relevance guardrail; begin with MMR as interpretable baseline | Open |
| D-012 | M5 versus M6 ordering | After M4 | Prefer M6 first if multiple retrievers are useful; prefer M5 first only when utility and labels are ready | Open later |
| D-013 | Frontier compute/provider budget | Before M8 | A fixed-cost research spike; no open-ended foundation-model training | Owner input required later |
| D-014 | Fate of untracked `docs/progress.md` | Before status-doc cleanup | Preserve untouched; owner chooses archive, refresh, or delete in a separate change | Owner input required |
| D-015 | Phase 4 automation timing | Before M3 | Stabilize SASRec experiment/export contracts first, then automate; SASRec pilots may continue meanwhile, but promotion/serving waits for M0 | Answered 2026-09-04 |
| D-016 | Meaning of the requested 300–400 ms runtime | Before M2 latency review | Preserve existing stricter p99 targets: SASRec encoder <15 ms and authenticated service <100 ms | Answered 2026-09-04; 300–400 ms was an assumption about growth, not a request to relax gates |

## D-001 — Retrieval promotion gate

Questions the amendment must answer:

- Is warm recall@500 primary because learned retrieval serves only histories at or above 10,
  or is overall recall primary with attribution?
- Is +3% relative the correct materiality threshold for retrieval?
- What are warm, cold, and overall seed tolerances, and how are they measured?
- Are seeds paired against a deterministic item-item run or compared through bootstrap/user-
  level confidence intervals?
- Which protocol mismatches cause an automatic `not comparable` result?
- Does a large negative pilot allow a one-seed closeout?

Recommended answer: primary warm recall@500, +3% relative over item-item, three-seed mean for a
positive claim, user bootstrap intervals as supporting uncertainty, cold/overall non-regression,
and a hard refusal when protocol fingerprints differ.

**Owner decision, 2026-09-04:** approved. Using the current approximate item-item warm
recall@500 of 0.3991, the illustrative SASRec floor is about 0.4111; the executable gate must
calculate its threshold from the protocol-compatible incumbent rather than hard-code that example.
Before implementation closes, measure retrieval-specific seed tolerances for the cold and overall
guardrails instead of borrowing the ranker's NDCG tolerances.

## D-002 — Joint system guardrail

A retriever can surface more relevant holdout items yet create a candidate distribution the
existing ranker orders poorly. Recommended answer: stage-local recall decides whether retrieval
learned anything; serving promotion also requires the champion LightGBM ranker to preserve
NDCG@10 within ADR 0001's warm/cold tolerances. If it fails, retrain the ranker on the new source
and gate the paired system as a new bundle.

**Owner decision, 2026-09-04:** approved.

## D-003 — SASRec pilot rule

**2026-09-04 update — the pilot already answered part of this.** The full options memo is
[`memos/d003-full-run-stop-rule.md`](memos/d003-full-run-stop-rule.md). Its central finding: the 6%
pilot measured popularity (0.1974), item-item (0.3619) and SASRec-BCE (0.3186) on the *same*
subsample, so SASRec's arm sits **12.0% below the incumbent**, not above it. The pilot record reads
as a pass because ADR 0016's stop rule named popularity and never named item-item. The full-data run
now executing therefore tests one hypothesis — that a 12% same-sample deficit closes and reverses to
a 3% surplus on 16.7× the data — and the rule for what its single seed authorizes should be fixed
before the number is visible.


The current ADR says the pilot should beat popularity or a last-item nearest-neighbor baseline,
but the latter has not been established and no margin is named. Recommended interpretation:

- pilot is a defect/viability gate, not promotion evidence;
- compare BCE and gBCE to same-sample popularity, item-item, last-item transition, and a shuffled-
  sequence control;
- advance one frozen SASRec configuration only when it beats both simple floors and order is not
  irrelevant;
- if it misses a simple floor by a margin larger than measured seed noise, close without full run;
- if results are close, repeat the winning arm at seeds 7 and 13 before choosing.

## D-004 — Compute budget information needed

The owner should specify:

- available hardware: current CPU/RAM, local GPU, or approved cloud GPU;
- maximum wall-clock per pilot and per full seed;
- maximum total compute/spend for one model family;
- whether overnight unattended local jobs are acceptable;
- the acceptable peak-memory ceiling and minimum free-space reserve.

**Owner direction, 2026-09-04:** use the local machine or cloud GPUs, with a budget typical for
the model being trained. Operationally, that means local correctness/pilot work first, then the
smallest standard single-GPU shape that meets the measured memory requirement for full seeds. The
pre-run review records the provider/instance, hourly price, projected hours, and projected total;
expanding beyond the frozen three-seed plan requires a new estimate and approval. An exact time/RAM
ceiling remains open until the 6% profiler establishes a credible full-data projection.

## D-005 — Test-set policy

Recommended trigger: the model family, data snapshot, configuration, seed aggregation, offline
gates, artifact checks, and serving checks are frozen, and the owner is deciding whether to call
that bundle a release candidate. Record the unseal commit and do not tune against the result. If
the test window has already influenced decisions, declare it contaminated and define a new final
window before proceeding.

**Repository audit, 2026-09-04:** no trainer reads `split.test` for model metrics or decisions.
Existing trainers only log its row count, and the committed result record describes holdout
evaluation. Git history likewise contains no test-evaluation path. The plan therefore treats the
partition as sealed unless the owner later recalls an external/manual inspection that influenced
model choices.

## D-006 — What production is proving

Two legitimate products exist:

1. A compact portfolio demo proving serving behavior with reviewed personas.
2. A full-data model system proving the exact measured MovieLens champion can be deployed.

The current repository robustly demonstrates the first, not the second. The recommended program
keeps both but labels them explicitly and makes the second M2's target. The owner may instead
choose a compact production demo, in which case full-scale feature/materialization work becomes a
research-platform goal rather than a deployment blocker.

**Owner direction, 2026-09-04:** production targets the exact full MovieLens 25M champion. The
compact persona-trained bundle remains useful only as a clearly labeled demo/test fixture. Full-
scale feature representation, artifact export, and serving equivalence are therefore M2 blockers.

## D-016 — Runtime clarification

The owner initially expected runtime below 300–400 ms because larger models and higher usage
normally increase work. On 2026-09-04 the owner approved preserving the project's stricter
requirements: SASRec's isolated encoder p99 below 15 ms and the authenticated end-to-end service
p99 below 100 ms.

Capacity or model growth does not automatically relax either SLO. A larger model must use an
appropriate combination of precomputation, ANN, batching, compilation, quantization, distillation,
caching, concurrency controls, or additional serving capacity. Measure p50/p95/p99 by concurrency,
history length, candidate count, and model version. If the unchanged representative-load gate
fails, the model is not serving eligible even when its offline quality improves.

## D-009 — Feature representation and the training feature source

Costed in full in [`memos/feature-source-boundary.md`](memos/feature-source-boundary.md), written
after the 2026-09-03 ADR 0009 amendment was withdrawn for closing the question without pricing the
alternatives.

The short form. Seven of the eight ranker features are single-entity; only `user_genre_affinity` is
user×item, and it is the one that forces the answer. The current materialization cross-joins users
against the whole catalog — 10,146,296,843 rows at full scale. The exact compact form is a per-user
map from 20-bit genre mask to count: **11,532,291 distinct (user, mask) pairs across all 25M
ratings, 880× smaller**, mean 71 masks per user. This corrects the recommended default in the row
above as originally written: a 20-length per-genre vector cannot reproduce the feature, because the
definition is a union over the candidate's genres.

Deferred, not decided. The modeling ladder does not touch the materialization path, so the deferral
is free until a full-data champion is materialized for serving.

## D-010 — Multi-objective truthfulness

MovieLens contains rating values and timestamps, not impressions, clicks, viewing completion,
watch duration, or skips. The project must not name proxies as real outcomes. Owner choices are:

- defer M5 until the running product collects explicit outcomes;
- perform a clearly labeled research exercise with `interaction` and `rating >= 4` tasks;
- introduce another public dataset with appropriate events under a new data/evaluation ADR.

The selected objectives need an explicit utility, calibration requirements, and a statement of
which trade-offs are unacceptable.
