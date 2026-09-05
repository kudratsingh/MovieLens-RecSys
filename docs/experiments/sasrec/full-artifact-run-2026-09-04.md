# SASRec artifact-backed full-data run — 2026-09-04

## Outcome

The frozen ADR 0016 seed-42 configuration completed on the full MovieLens 25M
source and produced a reloadable artifact. MLflow run
`a11af5ed0f0745f68572407237cfa4b9` is `FINISHED`. Its metrics exactly reproduce
the earlier metric-only seed-42 run, while its model bytes now exist in two
independently verified durable locations.

This closes the model-level save/load/export gap. It does **not** promote
SASRec: rolling-window evidence, measured retrieval tolerances, the isolated
encoder and end-to-end latency gates, and the paired-ranker guardrail remain
open. On 2026-09-05 the owner declared this one full run sufficient for SASRec;
seeds 7 and 13 will not be run.

## Lineage and frozen configuration

- source commit recorded by MLflow: `ad423fbf0c1551d6e2c825bcf94528668fa5c983`
- merged implementation: PR #135, squash commit
  `96e52aa18aa1d8ab273911a81845ad29f164cefc`
- experiment specification: [`full.json`](full.json)
- protocol hash:
  `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28`
- seed 42; standard BCE; 32 unique uniform negatives; two epochs
- 64 hidden dimensions; two causal blocks; 50-item maximum sequence
- exact FAISS inner-product retrieval; retrieval vectors L2-normalized
- 25,000,095 source ratings; 20,000,075 train rows; 129,683 holdout rows
- 139,383 train users; 34,461 train items; 19,739,546 training targets

The run also recorded 13,971,722 truncated sequences and 4,448,372,562
cumulative interactions outside the 50-item windows. Those figures describe
the prefix materialization cost; they do not mean source rows were discarded.

## Final metrics

| Metric | Value |
|---|---:|
| warm recall@500 | **0.4651693328** |
| warm NDCG@500 | 0.1734004197 |
| cold recall@500, popularity-routed | 0.5262729520 |
| cold NDCG@500, popularity-routed | 0.4358465704 |
| overall recall@500 | 0.4815962808 |
| overall NDCG@500 | 0.2439558029 |
| catalog coverage | 0.3070427440 |
| retrieved unique items | 10,581 |
| holdout-target reachability | 0.3556457441 |
| mean retrieved-item popularity rank | 3,564.3445 |

Epoch-one and epoch-two warm recall were 0.4447 and 0.4652; their losses were
0.0475 and 0.0387136. The final aggregates exactly match metric-only run
`6958fd082af6462da812ddd4708230c1`, providing an additional deterministic
reproduction check. The synthetic h0/h1/h3/h10 recalls remain
0.476/0.460/0.456/0.000. As ADR 0016 explains, h10 is valid routing evidence
but not sequence-quality evidence because its histories have tied timestamps
and yield no strict-prefix training targets.

## Artifact evidence

The trainer persisted the local recovery copy immediately after fitting and
before final evaluation, then uploaded a second copy to MLflow:

```text
artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/
├── sasrec-manifest.json
└── sasrec-model.zip

mlruns/362463800125436511/a11af5ed0f0745f68572407237cfa4b9/artifacts/model/
├── sasrec-manifest.json
└── sasrec-model.zip
```

- archive size: 9,458,477 bytes
- archive SHA-256:
  `43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`
- vocabulary SHA-256:
  `76f0cf89a8e2be1c3a294d535be3a9edc4ef76ebe3cd599376fc0bb9ae9de8cd`
- local and MLflow archive hashes are identical
- a clean load validated the manifest and tensors, reconstructed all 34,461
  item embeddings, and rebuilt the exact retrieval index

Neither copy is subject to a cleanup step. User histories are intentionally
absent from the model archive: they are runtime request data, not model state.

## Runtime and retained records

The fit took 24,293.3 seconds (6 h 44 min 53 s) on the 12-core Apple M3 Pro
with 36 GiB unified memory, `OMP_NUM_THREADS=1`, and approximately 8.8 GiB peak
resident memory observed during training. The machine had about 212 GiB free
disk space at preflight. The complete console record is retained at:

```text
artifacts/sasrec/logs/full-bce-neg32-seed42-artifact-20260904T1041PDT.log
```

The first preflight was intentionally interrupted before training when the
isolated worktree was found not to expose the versioned synthetic cohort. No
run data was deleted. Its console output remains at
`artifacts/sasrec/logs/full-bce-neg32-seed42-artifact-20260904T1039PDT.log`;
MLflow run `85f77ebbe0034f7d9664ac511da3501b` is marked `FAILED`; and the original
zero-byte interrupted metadata file is preserved beside its reconstructed
`meta.yaml` as `meta.yaml.interrupted-zero-byte-20260904T1040PDT`.

## Recovered gate evidence — 2026-09-05

The completed artifact run predated learned-trainer export of
`per_user_recall.json` and the warm/cold user-count metrics. These were recovered
without retraining under a fail-closed sequence:

1. reload the checksum-pinned model and rebuild its exact index;
2. rebuild runtime history and popularity fallback from the same frozen split;
3. derive the existing protocol through `protocol_manifest.build_protocol`;
4. require its semantic hash to match the stored protocol hash;
5. require all six recomputed recall/NDCG aggregates to equal the stored values
   exactly; and
6. refuse to write if a destination metric, tag, or evidence file already
   exists.

The read-only pass completed in 27.45 seconds. The guarded write pass repeated
the checks in 27.94 seconds, retained the run's `FINISHED` status, and recorded:

- 1,931 warm users and 710 cold users, 2,641 overall;
- configuration identity
  `sasrec-sha256:635f76bf6b1d7b284a0385c5b7fdba832f631fc84ccd9126d4f4934b844747b7`;
- a local recovery copy at
  `artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/evaluation/per_user_recall.json`;
- a second copy at the MLflow run's `per_user_recall.json` artifact path; and
- backfill tags that pin the source model SHA-256 and state that aggregates
  matched exactly.

Both evidence files are 151,097 bytes and have SHA-256
`971fcbb908330b46f693d1c27c654cdf08045d99aa8cdf4732942459b14f269a`.
The first dry-run command failed before data loading because its temporary
script did not expose the worktree on `PYTHONPATH`; that traceback remains in
`artifacts/sasrec/logs/seed42-evidence-dry-run-20260905.log`. The successful
read-only and write records remain in the adjacent `-attempt2.log` and
`seed42-evidence-backfill-20260905.log`. No prior run record was replaced.

The run now has the population and paired-user evidence needed by the retrieval
gate and tolerance instrument. The owner subsequently accepted one full run as
the complete replication set for this SASRec model. Rolling windows, tolerance
derivation, latency, and paired-ranker checks are unchanged.

## Comparable threshold-10 incumbent — 2026-09-05

Recovering the population counts exposed that the previously quoted item-item
reference was not gate-compatible. Its 1,939 warm / 702 cold partition is
exactly the partition produced by the threshold of five that was in force when
that run was made. At today's threshold of ten, eight holdout users with 5–9
training interactions move from warm to cold: 1,931 warm / 710 cold.

The current item-item trainer was therefore run on the same tracked CSV
snapshot without starting or querying Docker. MLflow run
`4b342e87dbf54834be5c719eae9a4e6c` is `FINISHED` and records the same protocol
hash and exactly the same user ids in every SASRec evaluation slice. Its result
is:

| Metric | Item-item | SASRec | Relative change |
|---|---:|---:|---:|
| warm recall@500 | 0.3990569036 | 0.4651693328 | **+16.57%** |
| cold recall@500 | 0.5262729520 | 0.5262729520 | 0.00% |
| overall recall@500 | 0.4332573558 | 0.4815962808 | **+11.16%** |

The item-item per-user artifact is 151,940 bytes with SHA-256
`64d98e2f427e50d64051024073878c61836a740b33573e62078eb707b94af6af`.
Its console record is retained at
`artifacts/sasrec/logs/itemitem-threshold10-current-protocol-20260905.log`, and
explicit tags record the CSV input seam and source commit because the temporary
launcher lived outside Git and MLflow could not infer that metadata.

Running the executable retrieval gate against these two run ids succeeds in
loading both strict envelopes and returns `incomplete` for one reason only:
SASRec seeds 7 and 13 are missing. The owner's later one-run decision makes
that a stale tool-policy mismatch rather than an experiment requirement. No
tolerance value or population mismatch is being hidden behind the result.
