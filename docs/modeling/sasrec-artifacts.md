# SASRec artifact contract

M2-02 turns a fitted SASRec process into an immutable, reloadable model. The
training runner writes the artifact immediately after `fit` returns and before
final evaluation, so an evaluation or tracking failure cannot discard hours of
learned weights.

## Durable layout

Every MLflow run gets its own directory:

```text
artifacts/sasrec/<mlflow-run-id>/
├── sasrec-manifest.json
└── sasrec-model.zip
```

The writer opens both files in exclusive-create mode and refuses to replace an
existing path. The local run directory remains the recovery copy. The trainer
then uploads the same two files to the run's MLflow `model/` artifact path and
logs the model checksum, vocabulary checksum, and manifest location as tags.
Failure to upload cannot erase the local copy; failure later in evaluation
cannot erase either copy. No cleanup step is part of this workflow.

The same run also records the shared evaluation-protocol envelope before
training starts: its dataset revision, derived snapshot and catalog hashes,
split boundaries, routing and filtering contracts, retrieval cutoff, and
training seed. The model checksum identifies the fitted bytes; the protocol
hash identifies the question their reported metrics answered. Promotion needs
both identities, so a reloadable artifact cannot be mistaken for comparable
evaluation evidence merely because it can serve candidates.

## Contents and identity

`sasrec-model.zip` is a deterministic archive with fixed member ordering and
timestamps. It contains canonical JSON metadata plus one non-pickle NumPy array
for every encoder state tensor. Metadata records:

- every `SASRecConfig` value;
- the dense-index-to-movie-id vocabulary and its SHA-256;
- the explicit unknown index and cold-start threshold;
- the complete, sorted state-key list; and
- the artifact type and schema version.

`sasrec-manifest.json` pins the archive SHA-256, vocabulary SHA-256, model
dimensions, sequence length, loss parameters, and the ordered-history contract:
oldest-to-newest inputs, left-zero padding, and L2-normalized retrieval. It
contains no timestamp, host path, or MLflow run id, so two identical fitted
models produce byte-identical artifacts.

The archive deliberately excludes user histories. Those are request data, may
contain tenant-scoped information, and must not become model state. The loaded
retriever accepts ordered movie ids at runtime, maps post-snapshot ids to one
explicit unknown token, and keeps positive history separate from output
exclusions.

## Load-time failure boundary

Loading fails before retrieval on any of these conditions:

- missing or modified archive;
- unsupported schema, artifact type, sequence order, padding, or normalization;
- unsafe, duplicate, missing, or unexpected archive members;
- manifest/config/item-count/vocabulary disagreement;
- duplicate item ids or an invalid unknown index; or
- missing, additional, wrong-shaped, or wrong-typed encoder tensors.

After validation, loading reconstructs the encoder strictly, switches it to
evaluation mode, and rebuilds the FAISS index from the pinned item embeddings.
The acceptance test requires exact user-embedding equality and identical
candidate ids before and after the round trip. A second export of the same
model must have the same archive and manifest bytes.

## Full-data artifact run

Run `a11af5ed0f0745f68572407237cfa4b9` exercised this contract on the frozen
seed-42 full-data configuration. Its 9,458,477-byte archive is retained both
under `artifacts/sasrec/<run-id>/` and the run's MLflow `model/` path. Both
copies have SHA-256
`43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`,
and a clean load rebuilt the exact index over 34,461 items. The run reproduced
the earlier metric-only run's warm recall@500 of 0.4651693328 exactly.

The earlier run `6958fd082af6462da812ddd4708230c1` remains preserved as
historical quality evidence, but no weights can be reconstructed from its
metrics. The complete rerun record, including resource use and recovery notes,
is [`../experiments/sasrec/full-artifact-run-2026-09-04.md`](../experiments/sasrec/full-artifact-run-2026-09-04.md).
