# Artifacts, registry, and serving workstream

## Purpose

Turn an evaluated model into one immutable, reproducible serving bundle. A model is not eligible
for production because a checkpoint exists; it is eligible only when its full dependency graph,
runtime behavior, and rollback path have been proven together.

## Artifact graph

Every bundle must identify exact immutable versions of:

- training data and derived snapshot manifests;
- retrieval model/checkpoint and item/user ID mappings;
- item embeddings, similarity index, or other retrieval structures;
- candidate-source configuration and source-mixing weights;
- ranker model and ordered feature schema;
- feature transformation code/config and online feature snapshot;
- routing, filtering, fallback, and re-ranking policy;
- evaluation protocol, aggregate report, and gate decision;
- runtime image/environment and model-loading configuration.

References are content-addressed where practical. Mutable names such as `latest`, a branch name, or
an MLflow experiment name may aid discovery but cannot define bundle identity.

## Registry states

Use an explicit lifecycle rather than overloading an MLflow stage label:

1. `candidate` — registered and structurally complete, not yet gate-approved.
2. `validated` — offline protocol and artifact checks pass.
3. `serving-eligible` — quality, correctness, latency, capacity, and rollback gates pass.
4. `shadow` — loaded in production-shaped infrastructure without affecting responses.
5. `canary` — receives a bounded, attributable portion of traffic.
6. `champion` — active default for its declared routing scope.
7. `retired` — no longer receives traffic; retained per policy for audit or rollback.

State transitions require a structured decision record containing actor, time, source/target state,
reason, gate outputs, and bundle hash. Never mutate a registered artifact to make it pass later.

## Bundle manifest

The proposed manifest includes:

| Section | Required content |
|---|---|
| Identity | bundle ID, schema version, creation time, source code SHA |
| Lineage | data, model, feature, index, policy, and evaluation artifact hashes |
| Compatibility | input/output schema, ID vocabulary, catalog, feature ordering, runtime ABI |
| Scope | user cohorts, tenant, geography if applicable, cold/warm routing |
| Quality | approved gate decision and protocol hash |
| Operations | resource profile, concurrency envelope, SLO result, health/readiness contract |
| Recovery | previous compatible champion, rollback instructions, retained dependencies |

Loading fails closed on missing artifacts, checksum mismatch, incompatible schema, or unsupported
manifest version. The service should report the bundle ID on health/debug surfaces and metrics.

## Full 25M champion versus fixture bundle

Maintain two deliberately different artifact classes:

- `fixture`: compact, deterministic, fast enough for tests and local demonstrations; never carries
  a production-quality claim.
- `full-25m`: built from the approved MovieLens 25M lineage and evaluated under the production
  protocol; the only class eligible to become the requested champion.

Both use the same manifest schema and loader interfaces. Tests must reject a fixture bundle when a
deployment environment requires `full-25m`.

## Retrieval-index contract

For learned retrieval, package the item encoder/checkpoint, exact item mapping, embedding matrix,
index build configuration, index binary, distance/normalization convention, and catalog exclusions.
Verify:

- embeddings and index cover the same eligible item IDs exactly;
- a brute-force fixture and ANN search agree within a declared recall tolerance;
- query and item vector dimensions/normalization match;
- deterministic tie handling and post-filter refill behavior;
- deleted/ineligible items cannot be returned;
- rebuilding from the same inputs gives an equivalent searchable index.

ANN tuning is an explicit quality-latency experiment, not an invisible serving optimization.

## Ranker and feature compatibility

- Ranker input is validated by ordered feature names, types, null policy, and contract version.
- Candidate-source distribution used for ranker training is recorded in lineage.
- Online transformations share tested code or golden fixtures with historical transformations.
- Feature defaults are explicit and monitored; schema mismatch never silently reorders columns.
- A changed retriever, feature contract, or filtering policy invalidates serving eligibility until the
  paired system is re-evaluated.

## Release flow

1. Build immutable candidate bundle in an isolated run directory.
2. Validate checksums, schemas, artifact completeness, and clean-code provenance.
3. Reproduce a small golden set from raw request through final ranked response.
4. Run offline retrieval and end-to-end gates against the current compatible champion.
5. Run representative latency, load, memory, and failure-injection checks.
6. Mark serving-eligible only if all required evidence is attached.
7. Load in shadow, compare outputs/errors/resources, then run a bounded canary.
8. Promote by pointer/registry transition, never by overwriting the champion bundle.
9. Retain the prior champion and its dependencies until rollback and observation windows close.

## Rollback and failure behavior

- Rollback is a single atomic selection change to a known compatible bundle.
- Warm both new and rollback bundles before traffic changes where memory permits.
- Define fallbacks for model unavailable, index unavailable, feature timeout/staleness, empty
  candidates, and post-filter exhaustion.
- Failures emit reason-coded metrics and never quietly change the evaluated routing policy.
- Drill rollback before canary. Measure time to detection and time to restore.

## Artifact retention

Retain champions, active canaries, rollback candidates, sealed-test release candidates, and their
complete lineage. Development artifacts may expire under a documented policy only after metrics and
manifests remain sufficient to explain the experiment. Never garbage-collect an object referenced by
a retained bundle.

## Test matrix

- missing/corrupt checkpoint, index, mapping, feature schema, or policy;
- checksum and bundle-hash mismatch;
- fixture bundle offered to a full-25M environment;
- incompatible catalog, vector dimension, normalization, or feature order;
- ANN versus exact-search fixture agreement;
- golden request parity between offline and service paths;
- feature timeout/staleness and empty/post-filtered candidate behavior;
- concurrent bundle load, atomic promotion, and rollback;
- registry authorization and illegal state transitions.

## Exit criteria

- A full bundle is immutable, content-identifiable, and loadable from its manifest alone.
- Fixture and full-25M claims are impossible to confuse programmatically.
- Registry transitions consume machine-readable gate evidence.
- Offline-to-online golden tests cover retrieval, features, ranking, filtering, and routing.
- Rollback is rehearsed and preserves a compatible prior champion.
