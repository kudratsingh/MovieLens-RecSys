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

## Existing full run

Run `6958fd082af6462da812ddd4708230c1` predates this contract. It remains valid
quality evidence and its MLflow metrics, parameters, and tags remain stored,
but its weights existed only in process memory. No code can reconstruct those
weights from the metrics. A servable seed-42 artifact therefore requires one
new full fit after this change lands; future fits retain both local and MLflow
copies before evaluation starts.
