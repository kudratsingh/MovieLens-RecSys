# M2 — Productionize a winning learned retriever

## Objective

Make the exact learned retriever that passed M1 loadable, auditable, reversible, and safe on the
existing online path. M2 begins only if M1 reaches `promotion eligible`; a negative SASRec result
skips this package for SASRec.

## Design principle

The artifact that is evaluated is the artifact that is registered, published, loaded, and
served. Re-training a compact or convenient approximation during packaging is not promotion.

## Agenda

### Session 1 — Artifact inventory and manifest v2

Define a discriminated manifest with:

- schema version and model-bundle identity;
- tenant compatibility without baking one tenant's assignment into model science;
- retriever type and typed artifact references;
- encoder weights, architecture/config, sequence length, and preprocessing version;
- item vocabulary, padding/unknown IDs, catalog fingerprint, and item matrix/FAISS index;
- ranker artifact and ordered feature contract;
- data, derived snapshot, protocol, code, environment, and source MLflow run identities;
- checksums, creation time, compatibility constraints, and rollback predecessor.

Unknown retriever types, missing fields, hash mismatches, or incompatible schema versions fail
startup.

### Session 2 — Save/load equivalence

Implement export from the fitted M1 object without retraining. On fixed warm/cold/unknown fixtures,
assert before/after equivalence for:

- vocabulary mapping and sequence encoding;
- user and item embeddings within declared numerical tolerance;
- exact-search candidate IDs and order;
- IVF candidates under its deterministic index build contract;
- full-history exclusions and dismissal semantics;
- threshold routing and popularity fallback;
- downstream LightGBM scores/ranks when the full bundle is compared.

Log the complete bundle to MLflow and register the checksums from that run.

### Session 3 — Generic retriever boundary

Replace item-item-specific loading with a small interface that returns candidate IDs plus source
and timing metadata. Keep the coordinator responsible for final exclusions and fallback safety.
The interface must support current item-item and SASRec without conditionals leaking through the
API layer.

### Session 4 — Online input semantics

Verify the sidecar receives ordered positive movie IDs, not a set; dismissals remain separate;
unknown IDs have an explicit audited rule; and histories longer than 50 are encoded from the same
window while the entire known history is excluded from output. No separate Redis sequence cache is
introduced unless the database read or encoder benchmark proves it necessary.

### Session 5 — Model-specific audit and latency

Add:

- retriever family/artifact version;
- sequence contract and vocabulary fingerprint;
- positive/unknown/truncated token counts;
- encoder, ANN, feature, ranker, and total stage timings;
- candidate source counts and fallback reason;
- input-state and exclusion hashes already used by the service.

Benchmark isolated encoding at representative history sizes and concurrency. Then run learned-
serving integration, feature parity, tenant isolation, reliability, and the unchanged k6 profile.

### Session 6 — End-to-end promotion check

Run the champion LightGBM against the exported retriever's candidates. If NDCG fails D-002,
retrain the ranker using that source under serving-equivalent exclusions and gate the pair. Record
whether the promoted unit is a retriever-only change or a coupled bundle.

## Full-data feature constraint

If D-006 chooses full-data production, M2 also requires D-009. The current user×catalog genre-
affinity cross product must be replaced by a compact or candidate-only representation and measured
at MovieLens scale. If D-006 retains a compact production demo, this remains model-platform debt
and the scorecard must say the served tenant does not use the full-data champion.

## Deliverables

- Manifest v2 schema and migration/compatibility tests.
- Exact SASRec export and MLflow artifact record.
- Generic retriever interface and item-item compatibility adapter.
- Sidecar loader and startup validation.
- Extended audit contract.
- Encoder microbenchmark and full service gate evidence.
- Paired retriever/ranker result and serving-eligibility scorecard.

## Acceptance criteria

- Packaging performs no fitting and makes no data-dependent choice.
- Loaded exact-search candidates match the evaluated model on the fixed corpus.
- Artifact hashes and source run IDs appear in MLflow, manifest, audit, and deployment evidence.
- Demo fixture and full-data bundle cannot be confused by name or metadata.
- Sidecar refuses corrupt, incomplete, unknown, or protocol-incompatible bundles.
- Ordered history, unknown items, dismissals, seen items, threshold routing, and fallback pass.
- Isolated encoder p99 is below 15 ms on the named target topology.
- Existing authenticated k6 p99 remains below 100 ms with zero correctness errors.
- Tenant isolation, durable audit, and rollback semantics are unchanged.
- Item-item remains immediately recoverable as the prior bundle.

## Stop conditions

- No offline M1 win: skip M2.
- Export changes model outputs beyond tolerance: do not serve; fix or invalidate artifact.
- Full-scale features cannot fit the approved cost envelope: stop and resolve D-006/D-009.
- Encoder or service breaches latency: do not relax thresholds; profile or decline promotion.
- End-to-end ranking guardrail fails after a correctly retrained ranker: record the coupled system
  as not serving eligible.

## Suggested PR shape

1. Manifest v2 and typed artifact references.
2. SASRec export/save-load equivalence.
3. Generic retriever interface and item-item adapter.
4. Sidecar integration plus online semantic tests.
5. Audit and microbenchmark instrumentation.
6. End-to-end/k6 evidence and promotion verdict.
