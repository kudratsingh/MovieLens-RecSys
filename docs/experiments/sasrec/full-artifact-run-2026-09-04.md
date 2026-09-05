# SASRec artifact-backed full-data run — 2026-09-04

## Outcome

The frozen ADR 0016 seed-42 configuration completed on the full MovieLens 25M
source and produced a reloadable artifact. MLflow run
`a11af5ed0f0745f68572407237cfa4b9` is `FINISHED`. Its metrics exactly reproduce
the earlier metric-only seed-42 run, while its model bytes now exist in two
independently verified durable locations.

This closes the model-level save/load/export gap. On 2026-09-05 the owner
declared this one full run sufficient for SASRec; seeds 7 and 13 will not be
run.

As of 2026-09-05 three of the gates below have since closed in SASRec's favour:
the single-run retrieval-quality gate returned `promote`, the isolated encoder
budget passed at p99 0.285 ms, and the fixed current LightGBM passed D-002's
non-regression check on SASRec candidates. What that adds up to is **retrieval
promotion eligible, end to end blocked on the ranker** — the ranker has not yet
been retrained on SASRec candidates. Rolling-window evidence, measured retrieval
tolerances, and the authenticated end-to-end latency gate also remain open. None
of this is a terminal verdict on SASRec v1.

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

At the time of the evidence recovery, running the executable retrieval gate
against these two run ids loaded both strict envelopes and returned
`incomplete` for one reason only: SASRec seeds 7 and 13 were missing. The
owner's later one-run decision made that a stale tool-policy mismatch rather
than an experiment requirement. No tolerance value or population mismatch was
hidden behind the result. The following section records the verdict after the
gate was brought into line.

## Single-run retrieval-quality verdict — 2026-09-05

PR #139 made the seed set an explicit gate input, and PR #143 put a paired
user-bootstrap uncertainty band around the warm positive claim. The recovered
SASRec and protocol-compatible item-item runs were therefore re-evaluated with
`--seeds 42` and both cold/overall tolerances set to zero. Zero is not presented
as a measured tolerance: it is the strictest possible non-regression boundary,
used to establish whether any valid non-negative tolerance could change the
decision.

The executable gate returned `promote` for retrieval quality:

- warm recall changed **+16.57%**; the population-only one-sided 95% lower
  bound is **+12.88%**, above the required +3%;
- cold recall changed exactly **0.00%** and passes even with no regression
  allowance; and
- overall recall changed **+11.16%** and also passes with no allowance.

The exact machine-readable output is retained in
[`single-run-retrieval-verdict-2026-09-05.json`](single-run-retrieval-verdict-2026-09-05.json).
Because the two guardrails pass at zero, a measured tolerance greater than or
equal to zero cannot reverse this retrieval-quality verdict. At this point the
run still recorded `serving_eligible=false`: paired LightGBM NDCG@10,
rolling-window, and latency evidence were separate gates, and training-seed
variability remained unmeasured under the owner-approved single-run regime.

## Isolated encoder latency — 2026-09-05

The checksum-pinned artifact was loaded through `load_sasrec`, its exact FAISS
index rebuilt, and its public `encode_movie_history` boundary measured with a
50-item history. The process pinned PyTorch intra-op and inter-op execution to
one thread, warmed up 500 calls, then recorded 10,000 calls on the same Apple
M3 Pro host used for training:

| Statistic | Milliseconds |
|---|---:|
| mean | 0.2642 |
| p50 | 0.2600 |
| p95 | 0.2710 |
| p99 | **0.2846** |
| maximum | 5.3457 |

The isolated encoder therefore passes ADR 0016's unchanged **p99 <15 ms**
budget. The benchmark includes history-to-index mapping and tensor creation but
not FAISS search, feature lookup, ranking, networking, or audit persistence; it
closes the isolated encoder gate, not the authenticated service's p99 <100 ms
gate. The executable benchmark is `src/evaluation/sasrec_latency.py`, and the
exact result is retained in
[`encoder-latency-2026-09-05.json`](encoder-latency-2026-09-05.json).

## Fixed-ranker D-002 guardrail — 2026-09-05

The historical full-window ranker run
`517fdc75136842e188018ae0a9210c20` retained metrics but not weights. Its MLflow
record is no longer present in the current Docker backend or the local file
store, so reconstruction admits only what remains verifiable in
`docs/results.md`: seed 42, the exact training shape, and all six metrics at
their published six-decimal precision. The dedicated runner also restores the
pre-#126 training-negative behavior used by that run.

The reconstruction produced exactly 154,003 positives, 87,794 groups, and
1,843,674 rows, then saved the ranker before evaluation. Its item-item results
reproduced all six published metrics. The same immutable booster was then used
for both holdout arms:

| Metric | Item-item | SASRec | Relative change |
|---|---:|---:|---:|
| warm recall@10 | 0.048867 | 0.063871 | +30.70% |
| warm NDCG@10 | 0.069967 | 0.071138 | **+1.67%** |
| cold recall@10 | 0.077805 | 0.077805 | 0.00% |
| cold NDCG@10 | 0.544948 | 0.544948 | 0.00% |
| overall recall@10 | 0.056647 | 0.067617 | +19.37% |
| overall NDCG@10 | 0.197659 | 0.198516 | **+0.43%** |

The fixed ranker passes D-002: both slice guardrails pass, with warm improving
and cold unchanged. The retained executable output also computes ADR 0001's
+3% overall clause and returns `DO NOT PROMOTE`, but that positive-gain clause
is diagnostic here because it applies to a newly trained challenger ranker,
not a fixed-ranker non-regression check. SASRec is retrieval-promotion eligible;
end-to-end promotion remains blocked on retraining LightGBM from SASRec
candidates. Seeds 7 and 13 are not required under the owner's one-run policy.

The next step is that retrain: a LightGBM trained on SASRec candidates under
PR #126's serving-equivalent exclusions, gated as a new bundle against an
item-item plus LightGBM incumbent built from the identical positives and the
same exclusions. Rung 3a follows — the SASRec user embedding and its
dot-product score against the candidate item as point-in-time ranker features.

All records were retained:

- failed Docker-MLflow attempt `be8d69130f2a45ee8e690909079d3197`, whose
  artifact upload failed only because the client resolved `/mlartifacts` on the
  read-only host filesystem; its `FAILED` status was retained and its three
  verified shape params, booster checksum, and `failure_stage` tag were added
  without replacing any prior field;
- its already-saved booster at
  `artifacts/sasrec-ranker-guardrail/be8d69130f2a45ee8e690909079d3197/ranker.txt`;
- successful resumed local-MLflow run `8d31c985e5da4879ab1d310a4d006c97`;
- byte-identical booster copies in the successful local artifact and MLflow
  store, SHA-256
  `b010ef156c141545058b5cdf7d37290248802ef1ee74cf96488028c986ffd843`;
- result JSON SHA-256
  `72f3e683b81cb5537ec7c8c89d5bcdb5234326af4eead898d0426bc3d496e0e4`;
- complete logs at
  `artifacts/sasrec/logs/paired-ranker-guardrail-20260905T0425PDT.log` and
  `artifacts/sasrec/logs/paired-ranker-guardrail-resume-20260905T0430PDT.log`;
  and
- the source-controlled raw verdict in
  [`paired-ranker-guardrail-2026-09-05.json`](paired-ranker-guardrail-2026-09-05.json).

The successful MLflow store is retained under
`artifacts/mlflow-sasrec-recovery/`. No run, artifact, log, or failed-attempt
record was overwritten or deleted. Both run records explicitly carry
`source_worktree_dirty=true`, base commit `384864d`, and a source-status tag:
the runner had not yet been committed when executed, and the only subsequent
source change moved the three immutable shape params ahead of artifact
transport so a future failed upload retains them automatically. The modeling,
artifact, evaluation, and gate paths are unchanged.
