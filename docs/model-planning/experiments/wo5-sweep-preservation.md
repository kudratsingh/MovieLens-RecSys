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

<!-- TABLE -->
| Run | Run id | Warm recall@500 | Archive SHA-256 | In MLflow | In backup (harvest commit) | Restore tested |
|---|---|---:|---|---|---|---|
| 01 calibration (v1, seed 42) | `2c02f22332324ffe9364bdf575f18c1b` | 0.507195 | `7bf2eea633b2e0a99fa6431f9ec9abe9640845af56f038a79bf53b60b1945d5f` | yes — re-score `744df0f9…` | yes — `440f9bb` | not required (not the best cell) |
| 02 0b | `f6fb3c074f384ea39d82ba18565d0a0c` | 0.518106 | `e1b4b75f6b7d79d77d5de3e663d7e3e2f1b5f1a7d84c600cd70790016cf2242c` | yes — re-score `b0224133…` | yes — `92ebfba` | not required (not the best cell) |
| 03 A | `ebbfc74a793b465d926c747cf121c0d8` | 0.534694 | `d93e858b606483c2c59edc8450a4d6ea2a4fbde2955e36d6c9a03dd2ac6e9811` | yes — re-score `98b7ecbc…` | yes — `32962c1` | not required (not the best cell) |
| 04 E | `5a038bb4dc774c13bf2945b331e79086` | 0.523779 | `05bff388e94a7b98f6bb5444c506bcefa24d81cf019ccfe3278eec15b2d8b8ae` | yes — re-score `4092f2b4…` | yes — `fc2a2b6` | not required (not the best cell) |
| 05 B | `2e576ee7c17145429928dd6301547065` | 0.534921 | `eacb04e6218f5ca1053b9e48f3feb8ae6b49a1a64ba58be0b4f8ece56c3b7dfb` | yes — re-score `f6465d24…` | yes — `3e7f6c8` | **yes** — exact (0.5349213182633166) |
| 06 C | `a1ba6606fc9c4c7b96332812ddde0f1f` | 0.528159 | `df1de581267d07b566c1811473d9261f8978973e24862399f71cd72b9d2de76d` | yes — re-score `ab83ebf0…` | yes — `25bd674` | not required (not the best cell) |
| 07 D | D — still training on the pod at the time of writing; see below | | | | | |

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

<!-- PERRUN -->
| Run | Shape (width/blocks/history), loss | Passes run | Archive bytes | Popularity SHA-256 | Per-user file SHA-256 | Console log SHA-256 | Trained (UTC) | Bundle SHA-256 | Warm recall@500 per pass |
|---|---|---|---:|---|---|---|---|---|---|
| 01 calibration (v1, seed 42) | 64/2/50, 2 heads, ff 256; bce, 32 neg. | 2 of 2 | 9,462,224 | `9bc82187…` | `fc39a58f…` | `b9cac9ea…` | 10-11T15:00–15:32 | `c1b8bd801235…` | 0.4909, 0.5072 |
| 02 0b | 64/2/50, 2 heads, ff 256; sampled-softmax, 1024 neg. | 3 of 5 (early stop) | 9,462,237 | `9bc82187…` | `919b1b63…` | `a7e805db…` | 10-11T15:33–17:00 | `b7846672e122…` | 0.4939, 0.4992, 0.5181 |
| 03 A | 128/2/100, 4 heads, ff 512; sampled-softmax, 1024 neg. | 5 of 5 | 19,509,983 | `9bc82187…` | `afc69a5b…` | `8815901c…` | 10-11T17:02–20:09 | `a92486e8daed…` | 0.5056, 0.5162, 0.5295, 0.5245, 0.5347 |
| 04 E | 128/2/100, 4 heads, ff 512; full-softmax | 3 of 5 (early stop) | 19,509,980 | `9bc82187…` | `8f1b0a03…` | `c0df779e…` | 10-11T20:10–21:02 | `8a4b4dd633d6…` | 0.4976, 0.5206, 0.5238 |
| 05 B | 256/2/100, 8 heads, ff 1024; sampled-softmax, 1024 neg. | 4 of 5 (early stop) | 41,939,168 | `9bc82187…` | `435323dd…` | `2b1f7b8d…` | 10-11T21:03–00:46 | `56ed00813330…` | 0.511, 0.5111, 0.5249, 0.5349 |
| 06 C | 128/4/100, 4 heads, ff 512; sampled-softmax, 1024 neg. | 4 of 5 (early stop) | 21,107,703 | `9bc82187…` | `872123a4…` | `b0de976b…` | 10-12T00:47–04:08 | `7de7b26341f8…` | 0.4872, 0.5178, 0.5285, 0.5282 |

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

<!-- DSECTION -->
**Run 07, cell D (128/2/200)** was still training on the pod when this record was written (started
2026-10-12T04:09Z, projected 7.25 h at its 5-pass ceiling). Its items 1–7 are **pending** until its
bundle is home; the pod must not be terminated before then.
<!-- /DSECTION -->

## What was missing, and what was done about it

- **The CPU re-scores' artifacts were not in the backup.** Each re-score run's `per_user_recall.json`
  and `rescore.json` lived only in the MLflow artifact volume, and the harvester's `rescore.json` /
  `rescore.log` only under `artifacts/wo5-sweep/` on the Mac; the `mlflow` dumps hold the runs'
  params, metrics and tags but no artifact bytes. Added as one supplement per run,
  `wo5-sweep-<seq>-<cell>-<run id>-supplement.tgz` (about 40 KB each, `.sha256` beside it, a
  `MANIFEST.sha256` inside, secret-scanned), backup commit
  `9859f4e4a8e6669f2e1bc87ccce1ca3f6bc1fe5c`.
- **The measured input SHA-256s sit outside the run bundles.** The pod checked `ratings.csv` and
  `movies.csv` against the committed manifest once, at setup (`setup/logs/data.log`, both
  `matches_manifest: true`); each run bundle carries the expected hashes, the zip MD5s and the
  protocol's derived-snapshot hash, which is computed from the bytes actually read. The setup logs
  come home in the sweep's last bundle, `08-sweeplog-norun`, when the sweep ends.
  <!-- SWEEPLOG -->
- Nothing else was missing: every file the request names was present on the Mac, in the backup and
  in the store for every run checked.

## Item 7 — the restore

Done for cell **B** (run `2e576ee7…`, warm recall@500 0.5349213182633166, the best cell when this
was written). <!-- DRESTORE -->

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
shasum -a 256 data/raw/ml-25m/{ratings,movies}.csv   # = docs/experiments/sasrec/ml-25m-input-sha256.json
# ADR 0011's cohort, regenerated as the pod did (the parquet is DVC-tracked, so a clone lacks it)
PYTHONPATH=. python -m synthetic.cold_start.generator --ratings-csv <ml-25m>/ratings.csv \
    --out data/synthetic/cold_start/v1/users.parquet     # MD5 9e0c978e… = the DVC pointer
# restore + CPU re-score with the repo's own path (src.training.wo5_harvest.rescore: load_sasrec,
# evaluation_frames, the protocol builder, evaluate), logging only to a throwaway sqlite store
OMP_NUM_THREADS=1 PYTHONPATH=. python restore_test.py backup-clone 05-B-2e576ee7c17145429928dd6301547065 <work> <ml-25m>
```

`restore_test.py` printed `code under test: …/restore-clone/src/__init__.py` (the fresh clone, not the
main checkout), reassembled nothing (no file was split), verified the bundle's sidecar and internal
manifest, then loaded the archive through `load_sasrec` (which re-checks its SHA-256, vocabulary,
config, tensor names, shapes and dtypes) and scored it on the CPU:

| | recorded on the pod (RTX 4090) | reproduced (Mac CPU, fresh clone, backup copy) |
|---|---|---|
| protocol hash | `sha256:b4ed5afa…0fb28` | `sha256:b4ed5afa…0fb28` |
| warm recall@500 (1,931 users) | 0.5349213182633166 | **0.5349213182633166** (exact) |
| cold recall@500 (710 users) | 0.5262729520330651 | **0.5262729520330651** (exact) |
| overall recall@500 | 0.5325963125747597 | 0.5325963125747597 (exact) |
| warm NDCG@500 | 0.1986015935630282 | 0.19860170287774997 (+1.1 × 10⁻⁷) |
| per-user recall vectors | `per_user_recall.json` | identical, every user |

Recall is exact because the set of 500 does not change between the GPU and the CPU; NDCG moves in
the seventh decimal because the order of a few near-tied candidates does (float rounding), which is
the same difference the harvester's re-score of record already shows. The job took 43 s.

<!-- DRESTORE_TABLE -->

## Item 8 — the plain export

`artifacts/wo5-sweep/export/B-2e576ee7c17145429928dd6301547065/` on the Mac, and
`wo5-sweep-export-B-2e576ee7c17145429928dd6301547065.tgz` in the backup. Built from the archive
restored out of the backup clone, through `load_sasrec`:

| file | what it is |
|---|---|
| `state_dict.pt` | the encoder weights, a plain `dict[str, Tensor]` (float32), loads with `torch.load(..., weights_only=True)`. Not safetensors: the package is not installed in the main venv (`ModuleNotFoundError`), and adding it was out of scope; the README gives the one-line conversion |
| `vocabulary.json` | `movie_ids` (dense index *d* ↔ `movie_ids[d − 1]`), `padding_index` 0, `unknown_index`, its hash |
| `config.json` | every hyperparameter needed to rebuild the hand-written encoder (width, blocks, heads, feed-forward, history length, LayerNorm eps, activation, norm placement, padding and order contract, cold-start threshold), the full training config, `encoder_impl`, objective, passes, the source run, archive and weight hashes, commit, protocol hash and metrics |
| `popularity-order.json` | the fallback order, byte-identical to the run's |
| `load_example.py` | depends on torch only; carries a minimal inference-only copy of the encoder and `SASRecRetriever.recommend(movie_ids, k=500)` |
| `README.md`, `SHA256SUMS` | how to load and query it; the history contract (oldest → newest, the last 100 encoded, left-padded with 0, unknown ids → one token, every movie in the history excluded from the results, fewer than 10 → popularity fallback); its metrics; the verification below |

**Verified from a clean shell** (`env -i`, cwd a copy of the export, no `PYTHONPATH`): for six real
warm users (history lengths 11, 12, 86, 98, 457 and 546 — so truncation to 100 is exercised) and
one popularity-routed user (5 interactions), `load_example.py` returned **exactly** the repo's
evaluation-path top 500 (`SASRecModel.recommend`, torch exact index). Against the repo's serving
path (`recommend_from_history`, FAISS exact inner product) it returned the same 500 movies for all
six; for one user two near-tied movies swap places.

<!-- DEXPORT -->

## Item 9 — what a deployment bundle would still need

None of this exists for the WO-5 models, and none of it was built. Serving is parked (D-022, D3).

1. **A ranker trained on this retriever's candidates** — WO-6: `make train-sasrec-ranker` and
   `make train-sasrec-ranker-bundles` on the winner's 500 candidates (with ADR 0018's encoder
   features), then `make gate` on the learned route against bundle `c1d742c8…` (warm NDCG@10
   0.101441; a pass needs +3 % warm with cold unchanged), D-002's end-to-end guardrail.
2. **A retrieval verdict that can promote** — D-001's gate reads a three-seed mean; these are single
   seed-42 runs. WO-7's seeds 7 and 13 and the rolling windows w1/w2 are separate approvals (D-052,
   condition 6); the sealed-window read (WO-8) comes after.
3. **A schema-2 served bundle** — a serving manifest that pins the encoder archive, its manifest, the
   popularity order and the new ranker by SHA-256 under their roles, published and checked by the
   loader; the committed `infra/model-bundle/` is still the schema-1 item-item demo pair.
4. **Feast features for the new candidate set** — the online store must cover every movie this
   retriever can return (34,461, against the item-item index's set), with the feature-parity test run
   on the new bundle; ADR 0018's encoder-derived features come from the sidecar, not Feast, and need
   their own parity check.
5. **A sidecar support check for this encoder's shape** — the sidecar's SASRec path
   (`src/serving/sequence_retrieval.py`) has to load a width-256, 8-head, history-100 archive
   (history-200 if D wins), pass its declared-parameter and finite-encoder checks, warm up, and fit
   the image's memory; and the encoder's CPU latency measured with `src/evaluation/sasrec_latency.py`
   (WO-6; reported, not gated, under D3; D-016's 15 ms budget stands).
6. **The k6 latency gate on this bundle** — p99 < 100 ms under the pinned load (ADR 0010), never run
   on a SASRec bundle.
7. **The champion swap** — per-tenant champion columns and ADR 0001's promotion gate. Parked with
   serving (D-022).

## Where everything lives

| What | Where |
|---|---|
| Run bundles (one per run) | backup `wo5-sweep-<seq>-<cell>-<run id>.tgz` (+ `.sha256`), commits listed in the table; Mac `artifacts/wo5-sweep/bundles/` and unpacked under `artifacts/wo5-sweep/<seq>-<cell>/`; pod `/workspace/wo5-sweep/out/` |
| MLflow dumps after each import | backup `mlflow-post-wo5-sweep-<seq>-<cell>-*.sql.gz` (same commits) |
| Re-score supplements, B export, audit and restore evidence | backup commit `9859f4e4a8e6669f2e1bc87ccce1ca3f6bc1fe5c`: `wo5-sweep-*-supplement.tgz`, `wo5-sweep-export-B-2e576ee7….tgz`, `wo5-sweep-preservation-20261012-B.tgz` (the audit JSON, the restore outputs, the scripts) |
| Shared store | `http://localhost:5001`, experiment `phase-a-sasrec` (id 6) |
