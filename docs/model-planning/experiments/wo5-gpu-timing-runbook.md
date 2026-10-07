# WO-5 step one — the rented-GPU timing session, runbook

**Status:** prepared 2026-10-07, not run. Nothing has been spent, no account exists, no key was used.
**Decision:** [D-050](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each)
(prepare it; buy it the next day). **The spend itself needs the owner's explicit approval at the
session** ([D-047](../03-decision-register.md#owner-decisions-from-2026-10-05--one-dated-row-each)):
nothing below is to be done on the strength of this page alone.
**Plan and prices:** [`../memos/wo5-hardware-options-2026-10-07.md`](../memos/wo5-hardware-options-2026-10-07.md).

**The session, in one line:** RunPod Secure Cloud, one RTX 4090, on-demand (not spot), $0.74/h; one
session of at most 1.0 h and $1.00; time cell A for 10 minutes and cells 0b and B for 3 minutes each
on `cuda`, after a `cuda` smoke test; pull the results back; terminate.

**What it is not.** A timing. No model is trained to the end, evaluated or exported, and nothing it
produces is a result of record. Its one output that matters is the seconds per optimizer step of
three of ADR 0020's cells on the card, which turns the memo's assumed 8–20× speed-up into a measured
one and prices step two.

## The kit

| Piece | What it does |
|---|---|
| [`infra/gpu/bootstrap.sh`](../../../infra/gpu/bootstrap.sh) | Runs on the pod. Preflight (one RTX 4090, disk, memory), clone at the pinned commit, a venv with `infra/gpu/requirements-cuda.txt`, the dataset (GroupLens zip, published MD5, then SHA-256 of `ratings.csv` and `movies.csv` against [`ml-25m-input-sha256.json`](../../experiments/sasrec/ml-25m-input-sha256.json)), the smoke, the three timings, one tarball. Stops at the first failed check and still packages the logs |
| [`infra/gpu/requirements-cuda.txt`](../../../infra/gpu/requirements-cuda.txt) | `torch==2.13.0+cu126` from PyTorch's index (2.13.0 has no cu128 build) and the slice of `pyproject.toml` the trainer imports, pinned to the Mac's venv. The sidecar's `torch==2.12.0+cpu` is untouched |
| `python -m src.training.gpu_smoke --device cuda` | Device present; v1's shape trains 200 steps on the device and memorizes; the sampled softmax at 1,024 negatives; export and reload **on the CPU** with identical weights and top-10 lists (the D6 "scored on the CPU" path); device-vs-CPU forward parity |
| `python -m src.training.sasrec_timing run <cells.json>` | The WO-4 step-timing method, committed: 20 warm-up steps excluded, then **N seconds of steady stepping** (setup excluded), steady mean × 38,554 steps per pass |
| [`wo5-gpu-timing-cellA-600s.json`](../../experiments/sasrec/wo5-gpu-timing-cellA-600s.json), [`-cell0b-180s`](../../experiments/sasrec/wo5-gpu-timing-cell0b-180s.json), [`-cellB-180s`](../../experiments/sasrec/wo5-gpu-timing-cellB-180s.json) | ADR 0020's cells A, 0b and B on `cuda`: strict-prefix objective, sampled softmax 1,024, seed 42, full data, early stopping off. Each pins the protocol hash and the steps per pass, and carries its CPU and `mps` reference step times (measured for A and 0b, projected for B) |
| [`infra/gpu/pull_and_record.sh`](../../../infra/gpu/pull_and_record.sh) | Runs on the Mac. Checks the tarball's SHA-256, unpacks it into `artifacts/wo5-timing/<timestamp>/`, logs one tagged MLflow run (`timing_only=true`, `not_a_result_of_record=true`), reads it back, prints the backup commands |

## Before the session (on the Mac, $0)

1. **The commit.** The kit must be merged. Get the commit to pin and print the line to paste:
   ```bash
   cd ~/Machine-Learning-Projects/movielens-recsys && git fetch origin
   SHA=$(git rev-parse origin/main) && git cat-file -e "$SHA:infra/gpu/bootstrap.sh" && echo "$SHA"
   echo "cd /workspace && curl -fsSLO https://raw.githubusercontent.com/kudratsingh/MovieLens-RecSys/$SHA/infra/gpu/bootstrap.sh && (nohup bash bootstrap.sh $SHA > wo5-bootstrap.out 2>&1 &) && sleep 2 && tail -f wo5-bootstrap.out"
   ```
   Keep that last line (the "pod command") to paste in step 9.
2. **MLflow is up:** `curl -s localhost:5001/health` prints `OK`.
3. **A way to copy one file back.** Either (a) `runpodctl` on the Mac (RunPod's own CLI; their docs give
   the Homebrew tap) — it needs no key to *receive*; or (b) an SSH key: put your public key
   (`cat ~/.ssh/id_ed25519.pub`) in RunPod's settings before deploying, so the pod's "SSH over exposed
   TCP" accepts `scp`.
4. **Your reference numbers** (below, "What was tested before the session") are in front of you, so a
   wildly wrong step time is obvious while the pod is still up.

## The session (on RunPod — billed from step 8 until step 14)

5. **Account.** Create the RunPod account. Turn on two-factor authentication.
6. **Credit.** Billing → add the smallest top-up RunPod accepts ($10 if that is the minimum). **Auto-pay
   (automatic top-up) OFF.** Prepaid credit is the only exposure: at a $0 balance RunPod stops pods.
7. **Choose the pod** (Pods → Deploy):
   - **Secure Cloud** (not Community);
   - GPU **RTX 4090**, count **1**;
   - pricing **On-Demand** (not Spot / Interruptible). **The rate shown must be $0.74/h** (±1–2¢). If it
     is higher, stop: the approval is for that rate;
   - template: the official **RunPod PyTorch** template (publisher RunPod; any recent 2.x). The script
     installs its own torch, so it needs only the template's NVIDIA driver, `git`, `curl` and
     `python3`. If the deploy page offers a CUDA-version filter, choose **12.6 or newer**;
   - **Edit template → Volume disk 50 GB** mounted at `/workspace`; container disk at its default
     (20 GB is plenty);
   - leave SSH and the web terminal enabled; Jupyter is not needed.
8. **Deploy On-Demand.** Note the time: this is where billing starts.
9. **Connect** (Connect → web terminal, or the SSH command it shows) and **paste the pod command** from
   step 1. It downloads `bootstrap.sh` at the pinned commit and starts it under `nohup`; `tail -f` shows
   it. **Ctrl-C stops only the `tail`**; the job keeps running and survives a closed terminal. To look
   again: `tail -f /workspace/wo5-bootstrap.out`.
10. **Wait.** Expected, from the paste (estimates; the venv and download depend on the data centre):

    | Phase | Expected |
    |---|---|
    | Preflight, clone | under 1 min |
    | Venv: `pip install` of torch 2.13.0+cu126 and its CUDA libraries (6.8 GB installed) | 3–8 min |
    | Data: `ml-25m.zip` (262 MB), MD5, extract, SHA-256 | about 1 min |
    | `cuda` smoke | about 1–2 min |
    | Cell A: about 1 min of setup (CSV read, protocol hash, example store), 20 warm-up steps, then 600 s | about 12 min |
    | Cells 0b and B: setup, warm-up, 180 s each | about 4–5 min each |
    | Summary and tarball | seconds |
    | **Total** | **about 25–35 min** (under 45 with the pod's start-up) |

    At $0.7468/h with the disk, 35 minutes is about $0.44.
11. **Success looks like** this at the end of the output:
    ```
    cell  device   steps  mean s/step           p10-p90   h/pass   x cpu   x mps
    0b    cuda       ...
    A     cuda       ...
    B     cuda       ...
    gpu smoke: PASS
    overall: PASS
    hh:mm:ssZ status: PASS after ... s
    hh:mm:ssZ sha256: <64 hex characters>
    hh:mm:ssZ PULL: /workspace/wo5-timing/wo5-timing-<UTC stamp>-rented-gpu.tgz
    ```
    `x cpu` for cell A is the number step two turns on (the memo's break-even is 10.2× at five passes).
12. **Abort criteria — terminate (step 14) when any of these happens:**
    - a line with `FAILED:` in it — the script has stopped on its own and packaged what it had as
      `...-FAILED.tgz`; pull that first if it exists (step 13), then terminate;
    - the cost shown for the session passes **$1.00**;
    - **55 minutes** of wall clock since step 8, whatever the script is doing (the script gives up on
      its own after 50, checked between its phases);
    - the GPU is not an RTX 4090, or the rate is not the approved one.

    To stop the script by hand before terminating: `pkill -f bootstrap.sh; pkill -f src.training`.
13. **Copy the tarball back** (and its `.sha256`, or keep the `sha256:` line the script printed):
    - **runpodctl** — on the pod (RunPod ships `runpodctl` in its pods; if it is missing, use scp):
      `runpodctl send /workspace/wo5-timing/<name>.tgz`; it prints a one-time code; on the Mac:
      `cd ~/Downloads && runpodctl receive <code>`. Repeat for `<name>.tgz.sha256`, or skip it and
      pass `--sha256 <hash>` in step 15;
    - **scp** — with "SSH over exposed TCP" (the Connect tab shows the IP and port):
      `scp -P <port> -i ~/.ssh/id_ed25519 'root@<ip>:/workspace/wo5-timing/wo5-timing-*.tgz*' ~/Downloads/`.

    Before terminating, check it on the Mac: `shasum -a 256 ~/Downloads/<name>.tgz` must equal the
    `sha256:` line. It is about 1 MB at most.
14. **Terminate and verify** — this is what stops the meter:
    - Pods → the pod → **Terminate** (not Stop: a stopped pod keeps billing its 50 GB volume), confirm;
    - the Pods list is empty, and Storage shows no network volume (none was created);
    - the account's spend rate reads **$0.00/h**; note the session's charge (about $0.40–0.75) and the
      balance left (the top-up less the charge);
    - auto-pay is still off.
    The remaining credit stays on the account; nothing else bills.

## After the session (on the Mac)

15. **MLflow up**, then from the main checkout:
    ```bash
    cd ~/Machine-Learning-Projects/movielens-recsys
    PYTHON=.venv/bin/python infra/gpu/pull_and_record.sh ~/Downloads/<name>.tgz --rate-usd-per-hour 0.74
    ```
    (add `--sha256 <hash>` if the `.sha256` was not copied). It refuses a tarball whose SHA-256 or
    inner manifest does not match, or that carries anything credential-shaped; then logs one run in
    `phase-a-sasrec` tagged `timing_only=true`, `not_a_result_of_record=true`, `run_kind=rented-gpu`,
    with the provider, instance, GPU, driver, CUDA and rate tags and every file attached, reads it back
    through the REST API, and writes `mlflow-run.json` beside the unpacked files. Running it twice on
    the same tarball verifies the first run instead of logging a second.
16. **Backup** (non-negotiable 12): run the commands it prints — a fresh `pg_dump` of the `mlflow`
    database and the tarball into `~/movielens-backups`, commit, push — and note the commit hash.
17. **Hand back** the run id, the backup commit and the charge. The decision register gets the dated
    row for your spend approval and its outcome in the pull request that records the timing.

## How the measured step becomes the step-two proposal

The coordinator does this with the session's `summary.json` and writes it up for your explicit
approval; nothing more is rented until you give it.

- **The speed-up.** `x cpu` per cell = the cells file's CPU reference step / the measured `cuda` steady
  mean. Cell A and 0b are against measured CPU steps (1.4097 s and 0.297 s); B is against a projection
  (5.31 s on ADR 0020's cost model), so B's figure also tests that projection.
- **GPU-hours per cell** = the cell's CPU hours per pass ([`wo5-cell-repricing.md`](wo5-cell-repricing.md),
  ADR-model column) ÷ its speed-up, × 3–5 passes. A, 0b and B are measured; C, D and E (not timed)
  take A's measured step × ADR 0020's cost ratio against A (2.000, 2.230, 1.096), which on a GPU is
  the conservative direction.
- **Calibration** (ADR 0020 D6, D-045): v1's exact cell, two passes, plus one contingent seed. v1
  trains with the per-example BCE sampler, a host-side Python loop that none of today's three cells
  exercises, so its speed-up is not read off 0b: it is bounded above by its CPU time (4.9 h, about
  $3.70 at $0.75/h) and stated that way.
- **The band** (memo §6): at A ≥ 10.2× every must-have fits the $75 ceiling (D-044) at five passes on
  Secure Cloud; between 6× and 10.2×, Community Cloud (≤ $43.25) or cell B on `mps`; below about 5×,
  rent nothing and run on `mps`. Total = hours × ($0.7468 + $0.0068 disk) × 1.2.
- **The proposal** names the provider, the same day's price, the hours per cell, the hard cap
  (≤ $74.00 with step one inside $75), on-demand only, and pulling artifacts after every run.

## What was tested before the session

On 2026-10-07, on the Mac and in a container, at a cost of $0:

- **The whole script in its Mac rehearsal mode** (`WO5_LOCAL_SMOKE=1`: no clone, venv or download; the
  device, a 1% user subsample, 20 s budgets and 5 warm-up steps overridden and recorded), on `mps` and
  on `cpu`. Both passed: data check, smoke, three timings, summary, tarball. The `mps` tarball went
  through `pull_and_record.sh` into the shared store as run `8318e187aba54e90a4f7f17f17f94db1`
  (`phase-a-sasrec`, `run_kind=local-smoke`). Its outputs are in
  [`wo5-gpu-timing-local-smoke-2026-10-07.json`](../../experiments/sasrec/wo5-gpu-timing-local-smoke-2026-10-07.json).
- **Reference step times from those rehearsals** — a 1% subsample, so an 11,623-movie catalog
  instead of 34,461; *local smoke, not results*:

  | Cell | `mps` s/step | `cpu` s/step | Full-data reference (CPU / `mps`) |
  |---|---:|---:|---|
  | 0b | 0.1060 | 0.2942 | 0.297 / 0.109, measured (WO-4) |
  | A | 0.5302 | 1.3066 | 1.4097 / 0.5502, measured (WO-5 prep) |
  | B | 1.1173 | 2.8020 | 5.31 / 2.07, projected only |

  On both devices B ran about 2.1× A's step, against ADR 0020's 3.77× cost ratio: a first hint that
  the projection for the wide cell is pessimistic, which tomorrow's full-catalog `cuda` number tests.
- **The pinned environment:** a clean venv from exactly `requirements-cuda.txt` (torch swapped for its
  macOS build) ran the smoke and a timing; and `uv pip compile` resolved the file as written for
  Linux x86-64 on Python 3.11, 3.12 and 3.13, including `torch==2.13.0+cu126`.
- **The data:** `ml-25m.zip` fetched from GroupLens once; its MD5 equals the published one
  (`6b51fb2759a8657d3bfcbfc42b592ada`, now pinned in the manifest), and the two CSVs extracted from it
  match the Mac's SHA-256s byte for byte. GroupLens's TLS certificate is valid until 2026-12-13.
- **The pod path, minus the GPU:** the script as committed, run in a `linux/amd64` `python:3.11`
  container with a stand-in `nvidia-smi` (an RTX 4090 on a driver reporting CUDA 12.8). Preflight,
  the clone and checkout at a pinned SHA, `python3.11 -m venv`, the wheel probe and the full `pip
  install` (`torch 2.13.0+cu126`, CUDA 12.6; 6.8 GB installed, about 5.5 minutes under emulation)
  all ran; it then stopped, as it must without a GPU, at `torch.cuda.is_available()` and packaged
  `...-FAILED.tgz` with exit 1. A second run, from a copy with only the two CUDA assertions relaxed,
  reused the venv, fetched GroupLens's published MD5, ran `prepare-inputs` on the real zip (both
  SHA-256s match), and stopped at the smoke's `device_available` check, again packaged. The first
  attempt found one bug, now fixed: `curl | grep -q` under `pipefail` read every wheel as missing.
- **Unit tests:** `tests/unit/test_sasrec_timing.py` (the time bound, the step bound, the output
  schema, the three cells files, the dataset checks, the session summary and an idempotent MLflow
  record) and `tests/unit/test_gpu_smoke.py` (the smoke on `cpu`, on `mps` where present, a missing
  device, and a crash mid-smoke reported as a failed check).

**What only the pod can show:** that `torch.cuda.is_available()` is true with this wheel on that
driver; the `cuda` step times themselves; peak GPU memory (`torch.cuda.max_memory_allocated`); how
much of the step the pod's single host thread holds back (the GPU-utilization and `vmstat` samples in
the tarball show it); and the real durations of the install and the download.

## What could go wrong

The likeliest failure is the environment: if the CUDA wheel and the pod's driver disagree,
`torch.cuda.is_available()` is false and the script stops at its venv check within about ten minutes
(about $0.12), and the fix is a pod whose driver reports CUDA 12.6 or newer (the script already
falls back across PyTorch's cu129 and cu130 builds, and to PyPI's, and records which it used). The
dataset download can be blocked — GroupLens's certificate lapsed once (results.md, 2026-08-29), and
the script never turns verification off — and then the fallback is to send the two files from the
Mac (`tar -czf ml-25m-csvs.tgz -C data/raw/ml-25m ratings.csv movies.csv`, `runpodctl send` it to the
pod's `/workspace`, rerun the pod command with `WO5_DATA_TARBALL=/workspace/ml-25m-csvs.tgz` in front
of `nohup`); the SHA-256 check is the same either way. cuDNN and CUDA kernel nondeterminism does not
matter for a timing: no number here is a model result, every timing records that TF32 is off
(`float32_matmul_precision`), and models trained in step two are re-scored on the CPU under D6's ±1%.
An on-demand pod is not preempted; only spot pods are, which is why spot is excluded. A dropped
terminal does not stop the job (`nohup`), and a rerun of the pod command reuses the clone, the venv
and the verified data.
