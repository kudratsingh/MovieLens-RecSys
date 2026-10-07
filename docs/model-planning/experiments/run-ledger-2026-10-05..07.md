# Run ledger, 5–7 October 2026

**Written 2026-10-07** for the catch-up the owner ordered with the two standing rules in
[`CLAUDE.md`](../../../CLAUDE.md) (non-negotiables 12, run preservation, and 13, decision record).
It lists every run since 2026-10-05, whether it is in the one shared MLflow store, and which commit
of the private `kudratsingh/movielens-backups` repo holds its files. A run whose store was lost
cannot be re-created with its original id through the MLflow API, so none was: those rows say
`lost (metadata)` and point at what survives.

## Where the run data lives

- **Shared store.** `http://localhost:5001` (MLflow 2.13 on the `movielens-coldstart` Compose
  project's Postgres). Every Phase A run up to 2026-10-05 is in experiment `phase-2-candidates`
  (id 1); runs from 2026-10-07 log straight to `phase-a-sasrec` (id 6). Artifact bytes are in the
  Docker volume `movielens-coldstart_mlflow_artifacts`, at `<experiment id>/<run id>/artifacts/`.
- **Backup repo** `kudratsingh/movielens-backups` (private). The commits for this catch-up:

  | Commit | What it adds |
  |---|---|
  | `323af002ae019777764536cd922b2d4ca6f53c9e` | `mlflow-pre-catchup-20261007T022521.sql.gz`, the store before the import (111 runs) |
  | `b6f92ac418f58600aa60fb56227c86495e66ceb4` | the five bundles `wo1-runs-2026-10-05.tgz`, `wo2-runs-2026-10-05.tgz`, `wo3-runs-2026-10-05-to-07.tgz`, `wo4-runs-2026-10-05.tgz`, `contamination-gbce-run-2026-10-05.tgz`, each with a `.sha256` sidecar and a `MANIFEST.sha256` inside, and `catch-up-2026-10-07.md` describing them |
  | `531f62ec7e8b297b8ae1abcd04ca758e48064a7a` | `mlflow-post-catchup-20261007T023906.sql.gz`, the store after the import, the two terminations and the 10-pass run's finish (119 runs); and `wo3-10pass-run-2026-10-07.tgz`, a read-only export of that run |

  The 2026-10-05 commits before them: `e4a7adf` (first `mlflow` dump), `d9936d9` (the September
  stores, `september-mlruns-artifacts.tgz`), `fe9dc20` and `ffe0479` (dumps before and after the
  September import, the second taken before `movielens` was dropped). Every file is under 100 MB, so
  nothing is split.
- **Working copies** in the main checkout, git-ignored: `artifacts/sasrec/` (WO-1 archives and logs),
  `artifacts/wo2-converter/`, `artifacts/wo2-pilot/`, `artifacts/wo3/`, and
  `artifacts/local-mlruns/mlruns-{wo3,wo4,contam}/` (copies of the worktree stores, taken before those
  worktrees were removed).

## What the catch-up did

1. **Dump first.** A fresh `pg_dump` of `mlflow` was pushed (`323af00`) before anything was written.
2. **Import, original ids.** The import tool's dry run planned 8 runs from the four surviving stores
   and blocked none; the execute step then inserted all 8 into experiment 1 and copied their 33
   artifact files (52.9 MiB) into the volume, checking every file's SHA-256 there:
   - 1 from the WO-2 converter's SQLite store (`artifacts/wo2-converter/mlflow.db`);
   - 3 from the WO-3 store, 3 from the WO-4 store, 1 from the gBCE store.
   - Rows: 8 runs, 381 params, 149 tags, 69,768 metric points (WO-4 logs every step), 162 latest
     metrics. Each run carries `imported_from` naming its store and `import_date=2026-10-07`.
   - The tool reads file stores; it was extended for this import to read a SQLite store and to take
     explicit store paths and a per-store `imported_from` label.
3. **Verify through the REST API.** All 8 pass: run info, params, latest metrics, tags,
   metric-history row counts and the artifact listing equal the source store, and each run's
   `per_user_recall.json` downloads with the source's SHA-256. The five WO-1 runs imported on
   2026-10-05 were re-verified against their local store the same way: 5 of 5 pass.
4. **Two September runs left running forever** were ended with `MlflowClient.set_terminated(...,
   status="KILLED")` and tagged `terminated_by=catch-up-2026-10-07`: `982b5cdb903f4c54ba1831aa9cc59a1e`
   (an empty ranker run in `phase-2-ranker`) and `ece5fca0926144a78a739c0c1a59cdaa` (an empty probe in
   `phase-2-ranker-probe`). Both logged nothing; only their status and end time changed.
5. **Bundles and a second dump**, pushed as listed above. Every text member of every bundle was
   searched for `password|secret|token|api_key`: no hits (the SQLite store's only matches are empty
   MLflow schema tables).

Not touched, because they are outside this window and nobody asked: three two-tower runs from
2026-08-29 still show `RUNNING` (`43fd9ddc…`, `5094b121…`, `2f6521f0…`).

## The ledger

"Backup" is the commit holding the run's files. For a run in the shared store its metadata is also in
the post-catch-up dump, `531f62e`.

| Run id | Work order | What | In shared store | In backup | Notes |
|---|---|---|---|---|---|
| `3e031fa2046b42b4a1f50fc6e91fcc71` | WO-1 | control: the old loop at `89520be^` plus the O-25 patch, seed 42 | yes (exp 1, imported 2026-10-05) | `b6f92ac` (`wo1-runs-2026-10-05.tgz`) | archive `9213b61f…` |
| `7baeb7d0b812486e9c86fdd3435ea715` | WO-1 | restored loop, seed 42 | yes (exp 1, imported 2026-10-05) | `b6f92ac` | archive `b63a6c5a…`; weights equal the control's |
| `d71f0fa6321d4fd6bf3288ee9c28e710` | WO-1 | restored loop, seed 7 | yes (exp 1, imported 2026-10-05) | `b6f92ac` | archive `9fd24442…` |
| `2d9f3cc1ed1943b495fbecc7ac101485` | WO-1 | restored loop, seed 13 | yes (exp 1, imported 2026-10-05) | `b6f92ac` | archive `4e9cfbb1…` |
| `8668ca0c57f6411dacd0d53af0b8b2fa` | WO-1 | restored loop, seed 21 | yes (exp 1, imported 2026-10-05) | `b6f92ac` | archive `758dbd73…` |
| `a2c3f5ac09064114b24d70897d68526c` | WO-2 | item 3: converter recheck of v1's saved model (record) | yes (exp 1, imported 2026-10-07 from the SQLite store) | `b6f92ac` (`wo2-runs-2026-10-05.tgz`) | evidence and top-500 lists kept beside it |
| `7c1d3377de164914bb5758a6c7fb9527` | WO-2 | item 3, first attempt: every metric logged, then failed on its first artifact | yes (exp 1, logged directly, `FAILED`) | metadata only, in the dumps | metrics equal the record; tagged `failure_cause` |
| `38442d1a08dd42f3868c1f6147a56fd8` | WO-2 | pre-fix pilot, seed 42 (superseded) | lost (metadata) | `b6f92ac`: archive and console log | store died with the WO-2 worktree; per-user file lost; numbers in the WO-2 record |
| `d0b596f7171a4623b22b6b9e774ade3f` | WO-2 | post-fix pilot, seed 42 | lost (metadata) | `b6f92ac`: archive and console log | as above |
| `2d800544a27b4a67bc6f844a91ad0efc` | WO-2 | post-fix pilot, seed 7 | lost (metadata) | `b6f92ac`: archive and console log | as above |
| `76a1cd9e13d541958e564bcaea49c414` | WO-2 | post-fix pilot, seed 13 | lost (metadata) | `b6f92ac`: archive and console log | as above |
| `29ad5b791ebe48bdbf6e23de51966bfa` | WO-2 | post-fix pilot, seed 21 | lost (metadata) | `b6f92ac`: archive and console log | as above |
| `4f87185e72b841ce95a924bdd6cf5141` | contamination (D-035) | gBCE re-run on O-25's protocol, seed 42 | yes (exp 1, imported 2026-10-07) | `b6f92ac` (`contamination-gbce-run-2026-10-05.tgz`) | archive `5820fa7a…`; console log lost with its worktree |
| `5911fe7fbcbc477c85261d2ec6531987` | WO-3 | P1: all-positions baseline, 2 passes | yes (exp 1, imported 2026-10-07) | `b6f92ac` (`wo3-runs-2026-10-05-to-07.tgz`) | archive `0fc56a05…` |
| `95003b498d7f41459f5f2b4ec6f4dfbc` | WO-3 | P2: overlapping windows, stride 25 | yes (exp 1, imported 2026-10-07) | `b6f92ac` | archive `61980a4d…` |
| `4929b4942d7645c38495c27f943b5d18` | WO-3 | P3: 512 window visits per step | yes (exp 1, imported 2026-10-07) | `b6f92ac` | archive `926a0a71…` |
| `001adb45a47549278680ccfd5658fe10` | WO-3 | P4: 8 passes, read at pass 4 and 8 | lost (metadata) | `b6f92ac`: archive, console log, run summary `p4.json` | store died with the WO-3 worktree; per-user file lost; archive `e759f6a1…` |
| `1fbde8732ab540cf85cd672744acd9e7` | WO-3 | cell 0b on the all-positions trainer, early stopping | lost (metadata) | `b6f92ac`: archive, console log, run summary `b0.json` | as above; archive `a0f79daa…` |
| `02060962064d41fd95e37c8941a596c5` | WO-3 | cell 0b on the all-positions trainer, 10 fixed passes, early stopping off (D-048) | yes (exp 6 `phase-a-sasrec`, logged directly; FINISHED 2026-10-07 09:37 UTC) | `531f62e` (`wo3-10pass-run-2026-10-07.tgz`) | logs its config and console log as artifacts; archive `3157a363…`; its record lands with that run |
| `bd1b04082e5b4c1db354d5df1b3cdcd0` | WO-4 | cell 0b on the CPU (pre-fix encoder) | yes (exp 1, imported 2026-10-07) | `b6f92ac` (`wo4-runs-2026-10-05.tgz`) | archive `8d7eece9…`; console log lost with the WO-4 worktree |
| `9b0d499430474fde9de6a5305bcda649` | WO-4 | device pair, CPU half | yes (exp 1, imported 2026-10-07) | `b6f92ac` | archive `b0ac2c57…`; console log lost |
| `faeb03a68e514941b8d37d1f74574318` | WO-4 | device pair, `mps` half — **not a result of record** | yes (exp 1, imported 2026-10-07) | `b6f92ac` | archive `4da1a7d7…`; console log lost |
| none (by design) | WO-4 / WO-5 prep | cell A timing, 2,000 steps on the CPU | no MLflow run: a timing script that logs nothing | `b6f92ac`: cells JSON with the result block, the script | the script's own JSON output was lost with the WO-4 worktree; numbers in [`wo5-cell-repricing.md`](wo5-cell-repricing.md) |
| none (by design) | WO-4 / WO-5 prep | cell A timing, 2,000 steps on `mps` — **not a result of record** | no MLflow run | `b6f92ac`: as above | as above |
| `131b0437acd64fb29a4596a18defa464` | none | `artifact-upload-probe` in `ops-probe`: a check that the recreated server accepts artifact uploads, not a model run | yes (exp 4, logged directly) | metadata only, in the dumps | — |

Two attempts never became runs and are listed for completeness: WO-1's first control launch, refused at
`start_run` by MLflow's file store (its empty store is in the WO-1 bundle), and WO-2's file-store
attempt `6916d221…`, refused at run creation.

## What run preservation cannot recover

Rule 1 asks for the run id, config, commit, seed, data and protocol hashes, device, start and end
time, every metric, per-pass numbers, the per-user file, the archive with its SHA-256 and the console
log. Against that list:

| Runs | Missing | Where the numbers survive |
|---|---|---|
| WO-2's five pilots | the MLflow store (params, metrics, tags, times as logged) and the per-user files | the WO-2 record, `docs/results.md`, the `wo2-*` cells JSONs, the console logs |
| WO-3 P4 and cell 0b | the MLflow store and the per-user files | the WO-3 record, `docs/results.md`, the `wo3-*` cells JSONs, `p4.json` and `b0.json` (read from the store before it was lost), the console logs |
| WO-4's three runs, the gBCE run | the console logs | the runs themselves, now in the shared store with every metric and per-step history |
| The two cell-A timings | the timing script's JSON output | the result block of `wo5-prep-cellA-step-timing-full.json` and `wo5-cell-repricing.md` |

Every other item on the list is in the shared store or the bundles.
