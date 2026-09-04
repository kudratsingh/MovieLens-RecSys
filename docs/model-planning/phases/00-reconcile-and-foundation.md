# M0 — Reconcile and harden the model foundation

## Objective

Create one trustworthy base for every later experiment: coherent branch ancestry, current
status documents, serving-equivalent training data, versioned protocol identity, and a real
retrieval promotion gate.

The active SASRec pilot does not need to be discarded while M0 runs, but no full-data
promotion claim is valid until this package exits.

## Entry state

- SASRec is accepted and in active implementation on a line stacked over two-tower v2.
- The two-tower v2 bounded result is complete and negative.
- Ranker exclusion and feature-parity fixes live on a separate branch.
- Existing promotion code gates NDCG@10, not retrieval recall@500.
- Project status and ADR indexes disagree about current model state.

## Agenda

### Session 1 — Lineage and status reconciliation

1. Draw the exact commit graph and identify intended merge/cherry-pick order.
2. Review the two ranker-fix commits against SASRec-era sequence/exclusion semantics.
3. Decide whether two-tower v2 and SASRec ship as stacked PRs or one reviewed series.
4. Resolve documentation conflicts without rewriting historical ADR context.
5. Leave `docs/progress.md` untouched until D-014 is answered.

Exit artifact: one branch plan, conflict list, and status files that will change together.

### Session 2 — Protocol identity design

Define a serializable `EvaluationProtocol` or equivalent with at least:

- dataset/DVC revision and derived snapshot hash;
- train cutoff, holdout interval, rolling-window ID, and sealed-test flag;
- label contract version;
- eligible catalog and unknown-item rule;
- cold threshold, routing policy, and exclusion/filter policy;
- model stage, K, metric/slice definitions, and evaluator version;
- candidate source and ranker feature/sequence contract versions.

Decide which fields form a comparison fingerprint and which are informational. A difference in
any semantic field must make the gate return `not comparable`.

Exit artifact: schema, canonical serialization, hash fixtures, compatibility matrix.

### Session 3 — Retrieval gate decision

Resolve D-001 and D-002. Work through these examples before coding:

- deterministic item-item versus three SASRec seeds;
- one missing/failed seed;
- same metrics at K=10 versus K=500;
- threshold 5 versus threshold 10;
- exact versus IVF retrieval when candidate lists differ;
- recall win with end-to-end NDCG regression;
- large one-seed loss that invokes a bounded stop rule.

Exit artifact: ADR 0004 amendment plus executable truth table.

### Session 4 — Training/serving parity closeout

1. Integrate serving-equivalent ranker candidate exclusions.
2. Pin the offline Python feature reconstruction versus Feast historical/Redis boundary.
3. Test positive history, seen items, dismissals, equal timestamps, and target exclusion.
4. Decide how the new protocol fingerprint records these policies.
5. Measure any candidate distribution change rather than assuming it is neutral.

Exit artifact: feature/candidate parity tests and updated Phase 3 model status.

### Session 5 — Temporal validation and test seal

1. Define at least three rolling-origin backtest windows using only pre-test data.
2. Preserve the fixed ADR 0001 holdout for continuity.
3. Decide the final test-unseal trigger and contamination procedure.
4. Define aggregate reporting: mean, dispersion, user bootstrap interval, and worst window.

Exit artifact: split fixtures and an approved test-set policy.

## Deliverables

- Reconciled modeling lineage.
- Synchronized `CLAUDE.md`, ADR index, model roadmap, status ledger, and result headings.
- Versioned evaluation protocol schema and hash.
- Retrieval-specific gate plus CLI and unit tests.
- Training candidate exclusion parity.
- Python/Feast/Redis feature-source contract.
- Rolling temporal backtest support and sealed-test rule.

## Acceptance criteria

- A gate comparison with any semantic protocol mismatch refuses to decide.
- Retrieval gate reads recall@500 and cannot accidentally read NDCG@10.
- Ranking gate behavior and ADR 0001 tolerances are unchanged.
- Ranker training and serving agree on candidate exclusion semantics at a timestamped fixture.
- Feature parity states which layer computes arbitrary historical rows and which layer persists
  serving snapshots; the test covers both.
- Three rolling windows contain no future or cross-window leakage.
- Targeted unit/feature-parity checks, lint, type checking, and `git diff --check` pass.
- Status pages name item-item and LightGBM as champions, two-tower v2 as closed, and SASRec as
  active without claiming an unrecorded result.

## Stop and escalation conditions

- Stop reconciliation if it would overwrite work in another worktree; coordinate branch order.
- Escalate any semantic conflict between the ranker-fix branch and ADR 0016 to a dated ADR note.
- Do not silently choose a retrieval threshold or test policy when D-001/D-005 remain open.

## Suggested PR shape

1. Branch/status reconciliation and doc normalization.
2. Protocol identity plus mismatch tests.
3. ADR 0004 retrieval gate plus implementation.
4. Ranker exclusions and feature-parity closeout.
5. Rolling backtests and test-set policy.
