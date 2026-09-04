# Model-program decision register

This register distinguishes assumptions that let planning continue from choices that require
the owner. Recommended defaults are proposals, not approvals. Once decided, record the outcome
in the governing ADR or a dated ADR note and replace `open` here with a link.

| ID | Decision | Needed by | Recommended default | Status |
|---|---|---|---|---|
| D-001 | Exact retrieval promotion gate | Before SASRec full-data verdict | Three-seed mean warm recall@500 >= item-item by 3% relative, cold/overall non-regression within measured tolerances | Open; ADR 0004 amendment required |
| D-002 | End-to-end guardrail for retriever promotion | Before serving any new retriever | Current LightGBM NDCG@10 must not regress outside ADR 0001 tolerances on the new candidate set | Open |
| D-003 | SASRec pilot advance/stop margin | Before interpreting the 6% pilot | Must beat same-sample popularity and last-item baseline; do not infer item-item parity from one noisy pilot seed | Open |
| D-004 | SASRec compute budget | Before full-data run | Profile first; agree maximum wall-clock, peak RAM, and total seed budget; no unbounded local run | Owner input required |
| D-005 | Test-set unseal trigger | Before first claimed release candidate | Open once, after model family/config/gates are frozen and serving eligibility passes | Open |
| D-006 | Full 25M model versus compact demo fixture in production | Before M2 architecture | Serve exact full-data champion when feasible; preserve compact bundle only as an explicit demo fixture | Owner input required |
| D-007 | 25M-to-32M migration trigger | Before any dataset expansion | Stay on 25M unless a named slice is underpowered or a model hypothesis needs newer events | Open |
| D-008 | Registry source of truth | Before M3 | MLflow owns immutable runs/artifacts/versions; Postgres owns tenant assignment and rollout state | Open |
| D-009 | Feature representation at full scale | Before full-data serving | Compact user genre vector + item metadata, joined for retrieved candidates; no user-by-catalog table | Open; ADR 0009 note required |
| D-010 | Multi-objective labels and utility | Before M5 | Do not invent completion/click labels from MovieLens; wait for observable product events or constrain the rung to rating-derived research proxies labeled as such | Owner input required |
| D-011 | Re-ranking objective | Before M6 | Choose one primary diversity metric plus relevance guardrail; begin with MMR as interpretable baseline | Open |
| D-012 | M5 versus M6 ordering | After M4 | Prefer M6 first if multiple retrievers are useful; prefer M5 first only when utility and labels are ready | Open later |
| D-013 | Frontier compute/provider budget | Before M8 | A fixed-cost research spike; no open-ended foundation-model training | Owner input required later |
| D-014 | Fate of untracked `docs/progress.md` | Before status-doc cleanup | Preserve untouched; owner chooses archive, refresh, or delete in a separate change | Owner input required |
| D-015 | Phase 4 automation timing | Before M3 | Stabilize SASRec experiment/export contracts first, then automate; manual model experiments may continue meanwhile | Open |

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

## D-002 — Joint system guardrail

A retriever can surface more relevant holdout items yet create a candidate distribution the
existing ranker orders poorly. Recommended answer: stage-local recall decides whether retrieval
learned anything; serving promotion also requires the champion LightGBM ranker to preserve
NDCG@10 within ADR 0001's warm/cold tolerances. If it fails, retrain the ranker on the new source
and gate the paired system as a new bundle.

## D-003 — SASRec pilot rule

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

Until answered, the plan permits profiling and pilot runs but not an unbounded full-data sweep.

## D-005 — Test-set policy

Recommended trigger: the model family, data snapshot, configuration, seed aggregation, offline
gates, artifact checks, and serving checks are frozen, and the owner is deciding whether to call
that bundle a release candidate. Record the unseal commit and do not tune against the result. If
the test window has already influenced decisions, declare it contaminated and define a new final
window before proceeding.

## D-006 — What production is proving

Two legitimate products exist:

1. A compact portfolio demo proving serving behavior with reviewed personas.
2. A full-data model system proving the exact measured MovieLens champion can be deployed.

The current repository robustly demonstrates the first, not the second. The recommended program
keeps both but labels them explicitly and makes the second M2's target. The owner may instead
choose a compact production demo, in which case full-scale feature/materialization work becomes a
research-platform goal rather than a deployment blocker.

## D-010 — Multi-objective truthfulness

MovieLens contains rating values and timestamps, not impressions, clicks, viewing completion,
watch duration, or skips. The project must not name proxies as real outcomes. Owner choices are:

- defer M5 until the running product collects explicit outcomes;
- perform a clearly labeled research exercise with `interaction` and `rating >= 4` tasks;
- introduce another public dataset with appropriate events under a new data/evaluation ADR.

The selected objectives need an explicit utility, calibration requirements, and a statement of
which trade-offs are unacceptable.
