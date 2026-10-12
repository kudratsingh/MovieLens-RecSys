# WO-5 sweep — run preservation verified, a restore proved, a plain export

**Written 2026-10-12** for the owner's request of that day,
[D-054](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each). It checks
every run of the WO-5 rented-GPU sweep ([runbook](wo5-sweep-runbook.md), approval D-052) against
non-negotiable 12, proves one model restores from the backup alone, exports that model in a form
that needs nothing from this repository, and lists what a deployment bundle would still need.
Nothing here trains, serves or changes the pod.

## The owner's request, verbatim

> "For each of the seven runs (calibration, 0b, A, E, B, C, D), confirm these are on the Mac and in
> the movielens-backups repo: 1. The model archive with its SHA-256, plus the manifest. 2. The
> movie-id vocabulary the model was trained with, and its hash. 3. The full config: sizes, history
> length, loss, passes run, seed, training objective, encoder implementation. 4. The popularity
> fallback order saved with the run. 5. Per-pass metrics, the per-user result file, and the console
> log. 6. The environment: git commit, torch and CUDA versions, GPU, data hashes, protocol hash.
> Then, for the best cell so far and again for D if it wins: 7. Prove it restores. In a fresh
> checkout, load the model from the backup copy only and reproduce its warm recall@500 on the CPU.
> 8. Export a plain copy for use outside this repo: the weights as a standard PyTorch state dict
> (safetensors if available), the vocabulary as JSON, the config as JSON, and a short README showing
> how to load it and get the top 500 movies for a list of movie ids. 9. List what a deployment
> bundle would still need that does not exist yet (for example the ranker retrained on this model's
> candidates, which is WO-6). Do not build it now. Rules: Terminate the pod only after items 1 to 7
> are verified. Keep each backup file under 100 MB and report the commit hash. Give me one table:
> run, archive hash, in MLflow, in backup, restore tested."

## The table

| Run | Run id | Warm recall@500 | Archive SHA-256 | In MLflow | In backup (harvest commit) | Restore tested |
|---|---|---:|---|---|---|---|
| 01 calibration (v1, seed 42) | `2c02f22332324ffe9364bdf575f18c1b` | 0.507195 | `7bf2eea633b2e0a99fa6431f9ec9abe9640845af56f038a79bf53b60b1945d5f` | yes — re-score `744df0f9…` | yes — `440f9bb` | not required (not the best cell) |
| 02 0b | `f6fb3c074f384ea39d82ba18565d0a0c` | 0.518106 | `e1b4b75f6b7d79d77d5de3e663d7e3e2f1b5f1a7d84c600cd70790016cf2242c` | yes — re-score `b0224133…` | yes — `92ebfba` | not required (not the best cell) |
| 03 A | `ebbfc74a793b465d926c747cf121c0d8` | 0.534694 | `d93e858b606483c2c59edc8450a4d6ea2a4fbde2955e36d6c9a03dd2ac6e9811` | yes — re-score `98b7ecbc…` | yes — `32962c1` | not required (not the best cell) |
| 04 E | `5a038bb4dc774c13bf2945b331e79086` | 0.523779 | `05bff388e94a7b98f6bb5444c506bcefa24d81cf019ccfe3278eec15b2d8b8ae` | yes — re-score `4092f2b4…` | yes — `fc2a2b6` | not required (not the best cell) |
| 05 B | `2e576ee7c17145429928dd6301547065` | 0.534921 | `eacb04e6218f5ca1053b9e48f3feb8ae6b49a1a64ba58be0b4f8ece56c3b7dfb` | yes — re-score `f6465d24…` | yes — `3e7f6c8` | **yes** — exact (0.5349213182633166) |
| 06 C | `a1ba6606fc9c4c7b96332812ddde0f1f` | 0.528159 | `df1de581267d07b566c1811473d9261f8978973e24862399f71cd72b9d2de76d` | yes — re-score `ab83ebf0…` | yes — `25bd674` | not required (not the best cell) |
| 07 D | `80e32acb0916402da3940f0847efef8f` | 0.541320 | `fd7b8458dcd9c754a0519394f637e3b20e191a48f7e35fd1db9198d59ed54f3f` | yes — re-score `cc36c27a…` | yes — `0fd2598` | **yes** — exact (0.5413202121777659) |

"In MLflow" means the training run (original id) **and** its CPU re-score (tagged
`rescored_from`) both resolve in the shared store `http://localhost:5001`, experiment
`phase-a-sasrec` (id 6), status `FINISHED`, and the model archive downloaded through the server has
the SHA-256 in the first column. "In backup" means a fresh `git clone` of
`kudratsingh/movielens-backups` (the remote, not the local working tree) holds the run's bundle, its
`.sha256` sidecar verifies, the bundle's own `MANIFEST.sha256` verifies after unpacking, and the
unpacked tree is byte-identical to the Mac's (`diff -r`). The bundle's SHA-256 is also the one the
pod wrote in its `INDEX`. "Restore tested" is item 7, below.

## Items 1–6, run by run

Every check was run twice, on the Mac's copy (`artifacts/wo5-sweep/<seq>-<cell>/…`) and on the copy
unpacked from the fresh backup clone, by one script (`audit_run.py`, kept in the evidence tarball);
the two outputs are identical for every run. No check failed.

| Run | Shape (width/blocks/history), loss | Passes run | Archive bytes | Popularity SHA-256 | Per-user file SHA-256 | Console log SHA-256 | Trained (UTC) | Bundle SHA-256 | Warm recall@500 per pass |
|---|---|---|---:|---|---|---|---|---|---|
| 01 calibration (v1, seed 42) | 64/2/50, 2 heads, ff 256; bce, 32 neg. | 2 of 2 | 9,462,224 | `9bc82187…` | `fc39a58f…` | `b9cac9ea…` | 10-11T15:00–15:32 | `c1b8bd801235…` | 0.4909, 0.5072 |
| 02 0b | 64/2/50, 2 heads, ff 256; sampled-softmax, 1024 neg. | 3 of 5 (early stop) | 9,462,237 | `9bc82187…` | `919b1b63…` | `a7e805db…` | 10-11T15:33–17:00 | `b7846672e122…` | 0.4939, 0.4992, 0.5181 |
| 03 A | 128/2/100, 4 heads, ff 512; sampled-softmax, 1024 neg. | 5 of 5 | 19,509,983 | `9bc82187…` | `afc69a5b…` | `8815901c…` | 10-11T17:02–20:09 | `a92486e8daed…` | 0.5056, 0.5162, 0.5295, 0.5245, 0.5347 |
| 04 E | 128/2/100, 4 heads, ff 512; full-softmax | 3 of 5 (early stop) | 19,509,980 | `9bc82187…` | `8f1b0a03…` | `c0df779e…` | 10-11T20:10–21:02 | `8a4b4dd633d6…` | 0.4976, 0.5206, 0.5238 |
| 05 B | 256/2/100, 8 heads, ff 1024; sampled-softmax, 1024 neg. | 4 of 5 (early stop) | 41,939,168 | `9bc82187…` | `435323dd…` | `2b1f7b8d…` | 10-11T21:03–00:46 | `56ed00813330…` | 0.511, 0.5111, 0.5249, 0.5349 |
| 06 C | 128/4/100, 4 heads, ff 512; sampled-softmax, 1024 neg. | 4 of 5 (early stop) | 21,107,703 | `9bc82187…` | `872123a4…` | `b0de976b…` | 10-12T00:47–04:08 | `7de7b26341f8…` | 0.4872, 0.5178, 0.5285, 0.5282 |
| 07 D | 128/2/200, 4 heads, ff 512; sampled-softmax, 1024 neg. | 4 of 5 (early stop) | 19,561,183 | `9bc82187…` | `705f74e0…` | `3eebf48e…` | 10-12T04:10–08:28 | `552b866d9070…` | 0.5177, 0.5352, 0.5337, 0.5413 |

Every run: `encoder_impl` `hand-written-transformer-v1`, objective `strict-prefix-final-position-v1`, seed 42, cold-start threshold 10, vocabulary `76f0cf89a8e2be1c3a294d535be3a9edc4ef76ebe3cd599376fc0bb9ae9de8cd` (34,461 movies), bundle `MANIFEST.sha256` of 31 files verified.

**What each item rests on.**

1. **Archive and manifest.** `model/sasrec-model.zip` and `model/sasrec-manifest.json`. The
   archive's SHA-256 equals the manifest's `model_sha256`, `result.json`'s `archive_sha256` and
   `manifest_model_sha256`, the MLflow tag `sasrec_artifact_sha256`, and the copy under the run's
   MLflow `model/` path.
2. **Vocabulary.** It lives **only inside the archive**, as `item_ids` in the archive's
   `metadata.json` (dense index *d* is `item_ids[d − 1]`; 0 is padding; `unknown_index` = 34,462).
   It was extracted and hashed (canonical JSON): the hash equals the manifest's
   `vocabulary_sha256`, the metadata's own field and the MLflow tag `sasrec_vocabulary_sha256`;
   34,461 ids, no duplicates. All seven runs share one vocabulary, `76f0cf89…de8cd`, because all
   fit the same frame. The export (item 8) carries it as `vocabulary.json`.
3. **Config.** The archive's `metadata.json` holds every `SASRecConfig` value, the objective, the
   cold-start threshold and `encoder_impl`; they agree with `result.json`'s config, the manifest and
   the cells file (whose SHA-256 is in `result.json`). Passes actually run are
   `epochs_completed` / `stopped_early`, cross-checked against the per-pass arrays.
4. **Popularity order.** `model/popularity-order.json` (all 34,461 movies), hashed by the manifest
   (`popularity_sha256`) and identical in every run (`9bc82187…6f0`). On the Mac it was also checked
   to equal `PopularityModel().fit(train_frame).ranking` exactly.
5. **Per-pass metrics, per-user file, console log.** `result.json`'s `per_pass` block (warm
   recall@500, probe recall, loss, seconds, RSS per pass — one entry per pass run) and the MLflow
   metric history of the same; `per_user_recall.json` under the run's MLflow artifacts (in the
   bundle and in the store); `logs/console.log` (the trainer's whole console: data load, cohort, one
   line per pass, fit summary, synthetic-cold slices — 1.4 KB, because the trainer logs at that
   rate) plus `logs/gpu-utilization.csv` every 10 s.
6. **Environment.** `result.json` (`git`, `software`, `device`, `host`, `data`, `protocol`) and
   `env/` (`torch.txt`, `nvidia-smi*.txt`, `pip-freeze.txt`, `sweep-facts.env`,
   `ml-25m-input-sha256.json`, `ml-25m.zip.md5`, the sweep plan). Identical across the runs:

   | | |
   |---|---|
   | git commit | `f07060bf38fa030811f7687ffb0bf9eb22ca7e25`, not dirty |
   | torch / CUDA | 2.13.0+cu126 / CUDA 12.6, cuDNN 91002, Python 3.11.13, TF32 matmul off |
   | GPU / driver | NVIDIA GeForce RTX 4090 (Secure Cloud, pod `ydc6tdtu9kmbea`), driver 595.91.07 (driver CUDA 13.2) |
   | data | `ml-25m.zip` MD5 `6b51fb27…2ada` = GroupLens's published MD5; `raw_data_revision` `md5:c3ce6309…021.dir`; derived snapshot `sha256:d9e07381…b22c`; catalogue `sha256:5a1f5114…24b1`; cohort regenerated with the fixed seed, MD5 `9e0c978e…51d7` = its DVC pointer, fingerprint `ae4475f0…cfe2` |
   | protocol | `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28`, `matches_expected: true` |

**Cell D finished after this audit began.** Run 07 trained from 04:10 to 08:28Z on 2026-10-12.
Early stopping ended it after 4 of 5 passes with warm recall@500 0.5413202121777659, the best of the
sweep. Its bundle was harvested, re-scored (Mac CPU equal to the pod, relative difference 0.0),
backed up in `0fd2598` and acknowledged at 08:36Z. The sweep then ended clean (`DONE every planned
step finished clean`): its last bundle, `08-sweeplog-norun`, was backed up in `3d602b0`, and the
harvester removed its own LaunchAgent. D was audited the same way as the other six runs, after it
was home.

## What was missing, and what was done about it

- **The CPU re-scores' artifacts were not in the backup.** Each re-score run's `per_user_recall.json`
  and `rescore.json` lived only in the MLflow artifact volume, and the harvester's `rescore.json` /
  `rescore.log` only under `artifacts/wo5-sweep/` on the Mac. The `mlflow` dumps hold the runs'
  params, metrics and tags but no artifact bytes. They were added as one supplement per run,
  `wo5-sweep-<seq>-<cell>-<run id>-supplement.tgz` (about 40 KB each, with a `.sha256` beside it, a
  `MANIFEST.sha256` inside, and secret-scanned): runs 01–06 in backup commit
  `9859f4e4a8e6669f2e1bc87ccce1ca3f6bc1fe5c`, run 07 in `c90043da94d0f49a1c1eda45db02d4dff0077db8`.
- **The measured input SHA-256s were outside the run bundles until the sweep ended.** The pod
  checked `ratings.csv` and `movies.csv` against the committed manifest once, at setup
  (`setup/logs/data.log`, both `matches_manifest: true`). Each run bundle carries the expected
  hashes, the zip MD5s and the protocol's derived-snapshot hash, which is computed from the bytes
  actually read. The measured log itself arrived in `08-sweeplog-norun` (backup `3d602b0`), and was
  checked in the fresh clone: `logs/data.log` holds both SHA-256s with `matches_manifest: true`,
  beside the setup, cohort, smoke and sweep logs and the final `INDEX`. Nothing needed adding.
- Nothing else was missing. Every file the request names was present on the Mac, in the backup and
  in the store for all seven runs.

## Item 7 — the restore

Done twice. First for cell **B** (run `2e576ee7…`, warm recall@500 0.5349213182633166), the best cell
when this audit started. Then for cell **D** (run `80e32acb…`, 0.5413202121777659), because D beat B.
The method was the same each time, run serially, with nothing else training on the Mac.

**Setup, all from scratch:**

```sh
# the backup, from the remote (not ~/movielens-backups)
git clone --depth 1 https://github.com/kudratsingh/movielens-backups.git backup-clone
shasum -a 256 -c backup-clone/wo5-sweep-*.tgz.sha256            # every bundle: OK
# the code, a fresh checkout at the sweep's commit
git init restore-clone && cd restore-clone
git fetch --depth 1 https://github.com/kudratsingh/MovieLens-RecSys.git f07060bf38fa030811f7687ffb0bf9eb22ca7e25
git checkout FETCH_HEAD
# the public MovieLens CSVs (not in the backup), checked against the committed manifest
shasum -a 256 <ml-25m>/{ratings,movies}.csv   # = docs/experiments/sasrec/ml-25m-input-sha256.json
# ADR 0011's cohort, regenerated as the pod did (the parquet is DVC-tracked, so a clone lacks it)
PYTHONPATH=. python -m synthetic.cold_start.generator --ratings-csv <ml-25m>/ratings.csv \
    --out data/synthetic/cold_start/v1/users.parquet     # MD5 9e0c978e… = the DVC pointer
# restore + CPU re-score with the repo's own path (src.training.wo5_harvest.rescore: load_sasrec,
# evaluation_frames, the protocol builder, evaluate), logging only to a throwaway sqlite store
OMP_NUM_THREADS=1 PYTHONPATH=. python restore_test.py backup-clone 05-B-2e576ee7c17145429928dd6301547065 <work> <ml-25m>
OMP_NUM_THREADS=1 PYTHONPATH=. python restore_test.py backup-clone 07-D-80e32acb0916402da3940f0847efef8f <work> <ml-25m>
```

Before scoring, `restore_test.py` did the following:

- printed `code under test: …/restore-clone/src/__init__.py`, which is the fresh clone, not the main
  checkout;
- reassembled nothing, because no file was split;
- verified the bundle's sidecar and its internal manifest;
- loaded the archive through `load_sasrec`, which re-checks its SHA-256, vocabulary, config, tensor
  names, shapes and dtypes.

It then scored each model on the CPU:

| | B recorded (RTX 4090) | B reproduced | D recorded (RTX 4090) | D reproduced |
|---|---|---|---|---|
| protocol hash | `sha256:b4ed5afa…0fb28` | equal | `sha256:b4ed5afa…0fb28` | equal |
| warm recall@500 (1,931 users) | 0.5349213182633166 | **0.5349213182633166** | 0.5413202121777659 | **0.5413202121777659** |
| cold recall@500 (710 users) | 0.5262729520330651 | **0.5262729520330651** | 0.5262729520330651 | **0.5262729520330651** |
| overall recall@500 | 0.5325963125747597 | 0.5325963125747597 | 0.5372749434527611 | 0.5372749434527611 |
| warm NDCG@500 | 0.1986015935630282 | 0.19860170287774997 | 0.20385707222473562 | 0.20385708679382983 |
| per-user recall vectors | | identical, every user | | identical, every user |
| time | | 43 s | | 41 s |

Recall is exact because the set of 500 movies does not change between the GPU and the CPU. NDCG
moves in the seventh decimal because the order of a few near-tied candidates does (float rounding).
The harvester's re-score of record shows the same difference.

## Item 8 — the plain export

There are two exports, built the same way from the archives restored out of the backup clone,
through `load_sasrec`:

| export | Mac | backup (commit) | `state_dict.pt` SHA-256 |
|---|---|---|---|
| **D** (the best cell, history 200) | `artifacts/wo5-sweep/export/D-80e32acb0916402da3940f0847efef8f/` | `wo5-sweep-export-D-80e32acb0916402da3940f0847efef8f.tgz` (18.1 MB, `c90043d…`) | `2bab051b…91de` |
| B (best before D, history 100) | `artifacts/wo5-sweep/export/B-2e576ee7c17145429928dd6301547065/` | `wo5-sweep-export-B-2e576ee7c17145429928dd6301547065.tgz` (39.0 MB, `9859f4e…`) | `219c0fb2…6bc4` |

| file | what it is |
|---|---|
| `state_dict.pt` | The encoder weights, a plain `dict[str, Tensor]` (float32). Loads with `torch.load(..., weights_only=True)`. It is not safetensors because the package is not installed in the main venv (`ModuleNotFoundError`), and adding it was out of scope; the README gives the one-line conversion |
| `vocabulary.json` | `movie_ids` (dense index *d* ↔ `movie_ids[d − 1]`), `padding_index` 0, `unknown_index`, its hash |
| `config.json` | Every hyperparameter needed to rebuild the hand-written encoder (width, blocks, heads, feed-forward, history length, LayerNorm eps, activation, norm placement, padding and order contract, cold-start threshold). Also the full training config, `encoder_impl`, objective, passes, the source run, archive and weight hashes, commit, protocol hash and metrics |
| `popularity-order.json` | The fallback order, byte-identical to the run's |
| `load_example.py` | Depends on torch only. Carries a minimal inference-only copy of the encoder and `SASRecRetriever.recommend(movie_ids, k=500)` |
| `README.md`, `SHA256SUMS` | How to load and query it. The history contract: oldest → newest, the last 200 (D) or 100 (B) encoded, left-padded with 0, unknown ids → one token, every movie in the history excluded from the results, fewer than 10 → popularity fallback. Also its metrics and the verification below |

**Verified from a clean shell** (`env -i`, cwd a copy of the export, no `PYTHONPATH`). The test set
was six real warm users and one popularity-routed user (5 interactions). The warm users' history
lengths were 11, 12, 86, 98, 457 and 546, so truncation is exercised for both history lengths. For
each export, `load_example.py` returned **exactly** the repo's evaluation-path top 500
(`SASRecModel.recommend`, torch exact index) for all seven users.

Against the repo's serving path (`recommend_from_history`, FAISS exact inner product) it returned
the same 500 movies for every warm user. For D the order was identical too. For B, two near-tied
movies swap places for one user.

## Item 9 — what a deployment bundle would still need

None of this exists for the WO-5 models, and none of it was built. Serving is parked (D-022, D3).
Cell D is the candidate. Its single seed-42 run is the best of the sweep, not a promoted model.

1. **A ranker trained on this retriever's candidates** (WO-6). Run `make train-sasrec-ranker` and
   `make train-sasrec-ranker-bundles` on D's 500 candidates, with ADR 0018's encoder features. Then
   run `make gate` on the learned route against bundle `c1d742c8…` (warm NDCG@10 0.101441; a pass
   needs +3 % warm with cold unchanged). This is D-002's end-to-end guardrail.
2. **A retrieval verdict that can promote.** D-001's gate reads a three-seed mean, and these are
   single seed-42 runs. WO-7's seeds 7 and 13 and the rolling windows w1/w2 are separate approvals
   (D-052, condition 6). The sealed-window read (WO-8) comes after.
3. **A schema-2 served bundle.** This is a serving manifest that pins the encoder archive, its
   manifest, the popularity order and the new ranker by SHA-256 under their roles, published and
   checked by the loader. The committed `infra/model-bundle/` is still the schema-1 item-item demo
   pair.
4. **Feast features for the new candidate set.** The online store must cover every movie this
   retriever can return (34,461 movies, compared with the item-item index's set), and the
   feature-parity test has to run on the new bundle. ADR 0018's encoder-derived features come from
   the sidecar, not Feast, and need their own parity check.
5. **A sidecar support check for this encoder's shape.** The sidecar's SASRec path
   (`src/serving/sequence_retrieval.py`) has never served a history-200 encoder (D: width 128,
   2 blocks, 4 heads, 200 positions). It has to load the archive, pass its declared-parameter and
   finite-encoder checks, warm up, and fit the image's memory. The encoder's CPU latency also needs
   measuring at 200 positions with `src/evaluation/sasrec_latency.py` (WO-6; reported, not gated,
   under D3; D-016's 15 ms budget stands).
6. **The k6 latency gate on this bundle**: p99 < 100 ms under the pinned load (ADR 0010). It has
   never been run on a SASRec bundle.
7. **The champion swap**: the per-tenant champion columns and ADR 0001's promotion gate. It is
   parked with serving (D-022).

## Where everything lives

| What | Where |
|---|---|
| Run bundles (one per run) | Backup: `wo5-sweep-<seq>-<cell>-<run id>.tgz` (+ `.sha256`), commits listed in the table. Mac: `artifacts/wo5-sweep/bundles/`, unpacked under `artifacts/wo5-sweep/<seq>-<cell>/`. Pod: `/workspace/wo5-sweep/out/` |
| Sweep-level logs | backup `wo5-sweep-08-sweeplog-norun.tgz`, commit `3d602b0fd8a0f6d1ae1f51dbdea18f30df2a51cb` |
| MLflow dumps after each import | backup `mlflow-post-wo5-sweep-<seq>-<cell>-*.sql.gz` (same commits as the bundles) |
| Re-score supplements, exports, audit and restore evidence | Backup commit `9859f4e4a8e6669f2e1bc87ccce1ca3f6bc1fe5c` holds runs 01–06's supplements, B's export and `wo5-sweep-preservation-20261012-B.tgz` (the audit JSON, the restore outputs, the scripts). Backup commit `c90043da94d0f49a1c1eda45db02d4dff0077db8` holds run 07's supplement, D's export and `wo5-sweep-preservation-20261012-D.tgz` |
| Shared store | `http://localhost:5001`, experiment `phase-a-sasrec` (id 6) |

Every file in the backup is under 100 MB (the largest is cell B's bundle, 88.5 MB), so nothing is
split. A fresh clone of the backup at `c90043d` verified 27 of 27 `.sha256` sidecars: eight run
bundles, eight `mlflow` dumps, seven supplements, two exports and two evidence tarballs.
