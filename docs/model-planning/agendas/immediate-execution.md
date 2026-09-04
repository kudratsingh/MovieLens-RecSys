# Immediate execution agenda — M0 and M1

This is the concrete session order from the repository snapshot. A “session” is one focused
decision or PR-sized unit, not a promised day. Stop at owner-decision gates rather than filling in
an answer implicitly.

## Session 0 — Owner decisions and scope lock

Inputs: D-001 through D-006 and the current SASRec pilot record.

Decide or explicitly defer:

- retrieval gate proposal and end-to-end guardrail;
- what constitutes a 6% pilot advance;
- available compute/RAM/time budget;
- test partition status and unseal trigger;
- whether full-data production artifacts or a compact demo bundle are the target.

Output: dated decisions, or a list of exactly which later sessions are blocked.

## Session 1 — Preserve the experiment record

1. Inventory SASRec runs by code SHA.
2. Record the post-fix BCE/gBCE pair.
3. Tag earlier logic variants superseded without deleting them.
4. Record wall-clock/machine and any interrupted run.
5. Draft the SASRec scorecard without a verdict not yet earned.

Output: M1-01 complete.

## Session 2 — Reconcile branch lineage

1. Review two-tower v2/SASRec stack and the separate ranker-fix commits.
2. Choose merge/cherry-pick order with no cross-worktree overwrite.
3. Run conflict-focused tests.
4. Normalize model status/index documents after ancestry is settled.

Output: M0-01/02 complete; user-owned `docs/progress.md` still untouched.

## Session 3 — Freeze evaluation identity

1. Implement protocol schema and canonical hash.
2. Bind current item-item, SASRec, and ranker run metadata.
3. Add mismatch tests for K, cutoff, threshold, exclusions, catalog, and stage.
4. Make missing semantic identity an explicit invalid/incomparable state.

Output: M0-03 complete.

## Session 4 — Make retrieval promotion executable

1. Amend ADR 0004 with D-001 and D-002.
2. Implement recall@500 decision and seed aggregation.
3. Add negative, positive, mismatch, missing-seed, and large-loss stop fixtures.
4. Keep the existing NDCG gate unchanged.

Output: M0-04 complete.

## Session 5 — Close training/serving skew

1. Integrate serving-equivalent ranker exclusions.
2. Land the historical-Python/Feast-snapshot parity contract.
3. Measure candidate distribution changes on a bounded slice.
4. Add policies to protocol identity.

Output: M0-05/06 complete.

## Session 6 — Prove SASRec correctness and sequence value

1. Add tiny-overfit gate.
2. Build last-item transition baseline.
3. Build deterministic shuffled-sequence control.
4. Run causal/equal-time/target/exclusion tests.

Output: M1-02/03/04 complete.

## Session 7 — Complete evaluation telemetry

1. Add run counts and sampler statistics.
2. Add h0/h1/h3/h10 and policy attribution.
3. Add coverage, reachability, popularity, item/history slices, and embedding health.
4. Add profiler phase timing and peak RSS.

Output: M1-05/06 complete.

## Session 8 — Scale review

1. Profile the 6% run.
2. Project full-data memory and wall-clock with uncertainty.
3. Compare packed/iterable/memmap options.
4. Select the smallest design that fits D-004.
5. Approve or reject a full-data run before implementation expands.

Output: scale design decision and M1-07/08 implementation scope.

## Session 9 — Implement and validate bounded data loading

1. Preserve current builder as fixture oracle.
2. Implement chosen loader and sampler.
3. Compare example identities, target counts, and model inputs exactly on fixtures.
4. Measure throughput/RSS on 0.5% and 6%.

Output: M1-07/08 complete or a documented compute stop.

## Session 10 — Re-run the decisive pilot

1. Freeze the corrected post-scale pilot spec.
2. Run simple baselines, BCE/gBCE candidate, and shuffle control.
3. Apply D-003 without adding cells.
4. Decide stop, narrow repeat, or full-data advance.

Output: M1-09 or clean SASRec closeout.

## Session 11 — Full-data seed 42 checkpoint, conditional

1. Run exact frozen configuration.
2. Validate completeness, resource cap, diagnostics, and protocol identity.
3. Apply the predeclared decisive-loss stop rule.
4. Authorize seeds 7/13 only if still decision-relevant.

Output: seed-42 checkpoint, not a promotion claim.

## Session 12 — Multi-seed and temporal verdict, conditional

1. Run remaining seeds.
2. Run fixed holdout and rolling temporal windows.
3. Aggregate compatible runs and uncertainty.
4. Apply retrieval gate and end-to-end ranker guardrail.
5. Record ADR/roadmap/results/scorecard outcome.

Output: M1 research-complete verdict.

## Session 13 — Serving kickoff, conditional

Only if M1 is promotion eligible:

1. Resolve D-006 and D-009.
2. Approve manifest v2 and generic retriever boundary.
3. Break M2 into export, loader, audit, benchmark, and gate PRs.

Output: M2 kickoff artifacts. If M1 is negative, skip this session and run the roadmap review.

## Definition of immediate-plan completion

- M0 has exited or names one owner decision that blocks it.
- SASRec has a valid, recorded research verdict.
- No full-data/test/serving claim bypassed its gate.
- Item-item and LightGBM remain champions unless explicit serving eligibility is proven.
- The next roadmap rung is proposed, approved, deferred, or skipped through the existing process.
