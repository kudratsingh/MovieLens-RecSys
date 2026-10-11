# WO-5 step two — the rented-GPU sweep, runbook

**Status:** prepared and rehearsed 2026-10-11; not run. Nothing has been spent on step two.
**Decision:** [D-052](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each)
(step two approved, with seven conditions). Step one, the timing it is priced from, is
[D-051](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each) and
[`wo5-gpu-timing-runbook.md`](wo5-gpu-timing-runbook.md). Prices before any future rental follow
non-negotiable 14 ([D-053](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each)).

## The owner's approval, verbatim (2026-10-11)

> "Step two approved: Secure Cloud RTX 4090, on-demand, up to $0.89/h, six cells at the 5-pass
> ceiling, about 38 GPU-hours and about $34. If the live rate is higher, stop and ask. Skip the
> separate C/D/E timing. Conditions: 1. Order: v1 calibration first (within 1% of 0.5091713455, one
> more seed on a miss, stop on a second miss). Then cells from cheapest to most expensive.
> 2. Self-shutdown that does not depend on this Mac: before any long run, prove on the pod that it
> can stop itself (for example runpodctl stop pod with its own pod id), then have the sweep script
> stop the pod when the last cell ends or at a hard time limit. Use stop, not terminate, so the
> results survive; terminate only after everything is verified at home. 3. Money cap: account holds
> about $45, auto-pay off. 4. After each cell: bundle, copy home, verify the checksum, import to
> MLflow, push to movielens-backups. Kill any cell running past 2x its projected time and report.
> 5. Every model is re-scored on the Mac CPU from its saved weights. 6. Conditional cells and the
> WO-7 seeds are separate approvals. 7. Add the register rows for tonight's $0.89 approval and this
> one."

Two additions the owner made the same day, also binding: **(a)** the pod waits for the Mac before it
stops itself — until the harvester has acknowledged the last bundle, or 30 minutes, whichever comes
first, and never past the hard limit; **(b)** every piece runs as a plain script that survives this
Claude session ending — `nohup`/`setsid` on the pod, a launchd LaunchAgent on the Mac that removes
itself when the sweep is home — with no step depending on an agent, an MCP tool, a monitor or a
scheduled Claude job.

## What runs, and in what order

One pod, one training process at a time, every run on the full data under the full protocol (v1's
protocol of record `sha256:b4ed5afa…`, ADR 0011's cohort included), the objective of record
(`strict-prefix-final-position-v1`), seed 42 except the contingent calibration. The order is
[`wo5-sweep-plan.json`](../../experiments/sasrec/wo5-sweep-plan.json); each cell is its own cells
file under `docs/experiments/sasrec/`.

| # | Step | Cells file | What it is | h/pass | Passes | Projected h | Killed past (2×) |
|---|---|---|---|---:|---:|---:|---:|
| 1 | calibration | `wo5-calibration-v1-cuda.json` | v1's exact cell (64/2/50, BCE, 32 negatives, per-example sampler, 2 passes), seed 42 | 0.479 (0b's measured step) | 2 | **1.21** | 2.42 |
| (2) | contingent calibration | `wo5-calibration-v1-cuda-seed2.json` | the same at **seed 7**, only after a miss; a second miss stops the sweep | 0.479 | 2 | 1.21 | 2.42 |
| 3 | cell 0b | `wo5-cell-0b-cuda.json` | 64/2/50, sampled softmax 1,024 | **0.479 measured** | ≤ 5 | **2.65** | 5.29 |
| 4 | cell A | `wo5-cell-A-cuda.json` | 128/2/100, 4 heads | **0.625 measured** | ≤ 5 | **3.38** | 6.75 |
| 5 | cell E | `wo5-cell-E-cuda.json` | A with the full softmax over 34,461 movies | 0.69 *projected* | ≤ 5 | **3.70** | 7.40 |
| 6 | cell B | `wo5-cell-B-cuda.json` | 256/2/100, 8 heads | **0.946 measured** | ≤ 5 | **4.98** | 9.96 |
| 7 | cell C | `wo5-cell-C-cuda.json` | 128/**4**/100 | 1.26 *projected* | ≤ 5 | **6.55** | 13.10 |
| 8 | cell D | `wo5-cell-D-cuda.json` | 128/2/**200** | 1.40 *projected* | ≤ 5 | **7.25** | 14.50 |

- **Projected hours** = passes × hours per pass + 0.25 h per run (data load, example store, the
  per-pass probe and holdout scoring, the final CPU scoring and export). Cells are at their 5-pass
  ceiling; early stopping may end any of them at 3 or 4.
- **Measured** = the 2026-10-11 timing on the same card type (run `522a71dc…`): s/step × 38,554 steps.
  **C, D and E are projections** from A's measured pass × ADR 0020's cost ratio against A (2.000,
  2.230, 1.096) — the owner skipped their separate timing. If one of them runs past twice its
  projection it is killed by design and the sweep stops for the owner (condition 4).
- **Why seed 7 for the contingent run:** it is the next seed of the project's headline list (42, 7,
  13; D-024) and of WO-1's reference pilots (42, 7, 13, 21), so a contingent run is also v1's seed-7
  GPU data point beside WO-7's v1 seeds rather than a one-off. It is judged against the same
  0.5091713455 ± 1%.
- **The calibration's pod-side verdict** is the warm recall@500 the run itself computes — trained on
  `cuda`, scored on the pod's CPU from the trained weights (`fit` moves the encoder back to the CPU,
  D6). It decides the contingent seed. The **D6 verdict of record** is the Mac's CPU re-score
  (condition 5), written by the harvester.

**Totals:** about **29.9 GPU-hours** projected (calibration + six cells + about 10 minutes of setup),
**31.1** with the contingent seed, plus at most 0.5 h waiting for the Mac at the end. At $0.89/h
that is **$26.60 / $27.68** (+ ≤ $0.45); the 50 GB volume adds about $0.007/h. The **hard limit is
42 h ≈ $37.38**, inside the account's ~$45 with auto-pay off. The owner's "about 38 GPU-hours, about
$34" is the same plan with margin.

| Projected clock from the start (h) | 0.17 | 1.38 | 4.02 | 7.40 | 11.10 | 16.08 | 22.63 | 29.88 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Done | setup | calibration | 0b | A | E | B | C | D |

## The kit

| Piece | Where it runs | What it does |
|---|---|---|
| [`infra/gpu/sweep.sh`](../../../infra/gpu/sweep.sh) | pod | preflight, clone at the pinned commit, venv, data, cohort, `cuda` smoke (shared with `bootstrap.sh` through `gpu_common.sh`), then `python -m src.training.wo5_sweep run-sweep`; finally a logs bundle, the DONE/HALT line, the wait for the Mac, the self-stop |
| [`src/training/wo5_sweep.py`](../../../src/training/wo5_sweep.py) | pod | the order, calibration logic, per-run timeout (2× projected, never past the hard limit), one process per run, one bundle per run, `out/INDEX`, `out/HALT` |
| [`infra/gpu/watchdog.sh`](../../../infra/gpu/watchdog.sh) | pod, own session | the hard limit whatever `sweep.sh` is doing; finishes the job if `sweep.sh` dies; retries a failed stop every 10 minutes |
| [`infra/gpu/self_stop.sh`](../../../infra/gpu/self_stop.sh) | pod | the pod's own key from `/proc/1/environ`; `runpodctl pod stop`, then `runpodctl stop pod`, then GraphQL `podStop`, then REST; `--probe` stops nothing |
| [`infra/gpu/harvest.sh`](../../../infra/gpu/harvest.sh) + [`src/training/wo5_harvest.py`](../../../src/training/wo5_harvest.py) | Mac, launchd | every 5 minutes: fetch, verify, unpack, import with the original run id, verify through the REST API, CPU re-score, back up, ACK |
| [`src/training/mlflow_import.py`](../../../src/training/mlflow_import.py) | Mac | the run-id-preserving import (was an uncommitted scratch tool; now in the repo, DSN from the environment or `Settings`) |

Pod layout under `/workspace/wo5-sweep/`: `kit/` (the four scripts), `repo/`, `venv/`, `data/`,
`setup/` (facts, logs), `work/<seq>-<step>/` (each run's console log, `result.json`, exported model),
`mlstore/mlflow.db` + `mlstore/files/` (the local MLflow store), `out/` (bundles, `INDEX`,
`HARVESTED`, `HALT`, `RESULT`, `STOP`), and `HEARTBEAT`, `WATCHDOG_HEARTBEAT`, `PHASE`, `DEADLINE`,
`sweep.pid`, `watchdog.pid`, `heartbeat.pid`, `SWEEP_EXITED`, `self-stop.log`, `watchdog.log`.

Each bundle `out/<seq>-<step>-<run id>.tgz` holds: `result.json` (run id, cells file and its SHA-256,
git commit, seed, data and protocol hashes, device/GPU/driver/CUDA, start and end UTC, every metric,
param and tag, per-pass numbers, the model archive's SHA-256), `runner.json` (status, timeout,
projection, calibration verdict, checks), `cells/`, `logs/console.log`, `logs/gpu-utilization.csv`,
`env/` (machine facts, `pip freeze`, `nvidia-smi`), `model/` (the exported archive), `mlflow/mlflow.db`
(the store holding this run only) with `mlflow/files/<run id>/artifacts/` (per-user recall file,
model, loss curve), and `MANIFEST.sha256`. An `INDEX` line is
`<seq> <step> <run id> <tgz> <sha256> <status>`; the last line is `DONE …` or `HALT …`.

## Before the pod (Mac, $0)

1. The kit is merged. Pin the commit and keep it: `cd ~/Machine-Learning-Projects/movielens-recsys
   && git checkout main && git pull && SHA=$(git rev-parse HEAD) && echo $SHA`.
2. The dev stack is up: `curl -s localhost:5001/health` prints `OK`; `docker ps` shows
   `movielens-coldstart-postgres-1`.
3. The SSH key is in RunPod's settings (`~/.ssh/id_ed25519.pub`, as on 2026-10-11).
4. `~/movielens-backups` is clean and pushes (`git -C ~/movielens-backups status`).
5. Nothing else trains on the Mac during the sweep: the CPU re-scores need it, one at a time.

## The session — the coordinator's exact sequence

**Billing starts at step 2 and ends when the pod is stopped; the volume bills (a few cents a day)
until it is terminated at step 12.**

1. **Read the live rate.** RunPod Secure Cloud, 1× RTX 4090, on-demand: if the rate is **above
   $0.89/h, stop and ask the owner** (the approval's ceiling). Record the rate shown.
2. **Create the pod** with the 2026-10-11 spec: Secure Cloud, 1× RTX 4090, on-demand (not spot),
   template `runpod-torch-v280` (image `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`), a 50 GB
   volume at `/workspace`, CUDA ≥ 12.8, SSH over exposed TCP. Note the pod id, the public IP and the
   SSH port. `ssh -p <port> -i ~/.ssh/id_ed25519 root@<ip> nvidia-smi` shows one RTX 4090.
3. **Fetch the kit at the pinned commit and probe the self-stop:**
   ```bash
   ssh -p PORT -i ~/.ssh/id_ed25519 root@IP "mkdir -p /workspace/wo5-sweep/kit && cd /workspace/wo5-sweep/kit && \
     for f in sweep.sh gpu_common.sh self_stop.sh watchdog.sh; do \
       curl -fsSLO https://raw.githubusercontent.com/kudratsingh/MovieLens-RecSys/$SHA/infra/gpu/\$f || exit 1; done && \
     bash self_stop.sh --probe"
   ```
   **Expected:** a line `pod=<id> key=present (N chars, from /proc/1/environ)`, one line per method,
   and `PROBE PASS: the pod's own key authenticates through: <A, B and/or C>` with exit 0, e.g.
   ```
   …Z self-stop[probe] pod=nqx5q11n02d9hz key=present (… chars, from /proc/1/environ)
   …Z self-stop[probe] runpodctl: /usr/bin/runpodctl, version: …
   …Z self-stop[probe] [A runpodctl pod get] exit 0: {"id":"…","desiredStatus":"RUNNING",…}
   …Z self-stop[probe] [B runpodctl get pod] exit 0: ID NAME … RUNNING …
   …Z self-stop[probe] [C graphql probe] {"data":{"pod":{"id":"…","desiredStatus":"RUNNING"}}}
   …Z self-stop[probe] [D rest probe] …403
   …Z self-stop[probe] PROBE PASS: the pod's own key authenticates through: A B C
   ```
   D's 403 is expected (2026-10-11). **`PROBE FAIL` (exit 1) or `key=MISSING` (exit 2): stop here,
   terminate the pod, report** — without a proven self-stop no long run starts (condition 2).
4. **The real proof: the pod stops itself.** Do it now, before anything is installed, so a lost GPU
   costs minutes, not a sweep:
   ```bash
   ssh -p PORT -i ~/.ssh/id_ed25519 root@IP "bash /workspace/wo5-sweep/kit/self_stop.sh"
   ```
   Expected: `STOPPING pod <id> (stop, not terminate)`, then `STOPPED via A` (or B/C/D), then the
   connection drops. **Confirm from outside** that the pod is stopped: the console (or `get-pod`)
   shows `desiredStatus: EXITED`; `self-stop.log` on the volume will say which method worked.
5. **Start it again** (console *Start*, or `pod-action start`), wait for SSH, and check
   `nvidia-smi` shows the RTX 4090 and `/workspace/wo5-sweep/kit/` is still there.
   **The GPU risk:** the volume is host-local, so a stopped pod restarts only on its own host; if
   that host's GPU was rented meanwhile the pod cannot start with a GPU. Nothing of value is on it
   yet, so: **terminate it, create a new pod (step 2) and repeat steps 3–5 on the new pod.** The
   same risk applies after the sweep's own self-stop: the results stay on the volume (stop, not
   terminate) and the Mac has normally pulled everything before then; if anything was not pulled
   and the GPU is gone, start the pod without a GPU if the console offers it, or wait and retry —
   never terminate a pod whose bundles are not all verified at home.
6. **Launch the sweep** (it detaches; a closed terminal does not stop it):
   ```bash
   ssh -p PORT -i ~/.ssh/id_ed25519 root@IP "cd /workspace/wo5-sweep/kit && \
     setsid nohup bash sweep.sh $SHA > /workspace/wo5-sweep/sweep.out 2>&1 < /dev/null & \
     sleep 20; cat /workspace/wo5-sweep/HEARTBEAT; tail -n 5 /workspace/wo5-sweep/sweep.out"
   ```
   It prints its hard deadline (`hard limit 42 h (until …)`). Setup takes about 10 minutes (the
   venv about 5); the smoke must pass before the calibration starts.
7. **Start the harvester on the Mac** (launchd; survives terminals, sleep-wake, Claude ending):
   ```bash
   cd ~/Machine-Learning-Projects/movielens-recsys
   ssh -p PORT -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new root@IP true   # first contact
   infra/gpu/harvest.sh install --host IP --port PORT --key ~/.ssh/id_ed25519
   infra/gpu/harvest.sh status
   ```
   It writes `~/Library/LaunchAgents/io.movielens.wo5-harvest.plist` (`KeepAlive`, `RunAtLoad`,
   under `caffeinate -i`) and polls every 5 minutes into `artifacts/wo5-sweep/`.
8. **After each run** (the harvester does all of this; the coordinator reads the result):
   `artifacts/wo5-sweep/harvest-summary.log` gets one line —
   `… <seq> <step> run=<id> status=<s> pod_warm=<x> mac_cpu_warm=<y> rel_diff=<d> d6=<PASS|MISS|-> rescore_run=<id> backup=<commit> ack=ok`.
   Check: the status is `FINISHED` (or `FINISHED-CAL-PASS`); the protocol hash, 1,931 warm / 710 cold
   users and cold recall 0.5262729520 were checked on the pod (a miss is `CHECK-FAILED` and a HALT);
   the import verified; the CPU re-score ran; the backup commit is pushed. Report each line.
9. **On a HALT** (INDEX ends `HALT …`; `out/HALT` says why): a killed run (past 2× its projection), a
   failed run, a failed check, the second calibration miss, the hard limit, or a `STOP` file. The
   pod packages what exists, waits for the Mac (≤ 30 min, never past the hard limit) and stops
   itself; the harvester brings it all home and removes itself. **Report to the owner; restart
   nothing** — a rerun needs the owner's word.
10. **On a Mac-side D6 MISS** while the pod's own verdict passed (`d6=MISS` on a calibration line):
    stop the sweep before its next run with
    `ssh … 'touch /workspace/wo5-sweep/out/STOP'` and report to the owner (the contingent seed is
    theirs to approve in that case). The run in flight is not interrupted.
11. **The end:** INDEX ends `DONE …`; the pod stops itself once the Mac has acknowledged every
    bundle (or 30 minutes). The harvester removes its own LaunchAgent after the last ACK.
12. **Verify at home, then terminate:** every run id in `INDEX` is in experiment `phase-a-sasrec`
    with its CPU re-score (`rescored_from=<id>`); `~/movielens-backups` has one commit per bundle,
    pushed; `artifacts/wo5-sweep/<seq>-<step>/` holds each unpacked bundle. **Only then** terminate
    the pod (console *Terminate* or `delete-pod`), and confirm no pod or volume bills.

## Is it alive? — checks that need no agent

| Piece | Started by | How to check it |
|---|---|---|
| `sweep.sh` | step 6 (`setsid nohup`) | `ssh … 'cat /workspace/wo5-sweep/HEARTBEAT'` — rewritten every minute: time, pid, phase (`step 04 cell-A started …`), elapsed hours, the hard deadline; `kill -0 $(cat /workspace/wo5-sweep/sweep.pid) && echo alive` |
| the runner and the training process | `sweep.sh` | `ssh … 'cat /workspace/wo5-sweep/PHASE; tail -n 3 /workspace/wo5-sweep/work/*/console.log; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv'` |
| `watchdog.sh` | `sweep.sh` (own session) | `ssh … 'cat /workspace/wo5-sweep/WATCHDOG_HEARTBEAT; kill -0 $(cat /workspace/wo5-sweep/watchdog.pid) && echo alive'` |
| progress | — | `ssh … 'cat /workspace/wo5-sweep/out/INDEX; cat /workspace/wo5-sweep/out/HARVESTED'` |
| the harvester | step 7 (launchd) | `launchctl list \| grep wo5-harvest` (a pid in the first column means running); `infra/gpu/harvest.sh status` (launchd state, last poll, each bundle's stages, the last summary lines); `tail -f artifacts/wo5-sweep/harvest.log`; `artifacts/wo5-sweep/harvest-status.json` (`last_poll_utc`) |
| the pod itself | RunPod | the console's pod page, or `runpodctl pod get <id>`: `desiredStatus` `RUNNING` / `EXITED`; billing on the account page |

**When it is all over:** `launchctl list | grep wo5-harvest` prints nothing and
`~/Library/LaunchAgents/io.movielens.wo5-harvest.plist` no longer exists (the harvester removed
both; its logs and status file stay in `artifacts/wo5-sweep/`). By hand at any time:
`infra/gpu/harvest.sh uninstall`.

## If the Mac sleeps, loses the network, or Claude stops

- **The pod does not need the Mac.** It keeps running its cells, writing bundles to its volume, and
  stops itself at the end — after the 30-minute wait for acknowledgements, or at once at the hard
  limit — and the watchdog enforces the hard limit even if `sweep.sh` itself dies.
- **The harvester catches up.** `caffeinate -i` keeps an idle Mac awake but not a closed lid on
  battery; after sleep, a network drop or a reboot, launchd restarts it (`KeepAlive`, `RunAtLoad`),
  it reads `harvest-state.json` and carries on from the first unfinished stage of the first
  unfinished bundle. Bundles never leave the pod's volume, so nothing is lost by a late Mac.
- **If the pod stopped before the Mac caught up**, a bundle the Mac already fetched is finished
  anyway (its import, re-score and backup are local; once the INDEX has ended, an ACK the stopped pod
  cannot take is recorded as `UNSENT` after three tries and the harvester still completes). A bundle
  never fetched is on the stopped pod's volume: start the pod (step 5's GPU caveat applies — a pod
  without its GPU still serves its files), let the harvester fetch it, then stop the pod again.
  Never terminate first.
- **Nothing depends on this session:** no step uses an agent, an MCP tool, a monitor or a scheduled
  Claude job; the coordinator's role is to start things and read the files above.

## What was tested before the session ($0, 2026-10-11)

- **Unit tests** (`tests/unit/test_wo5_sweep.py`, `test_wo5_harvest.py`, `test_mlflow_import.py`,
  `test_wo5_gpu_scripts.py`): the committed plan and every cells file against ADR 0020; the order;
  calibration pass, miss-then-pass, second miss; a killed run, a crashed run, the hard limit, a
  `STOP` file, all halting; INDEX parsing and resume; a real `run_once` through `train-cell` into a
  SQLite store; the one-run store snapshot in a bundle; the CPU re-score reproducing the run's own
  numbers exactly from its saved weights; a tampered archive refused; fetch/verify/ACK over a fake
  SSH; a corrupted download refetched; finish only after DONE/HALT and every ACK; the LaunchAgent
  removing itself; the import's SQLite and file-store readers, the remap, the plan, the dump check;
  `self_stop.sh` in simulate, probe and stop modes with stand-in `runpodctl` and `curl` (falls
  through to GraphQL; never prints the key; never terminates); the ACK wait, its timeout and the
  hard limit overriding it; the watchdog stopping the pod at the deadline and when `sweep.sh` died.
- **Local rehearsals** (`WO5_LOCAL_SMOKE=1`: CPU, a 1% subsample, one pass, a tiny encoder, time
  limits scaled, a simulated self-stop): the whole plan to DONE with forced calibration verdicts
  (MISS then PASS) and every bundle harvested into the shared store as `local-smoke` runs (experiment
  `wo5-sweep-local-smoke`) with CPU re-scores and one commit per bundle in a throwaway backup repo;
  a second rehearsal with real verdicts (both calibrations miss at 1%, so it HALTs) with the
  harvester running as a real LaunchAgent over a stand-in SSH: the pod side waited for the ACKs,
  stopped (simulated) on the last one, and the LaunchAgent removed itself. The refactored
  `bootstrap.sh` rehearsal still passes.
- **The pod path without a GPU:** `sweep.sh` in a `linux/amd64` `python:3.11` container with a
  stand-in `nvidia-smi`: preflight, clone at the pinned commit, venv and the full CUDA-wheel install,
  then the stop it must make at `torch.cuda.is_available()`; it still packaged its logs, wrote the
  HALT line, waited for the Mac, and called the self-stop (which, with no RunPod key in a container,
  reported `key=MISSING` and left the watchdog retrying). The venv it built (the pinned list, with
  `alembic` and `matplotlib`, about 6 minutes under emulation) then trained a tiny cell through
  `train-cell` and `run_once` on the CPU into a SQLite store, loss curve included — the store, the
  plotting and the trainer all work in the pod's environment. Without `alembic`, `mlflow-skinny`
  cannot open a SQLite store at all (`ModuleNotFoundError`), which is why it was added.
- `shellcheck` is clean on every script.

**What only the real pod can prove:** that the pod's own key may stop its own pod (step 4); that the
stopped pod restarts with its GPU; the real step times of C, D and E (projections until then); that
the pinned packages, now including `alembic` and `matplotlib`, install on the image; GPU memory at
cells B, D and E at full size; and the Mac's re-score time on full data (minutes per run, serial).
