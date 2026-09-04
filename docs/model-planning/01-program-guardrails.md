# Program guardrails

These rules apply to every work package and experiment unless an accepted ADR explicitly
changes them.

## Scientific integrity

1. Use temporal splits only. A context contains interactions strictly before its target.
2. Equal timestamps do not establish order. Same-time items cannot appear in one another's
   context.
3. Every MovieLens rating remains a positive interaction under ADR 0002. Rating magnitude,
   dismissals, and watchlist events do not become labels without a new decision.
4. Keep the test partition sealed. Development uses holdout and rolling temporal backtests;
   the test set opens once at an owner-approved release checkpoint.
5. Every experiment changes one interpretable axis or uses a declared factorial design.
6. Define a falsifiable hypothesis, stop rule, compute cap, and baseline before spending
   compute.
7. A failed model is a completed result when the protocol is valid. Do not tune until it wins.

## Comparable evidence

Every run of record must carry:

- DVC dataset revision and derived-dataset fingerprint;
- train/holdout cutoffs and rolling-window definition;
- eligible catalog fingerprint and unknown-item rule;
- label contract, exclusion/filter policy, and cold threshold/routing policy;
- K, metric implementation version, slice definitions, and baseline run IDs;
- architecture, optimizer, sampler, seed, code SHA, environment, and dependency lock hash;
- training examples/users/items, truncation counts, peak RSS, wall-clock, and machine;
- artifact checksums when artifacts are produced.

The comparison layer must refuse mismatched protocol identities rather than printing a
misleading delta.

## Stage-local and end-to-end gates

- Retrieval is judged primarily by recall@500 against the current retrieval champion.
- Ranking is judged primarily by overall NDCG@10 at +3% relative, with warm/cold regression
  tolerances from ADR 0001.
- Retrieval diagnostics include NDCG@500, coverage, reachability, popularity bias, and
  performance by history/item slices; diagnostics cannot replace the primary metric.
- Before a retriever is eligible to serve, rerun the current champion ranker on its candidate
  set and require an owner-approved end-to-end NDCG guardrail.
- Never compare candidate-stage K=500 and ranking-stage K=10 through the same threshold.

## Reproducibility

- Seeds 42, 7, and 13 are the default stochastic evidence set for promotion.
- A bounded negative result may stop after seed 42 only when the predeclared margin is too
  large for plausible seed variance to reverse; say so in the result.
- Same seed, data, code, and environment must reproduce metric and artifact hashes where the
  underlying libraries permit exact determinism. Otherwise define and measure tolerance.
- Store immutable experiment specifications in `docs/experiments/`; environment variables
  may execute a spec but must not be the only record of it.
- No notebook computes a metric of record.

## Model-development versus serving eligibility

Use three explicit states:

1. **Research complete:** the hypothesis has a valid offline verdict.
2. **Promotion eligible:** it clears stage-local and end-to-end offline gates with required
   seed evidence.
3. **Serving eligible:** the exact evaluated artifact passes export equivalence, compatibility,
   latency, reliability, and audit checks.

Only state 3 may change a tenant champion. A research failure skips artifact and serving work.

## Artifact integrity

- Promotion consumes the exact artifact produced by the evaluated run.
- Manifests are typed by retriever/ranker family and fail closed on unknown schemas.
- Store encoder weights, vocabularies, indexes, preprocessing, feature/sequence schema,
  dataset/protocol/code identities, and checksums together.
- Save/load tests compare embeddings, candidates, scores, exclusions, and fallback behavior.
- Demo fixtures and full-data champions have different names and provenance.

## Online safety

- The online path preserves positive-history, seen, dismissal, and unknown-item semantics.
- The threshold remains 10 until an ADR changes it; h0/h1/h3 remain fallback evidence and
  h10 is the first direct learned slice.
- SASRec's isolated encoder target is p99 below 15 ms. The authenticated service target stays
  p99 below 100 ms under the pinned k6 profile. Thresholds do not move for a model.
- A new model must retain tenant isolation, fail-closed exclusions, audit durability, and a
  deterministic fallback.
- Rollback must not require rebuilding an artifact.
- A stated 300–400 ms runtime ceiling does not relax the existing 15 ms encoder and 100 ms service
  p99 gates; clarify the measured operation before introducing any additional latency metric.

## Approval boundaries

- M0 may fix correctness and parity without approving a new roadmap rung.
- M1 is already permitted by accepted ADR 0016.
- M2 is conditional on an offline SASRec win; it is not automatic approval to promote.
- M4–M8 each require their own roadmap/ADR approval before implementation.
- A skipped rung receives a written reason in the roadmap decision log.

## Reviewable delivery

- Prefer PR-sized units listed in [`work-items.md`](work-items.md).
- Couple a contract change with its tests and the ADR or dated decision note that justifies it.
- Couple measured results with run IDs and a scorecard, not unrelated refactors.
- Do not mix model research, generic infrastructure, and frontend work in one PR.
- No status document says work is complete until code and evidence are on the intended branch.
