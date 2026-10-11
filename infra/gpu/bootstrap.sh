#!/usr/bin/env bash
#
# WO-5 step one on a rented GPU: one command on a fresh pod, one tarball back.
#
#   bash bootstrap.sh <40-character commit>
#
# Written for a RunPod PyTorch pod (Ubuntu, an NVIDIA driver, python3, git and
# curl), run as root from the web terminal or over SSH. The runbook is
# docs/model-planning/experiments/wo5-gpu-timing-runbook.md; it starts this
# script under nohup so a dropped terminal does not end the session.
#
# In order, stopping at the first failed check:
#
#   1. preflight  -- one GPU whose name contains $WO5_EXPECTED_GPU ("RTX 4090"),
#                    enough disk and memory; the machine described into env/
#   2. repo       -- clone the public repository and check out exactly <commit>
#   3. venv       -- a Python 3.11+ venv with infra/gpu/requirements-cuda.txt
#                    (torch 2.13.0's CUDA build); torch must see the GPU
#   4. data       -- ml-25m.zip from GroupLens, its published MD5, then the
#                    SHA-256 of ratings.csv and movies.csv against the manifest
#                    committed from the Mac's copies
#   5. smoke      -- python -m src.training.gpu_smoke --device cuda
#   6. timing     -- cell A for 600 s, then cells 0b and B for 180 s each,
#                    one process per cell, with the GPU sampled every 2 s
#   7. package    -- summary.json, a MANIFEST.sha256 inside, one .tgz and its
#                    .sha256 beside it; the last line printed is the path to pull
#
# A failure still packages what exists (the tarball's name ends in -FAILED) so
# the logs come back, and the script exits non-zero. Nothing here logs to
# MLflow, uploads anything, or needs a key: the Mac pulls the tarball and
# records it (infra/gpu/pull_and_record.sh).
#
# Idempotent: a rerun reuses the clone, the venv when its torch already sees the
# GPU, and the dataset when its checksums still match; each run writes a new,
# timestamped session directory.
#
# Rehearsal on the Mac, which skips everything that needs Linux, a GPU or the
# network (no clone, no venv, no download) and overrides the device, the data
# size and the budget -- every override is recorded in each output:
#
#   WO5_LOCAL_SMOKE=1 WO5_DEVICE=mps WO5_REPO_DIR="$PWD" WO5_PYTHON=.venv/bin/python \
#   WO5_INPUT_DIR=data/raw/ml-25m WO5_ROOT=/tmp/wo5 bash infra/gpu/bootstrap.sh HEAD
#
# The preflight, repository, venv and data phases live in gpu_common.sh beside
# this file, shared with infra/gpu/sweep.sh. A copy of this script fetched on its
# own (the runbook's one-liner) fetches gpu_common.sh at the same pinned commit.
#
# Written for bash 3.2 as well as 5, because the rehearsal runs on macOS's.
set -Eeuo pipefail

COMMIT_ARG="${1:-}"
LOCAL_SMOKE="${WO5_LOCAL_SMOKE:-0}"
ROOT="${WO5_ROOT:-/workspace/wo5-timing}"
REPO_URL="${WO5_REPO_URL:-https://github.com/kudratsingh/MovieLens-RecSys.git}"
EXPECTED_GPU="${WO5_EXPECTED_GPU:-RTX 4090}"
DATA_URL="https://files.grouplens.org/datasets/movielens/ml-25m.zip"
# The runbook terminates the pod at 55 minutes of wall clock; this stops the
# script first, so there is time left to pull whatever it packaged.
MAX_WALL_SECONDS="${WO5_MAX_WALL_SECONDS:-3000}"
MIN_DISK_GB="${WO5_MIN_DISK_GB:-15}"
MIN_MEMORY_GB="${WO5_MIN_MEMORY_GB:-12}"
# Probed in this order if PyTorch's index has no wheel for the pinned variant
# (infra/gpu/requirements-cuda.txt says why cu126 is the pin).
TORCH_FALLBACK_VARIANTS="${WO5_TORCH_FALLBACK_VARIANTS:-cu129 cu130}"
CELLS_DIR="docs/experiments/sasrec"
CELLS="wo5-gpu-timing-cellA-600s.json wo5-gpu-timing-cell0b-180s.json wo5-gpu-timing-cellB-180s.json"
INPUT_MANIFEST="$CELLS_DIR/ml-25m-input-sha256.json"

STARTED_EPOCH="$(date +%s)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

COMMON="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gpu_common.sh"
if [ ! -f "$COMMON" ]; then
  printf '%s' "$COMMIT_ARG" | grep -Eq '^[0-9a-f]{40}$' || {
    echo "gpu_common.sh is not beside $0 and $COMMIT_ARG is not a full SHA to fetch it at" >&2
    exit 1
  }
  COMMON="$(dirname "$0")/gpu_common.sh"
  curl -fsSL -o "$COMMON" \
    "https://raw.githubusercontent.com/kudratsingh/MovieLens-RecSys/$COMMIT_ARG/infra/gpu/gpu_common.sh"
fi
# shellcheck source=infra/gpu/gpu_common.sh
. "$COMMON"
trap 'log "error at line $LINENO: $BASH_COMMAND"' ERR

usage() {
  sed -n '3,6p' "$0" >&2
  exit 2
}

[ -n "$COMMIT_ARG" ] || usage
if [ "$LOCAL_SMOKE" = 1 ]; then
  DEVICE="${WO5_DEVICE:-mps}"
  RUN_KIND="local-smoke"
else
  DEVICE=cuda
  RUN_KIND="rented-gpu"
  printf '%s' "$COMMIT_ARG" | grep -Eq '^[0-9a-f]{40}$' ||
    fail "the commit must be the full 40-character SHA of a commit on main, got '$COMMIT_ARG'"
fi

if command -v sha256sum >/dev/null 2>&1; then
  SHA256="sha256sum"
else
  SHA256="shasum -a 256"
fi

SESSION="wo5-timing-${STAMP}-${RUN_KIND}"
OUT="$ROOT/out/$SESSION"
FACTS="$OUT/env/bootstrap-facts.env"
mkdir -p "$OUT/env" "$OUT/logs" "$OUT/results" "$OUT/cells"
# The console log travels in the tarball: it is the run's console record.
exec > >(tee -a "$OUT/logs/bootstrap.log") 2>&1


SAMPLERS=""
stop_samplers() {
  local pid
  for pid in $SAMPLERS; do kill "$pid" 2>/dev/null || true; done
  SAMPLERS=""
}

package() {
  local status="$1" name
  stop_samplers
  [ -z "$PHASE" ] || facts "${PHASE}_SECONDS" $(($(date +%s) - PHASE_STARTED))
  PHASE=""
  facts STATUS "$status"
  facts FINISHED_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  facts TOTAL_SECONDS "$(elapsed)"
  if [ "$status" != PASS ] && [ -n "${PY:-}" ] && [ -x "${PY:-}" ] && [ -n "${REPO:-}" ]; then
    # What did finish, summarized; a passing run already wrote it in its own phase.
    (cd "$REPO" && PYTHONPATH="$REPO" "$PY" -m src.training.sasrec_timing summarize "$OUT") || true
  fi
  # Let tee write the last lines before the log is hashed.
  sleep 1
  # Written beside the session and then moved in, so the listing never sees it.
  # shellcheck disable=SC2086 # SHA256 is a command and its flags.
  (cd "$OUT" && find . -type f -print0 | sort -z | xargs -0 $SHA256 >"$ROOT/out/.manifest.tmp")
  mv "$ROOT/out/.manifest.tmp" "$OUT/MANIFEST.sha256"
  name="$SESSION"
  [ "$status" = PASS ] || name="${SESSION}-FAILED"
  tar -C "$ROOT/out" -czf "$ROOT/$name.tgz" "$SESSION"
  (cd "$ROOT" && $SHA256 "$name.tgz" >"$name.tgz.sha256")
  log "status: $status after $(elapsed) s"
  log "sha256: $(cut -d' ' -f1 "$ROOT/$name.tgz.sha256")"
  log "PULL: $ROOT/$name.tgz"
}

on_exit() {
  local rc=$?
  trap - EXIT ERR
  if [ "$rc" -ne 0 ]; then
    log "stopping on exit code $rc; packaging what exists"
    package FAILED || true
  fi
  exit "$rc"
}
trap on_exit EXIT

facts SESSION "$SESSION"
facts RUN_KIND "$RUN_KIND"
facts DEVICE "$DEVICE"
facts STARTED_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "WO-5 step one: $RUN_KIND session $SESSION on $DEVICE"

# --- 1. preflight -------------------------------------------------------------
phase PREFLIGHT
wo5_preflight

# --- 2. repository at the pinned commit ----------------------------------------
phase REPO
wo5_repo
for cells in $CELLS; do
  [ -f "$REPO/$CELLS_DIR/$cells" ] || fail "$CELLS_DIR/$cells is missing at $COMMIT"
  cp "$REPO/$CELLS_DIR/$cells" "$OUT/cells/"
done

# --- 3. Python environment ------------------------------------------------------
phase VENV
wo5_venv

# --- 4. data ---------------------------------------------------------------------
phase DATA
wo5_data

# --- 5. device smoke ----------------------------------------------------------------
phase SMOKE
if [ "$LOCAL_SMOKE" = 1 ]; then
  # Two OpenMP runtimes (torch's and FAISS's) share the Mac process; the
  # repository's tests and runs set the same pair.
  export KMP_DUPLICATE_LIB_OK=TRUE
fi
run_logged 900 "$OUT/logs/gpu-smoke.log" \
  "$PY" -m src.training.gpu_smoke --device "$DEVICE" --out "$OUT/results/gpu-smoke.json"

# --- 6. the three timings -------------------------------------------------------------
OVERRIDES=""
if [ "$LOCAL_SMOKE" = 1 ]; then
  OVERRIDES="--device $DEVICE --sample-fraction ${WO5_SAMPLE_FRACTION:-0.01} --timing-seconds ${WO5_TIMING_SECONDS:-20} --warmup-steps ${WO5_WARMUP_STEPS:-5}"
  facts OVERRIDES "$OVERRIDES"
elif command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,power.draw,clocks.sm,temperature.gpu \
    --format=csv -l 2 >"$OUT/env/gpu-utilization.csv" 2>&1 &
  SAMPLERS="$!"
  if command -v vmstat >/dev/null 2>&1; then
    # Host CPU alongside the GPU: a step that waits on the CPU shows up here.
    vmstat -t 5 >"$OUT/env/vmstat.txt" 2>&1 &
    SAMPLERS="$SAMPLERS $!"
  fi
fi
for cells in $CELLS; do
  cell="$(printf '%s' "$cells" | sed -n 's/^wo5-gpu-timing-cell\([A-Za-z0-9]*\)-.*/\1/p')"
  phase "TIMING_${cell}"
  budget="$(printf '%s' "$cells" | sed -n 's/.*-\([0-9]*\)s\.json$/\1/p')"
  # shellcheck disable=SC2086 # OVERRIDES is a list of flags without spaces in any value.
  run_logged $((budget + 900)) "$OUT/logs/timing-cell${cell}.log" \
    "$PY" -m src.training.sasrec_timing run "$CELLS_DIR/$cells" --out "$OUT/results" \
    --run-kind "$RUN_KIND" $OVERRIDES
done
stop_samplers

# --- 7. package ---------------------------------------------------------------------
phase PACKAGE
"$PY" -m src.training.sasrec_timing summarize "$OUT" | tee "$OUT/logs/summary.log"
trap - EXIT
package PASS
log "copy it back with: runpodctl send $ROOT/$SESSION.tgz  (or scp; see the runbook)"
