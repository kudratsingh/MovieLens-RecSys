# M5/M6 — Multi-objective ranking, multi-retriever mixing, and re-ranking

These are separate roadmap rungs. They share data and evaluation concerns but neither is
implicitly approved, and their order is deliberately left to D-012.

## M5 — Multi-objective ranking

### Objective

Move from one implicit-interaction target to an explicit owner-defined utility without inventing
events MovieLens does not contain.

### Entry conditions

- D-010 chooses observable objectives and their meaning.
- ADR 0002 is amended before rating magnitude re-enters modeling.
- Each target's availability time, missingness, and censoring are understood.
- A single-task baseline exists for every proposed target.

### Agenda

1. Audit event/label availability and delayed-outcome windows.
2. Write a label table with positive, negative, unknown, and censored states; unknown is not zero.
3. Define primary utility, weights, constraints, and unacceptable regressions.
4. Train independent task models as the complexity floor.
5. Build MMoE only if shared representation has a testable advantage.
6. Compare fixed weights, constrained optimization, and Pareto frontier.
7. Validate calibration for each head before combining scores.
8. Gate per task, combined utility, warm/cold, and key item/user slices.

### Minimum honest MovieLens option

If the owner chooses a research proxy, name tasks `any interaction` and `rating >= 4` rather than
“watch” and “satisfaction.” Preserve timestamp correctness and document selection bias: MovieLens
contains ratings from users who chose to rate, not impressions shown by this product.

### Acceptance criteria

- Label semantics and availability are tested at temporal boundaries.
- Missing or censored outcomes never become automatic negatives.
- Every head beats or matches its independent baseline within approved tolerances.
- Combined utility reports the trade-off per objective and cannot hide a material regression.
- Serving manifest and audit identify head versions, combination weights, and calibration.
- The scorecard says whether results are production evidence or proxy-only research.

### Stop conditions

- Objectives cannot be observed honestly or joined to exposures.
- One objective is all the product needs.
- MMoE does not beat independent tasks enough to justify coupling.
- Utility weights are chosen after viewing the test set.

## M6 — Multi-retriever mixing and re-ranking

### Objective

Combine useful candidate sources to raise reachable relevance, then manage redundancy and
catalog balance without sacrificing the ranking gate.

### Entry conditions

- At least two sources contribute distinct relevant targets on comparable evaluation.
- D-011 chooses diversity/calibration metrics and limits.
- Candidate source and contribution metadata are stable in artifacts/audits.
- Source budgets and deduplication rules are approved.

### Stage A — Source analysis before mixing

For item-item, SASRec if useful, popularity, and any approved content source, measure:

- source recall@500 and unique relevant hits;
- pairwise candidate overlap and target overlap;
- marginal recall when each source is added last;
- oracle union ceiling before truncation;
- head/tail, genre, item-age, and cold-item distribution;
- retrieval latency and artifact cost.

Skip mixing if no second source adds meaningful unique relevant items.

### Stage B — Deterministic union

- Allocate explicit per-source budgets.
- Deduplicate by movie ID while retaining every contribution/source.
- Apply exclusions before and after union.
- Use a deterministic tie/order policy.
- Expose source IDs or scores to the ranker only after calibration/normalization.
- Test empty, duplicate, all-excluded, and one-source-failed behavior.

### Stage C — Source-aware ranking

Retrain LightGBM or the current ranker on the union distribution. Candidate-source features may
include source presence, normalized source rank, agreement count, and source-specific score after
calibration. Ablate each; never compare a union retriever with a ranker trained only on another
source's candidates and call the pair optimal.

### Stage D — Re-ranking

Begin with MMR because its relevance/diversity trade-off is explicit. Compare DPP only if MMR
establishes value and the additional modeling is justified. Define similarity from approved item
metadata/embeddings and apply business constraints separately from learned relevance.

Report a frontier, not one hand-picked weight:

- NDCG@10/recall@10;
- intra-list similarity or approved diversity metric;
- genre/catalog coverage and novelty;
- source exposure balance;
- latency and failure behavior.

### Acceptance criteria

- Union recall@500 beats the best single source by the approved margin.
- Each source's marginal value and latency cost are recorded.
- Deduplication and exclusions are deterministic and fail closed.
- The ranker is trained/evaluated on the serving source mix.
- Re-ranking meets a predeclared diversity improvement while relevance stays inside its guardrail.
- Candidate source lineage is visible in prediction audits and scorecards.

### Stop conditions

- Union offers no meaningful unique target lift.
- Extra sources add latency/artifact cost without marginal recall.
- Re-ranking diversity gains require an unacceptable relevance loss.
- Source scores cannot be calibrated or interpreted safely enough for joint ranking.

## Recommended order

Choose M6 before M5 when SASRec and item-item have complementary recall and real multi-objective
labels are not ready. Choose M5 first only when the owner has an observable utility that makes the
meaning of “better slate” concrete.
