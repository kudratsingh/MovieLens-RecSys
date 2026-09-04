# Current model state and gap assessment

This snapshot describes committed code at `1d189a8` on 2026-09-04. It distinguishes
research models, the small served demo bundle, and lifecycle capabilities; treating them
as one thing would overstate readiness.

## Current champions and completed experiments

| Layer | Model | Evidence | Status |
|---|---|---|---|
| Retrieval | Item-item cosine | Warm recall@500 about 0.399 at the shared threshold of 10 | Champion |
| Retrieval | Two-tower v1 | Best swept warm recall@500 0.0591, far below item-item | Measured, not promoted |
| Retrieval | Two-tower v2 | Bounded 6% complete arm 0.0435 vs popularity 0.1974 | Stop rule fired; closed |
| Ranking | LightGBM LambdaRank | Warm/overall NDCG@10 0.0705/0.1993; +21.2%/+15.5% vs ALS | Champion |
| Retrieval | SASRec/gSASRec | Implementation and pilot runner exist; latest 6% run is not yet recorded | Active research |

The two-tower v2 result is conclusive for its approved bounded question: do not run a
full-data v2 or begin a third tuning cycle without new evidence and a new owner decision.

## Repository reality

- `main` and `origin/main` are at `1821dc5` from 2026-08-30.
- The current SASRec line is stacked on the two-tower v2 line and is ahead of `main`.
- `fix/ranker-training-exclusions` contains two model-platform fixes not present in the
  SASRec lineage: point-in-time serving-equivalent exclusions and a tested definition of
  the Python-training/Feast-serving feature boundary.
- Several status and index documents still describe SASRec as deferred or the next rung
  as undecided. These must be reconciled before publication.
- The original worktree contains an untracked, stale `docs/progress.md`. It is user-owned
  and must not be deleted or silently treated as current truth.

## What the SASRec branch already provides

- Shared strict-prefix construction with equal-timestamp interactions excluded from one
  another's context.
- A causal, pre-normalized Transformer with tied item embeddings.
- Seeded unique negative sampling that excludes padding, the target, and prefix items.
- BCE and gBCE loss modes with a bounded sweep runner.
- Exact FAISS retrieval for pilot measurement and configurable IVF retrieval.
- Threshold-10 popularity fallback and full-history seen-item exclusion.
- Unit checks for causality, deterministic fitting, loss behavior, exclusion, and dropout-
  free retrieval.
- MLflow logging for configuration, loss, wall-clock, and basic warm/cold/overall recall
  and NDCG.

This is an in-flight implementation, not a new-model placeholder. M1 resumes it rather
than designing SASRec from scratch.

## SASRec gaps against ADR 0016

The following are promised by the ADR but not yet complete in the runner or result record:

- a deterministic tiny-overfit gate with a named pass threshold;
- a last-item transition or recency-aware baseline;
- a shuffled-sequence control proving order adds information;
- sequence, target, truncation, sampler-rejection, and collision counts;
- synthetic h0/h1/h3/h10 metrics and serving-policy attribution;
- catalog coverage, target reachability, novelty/head-bias, and cold-item diagnostics;
- peak memory and isolated inference timing;
- a fixed multi-seed promotion policy;
- an executable retrieval-specific promotion gate;
- immutable encoder/vocabulary/index export and load equivalence;
- private-sidecar integration, encoder p99 below 15 ms, and unchanged service p99 below
  100 ms.

## Scale risk before a full MovieLens run

The current sequence builder materializes every eligible prefix as a dense
`[n_examples, 50]` int32 tensor. At roughly 20 million examples, histories alone are
approximately 4 GB before positive targets, permutations, negative batches, model state,
FAISS, pandas, and allocator overhead. Negative sampling also loops row by row in Python.

A full-data run is therefore blocked on measured peak RSS and wall-clock projections. The
preferred implementation direction is a packed per-user or iterable/memory-mapped dataset
that forms windows per batch, plus a bounded vectorized sampler. The plan does not require
that exact design if another design proves the same memory and determinism properties.

## Evaluation gaps

- `src/evaluation/gate.py` implements the ranking gate over NDCG; it is not a retrieval
  recall gate.
- ADR 0004 says a learned retriever must beat item-item at recall@500, but the exact
  threshold, primary slice, guardrails, uncertainty, and seed aggregation remain unpinned.
- SASRec MLflow runs do not yet log the user counts expected by the existing gate reader.
- Run comparability currently checks too little. A trustworthy comparison must bind the
  dataset revision, split, cutoff, catalog, K, cold threshold, routing, exclusions, feature
  or sequence contract, seed set, and code revision.
- The same holdout has been used for repeated model decisions. Rolling temporal backtests
  should become development evidence, while the test partition remains sealed for a named
  release decision.
- A retrieval win can still degrade final LightGBM NDCG. The owner must decide whether
  end-to-end NDCG non-regression is a promotion guardrail; this plan recommends yes.

## Training and feature-platform gaps

- Ranker training still constructs point-in-time features in Python rather than through
  Feast historical retrieval. The parity boundary needs one documented, tested contract.
- The ranker candidate set in the active lineage does not apply the same exclusions as
  serving; the fix exists on a separate branch.
- The current user-item genre-affinity materialization cross-joins users and movies. It is
  appropriate for the compact demo but can create billions of rows at full MovieLens scale.
  Full-scale serving needs compact user genre aggregates plus item metadata or candidate-
  only computation.
- Derived training datasets are not yet versioned as first-class artifacts with schema and
  protocol fingerprints.

## Artifact and serving gaps

- The measured offline champions and the served demo bundle are not the same artifacts.
- The checked-in demo bundle is intentionally compact, tenant-specific, and retrained with
  demo-only settings. It must be labeled as a fixture, not called the full-data champion.
- Offline item-item uses `implicit`; the served `CandidateIndex` is a separate pure-Python
  implementation. No exporter proves output equivalence.
- Manifest schema v1 assumes a JSON item-item index plus a LightGBM text model. It has no
  retriever discriminator, encoder weights, vocabulary, FAISS artifact, preprocessing
  fingerprint, or protocol/data/code lineage.
- MLflow runs log metrics and parameters but do not yet register the exact artifact later
  promoted and served.

## Lifecycle gaps

- `pipelines/` has no Prefect flows beyond its package marker.
- Promotion is a manual CLI decision; there is no idempotent snapshot-to-registration flow.
- The registry source-of-truth boundary between MLflow and Postgres tenant assignments is
  not written down.
- Model-specific monitoring, drift simulation, experiment routing, shadowing, and off-policy
  evaluation are not implemented.
- Current feedback records decisions, but trustworthy online learning also needs impressions,
  non-actions/outcomes, assignment, and eventually propensities.

## Readiness statement

The repository has a strong production-shaped demo serving path for item-item plus
LightGBM. It does not yet have a production lifecycle for the exact full-data artifacts
that won offline, and SASRec is research-only. Bridging `train -> evaluate -> immutable
artifact -> register -> promote -> serve -> observe` is the central program dependency.
