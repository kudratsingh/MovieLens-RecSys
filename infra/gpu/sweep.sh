#!/usr/bin/env bash
#
# WO-5 step two on the rented GPU: the approved sweep, start to self-stop, unattended.
#
#   bash sweep.sh <40-character commit>
#
# Run on the pod under nohup/setsid from the runbook
# (docs/model-planning/experiments/wo5-sweep-runbook.md), beside gpu_common.sh,
# self_stop.sh and watchdog.sh fetched at the same commit. Nothing here needs this
# Mac, an agent or a terminal once started: it survives a dropped SSH session, and
# the pod stops itself however the sweep ends.
#
# In order, stopping at the first failed check:
#
#   0. start     a watchdog in its own session (watchdog.sh: the hard limit, enforced
#                even if this script dies) and a heartbeat file updated every minute
#   1. preflight one RTX 4090, disk, memory          } the phases bootstrap.sh uses,
#   2. repo      the public repository at <commit>   } from gpu_common.sh
#   3. venv      infra/gpu/requirements-cuda.txt     }
#   4. data      ml-25m, published MD5, SHA-256s      }
#   5. cohort    ADR 0011's cold-start cohort, regenerated from the ratings (fixed seed)
#                and checked against its DVC pointer's MD5: the protocol of record
#                (v1's sha256:b4ed5afa...) includes it
#   6. smoke     python -m src.training.gpu_smoke --device cuda
#   7. sweep     python -m src.training.wo5_sweep run-sweep: calibration, then the six
#                cells, one process each, each bundled into out/ with a line in out/INDEX
#   8. finish    a last bundle of this script's own logs, the DONE or HALT line in INDEX,
#                then wait for the Mac to acknowledge every bundle in out/HARVESTED (at
#                most WO5_ACK_WAIT_SECONDS, default 1800, never past the hard limit), then
#                self_stop.sh: stop, not terminate
#
# Every way out -- done, HALT, a failed check, a crash -- goes through step 8. The hard
# limit (WO5_HARD_LIMIT_HOURS, default 42: about $37.38 at $0.89/h) is counted from the
# start of this script and checked between and during runs; the watchdog stops the pod
# at it whatever this script is doing. A rerun after a pod restart resumes after the
# last bundled run; a sweep whose INDEX already ends is not started again.
#
# Files under the sweep root (default /workspace/wo5-sweep):
#   HEARTBEAT, PHASE, DEADLINE, sweep.pid, watchdog.pid, WATCHDOG_HEARTBEAT,
#   SWEEP_EXITED, self-stop.log, watchdog.log, setup/{env,logs}, out/{INDEX,
#   HARVESTED, HALT, RESULT, STOP, <seq>-<step>-<run id>.tgz[.sha256]}, work/, mlstore/
#
# Rehearsal on the Mac (no GPU, clone, venv or download; a 1% subsample, one pass, a
# tiny encoder, every time limit scaled, and a simulated self-stop):
#
#   WO5_LOCAL_SMOKE=1 WO5_DEVICE=cpu WO5_REPO_DIR="$PWD" WO5_PYTHON=.venv/bin/python \
#   WO5_INPUT_DIR=data/raw/ml-25m WO5_ROOT=/tmp/wo5-sweep bash infra/gpu/sweep.sh HEAD
#
# Written for bash 3.2 as well as 5, because the rehearsal runs on macOS's.
set -Eeuo pipefail

COMMIT_ARG="${1:-}"
LOCAL_SMOKE="${WO5_LOCAL_SMOKE:-0}"
ROOT="${WO5_ROOT:-/workspace/wo5-sweep}"
REPO_URL="${WO5_REPO_URL:-https://github.com/kudratsingh/MovieLens-RecSys.git}"
EXPECTED_GPU="${WO5_EXPECTED_GPU:-RTX 4090}"
DATA_URL="https://files.grouplens.org/datasets/movielens/ml-25m.zip"
HARD_LIMIT_HOURS="${WO5_HARD_LIMIT_HOURS:-42}"
ACK_WAIT_SECONDS="${WO5_ACK_WAIT_SECONDS:-1800}"
HEARTBEAT_SECONDS="${WO5_HEARTBEAT_SECONDS:-60}"
MIN_DISK_GB="${WO5_MIN_DISK_GB:-20}"
MIN_MEMORY_GB="${WO5_MIN_MEMORY_GB:-16}"
TORCH_FALLBACK_VARIANTS="${WO5_TORCH_FALLBACK_VARIANTS:-cu129 cu130}"
INPUT_MANIFEST="docs/experiments/sasrec/ml-25m-input-sha256.json"
PLAN="docs/experiments/sasrec/wo5-sweep-plan.json"
COHORT="data/synthetic/cold_start/v1/users.parquet"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STARTED_EPOCH="$(date +%s)"
DEADLINE_EPOCH="$(awk -v s="$STARTED_EPOCH" -v h="$HARD_LIMIT_HOURS" 'BEGIN { printf "%d", s + h * 3600 }')"
MAX_WALL_SECONDS=$((DEADLINE_EPOCH - STARTED_EPOCH))

for piece in gpu_common.sh self_stop.sh watchdog.sh; do
  [ -f "$KIT/$piece" ] || {
    echo "$piece is not beside $0: fetch the kit's four files at the same commit (see the runbook)" >&2
    exit 1
  }
done
# shellcheck source=infra/gpu/gpu_common.sh
. "$KIT/gpu_common.sh"
trap 'log "error at line $LINENO: $BASH_COMMAND"' ERR

[ -n "$COMMIT_ARG" ] || {
  sed -n '3,5p' "$0" >&2
  exit 2
}
if [ "$LOCAL_SMOKE" = 1 ]; then
  DEVICE="${WO5_DEVICE:-cpu}"
  RUN_KIND="local-smoke"
  # A rehearsal never calls RunPod.
  SELF_STOP_ARGS="--simulate"
else
  DEVICE=cuda
  RUN_KIND="rented-gpu"
  SELF_STOP_ARGS=""
  printf '%s' "$COMMIT_ARG" | grep -Eq '^[0-9a-f]{40}$' ||
    fail "the commit must be the full 40-character SHA of a commit on main, got '$COMMIT_ARG'"
fi
if command -v sha256sum >/dev/null 2>&1; then SHA256="sha256sum"; else SHA256="shasum -a 256"; fi

mkdir -p "$ROOT/out"
if [ -f "$ROOT/out/INDEX" ] && grep -Eq '^(DONE|HALT)' "$ROOT/out/INDEX"; then
  echo "$ROOT/out/INDEX already ends ($(grep -E '^(DONE|HALT)' "$ROOT/out/INDEX" | head -n 1)); not starting again" >&2
  exit 3
fi
if [ -f "$ROOT/sweep.pid" ] && kill -0 "$(cat "$ROOT/sweep.pid")" 2>/dev/null; then
  echo "a sweep is already running (pid $(cat "$ROOT/sweep.pid"))" >&2
  exit 3
fi

OUT="$ROOT/setup"
FACTS="$OUT/env/sweep-facts.env"
mkdir -p "$OUT/env" "$OUT/logs" "$OUT/results"
exec > >(tee -a "$OUT/logs/sweep.log") 2>&1
echo "$$" >"$ROOT/sweep.pid"
echo "$DEADLINE_EPOCH" >"$ROOT/DEADLINE"
rm -f "$ROOT/SWEEP_EXITED"
echo "starting" >"$ROOT/PHASE"

export WO5_SELF_STOP_LOG="$ROOT/self-stop.log"
export WO5_SELF_STOP_MARKER="${WO5_SELF_STOP_MARKER:-$ROOT/SELF_STOPPED}"
export WO5_SELF_STOP_ARGS="$SELF_STOP_ARGS"
export WO5_ACK_WAIT_SECONDS="$ACK_WAIT_SECONDS"

# --- 0. the watchdog and the heartbeat ---------------------------------------------------
# The watchdog runs in its own session, so neither a dropped terminal nor this
# script's death takes it down; it exits once the pod's stop has been called.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup bash "$KIT/watchdog.sh" "$ROOT" >>"$ROOT/watchdog.log" 2>&1 </dev/null &
else
  nohup bash "$KIT/watchdog.sh" "$ROOT" >>"$ROOT/watchdog.log" 2>&1 </dev/null &
fi
SWEEP_PID=$$
heartbeat() {
  while kill -0 "$SWEEP_PID" 2>/dev/null; do
    printf '%s pid=%s phase="%s" elapsed_h=%s deadline_utc=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SWEEP_PID" "$(tr '\n' ' ' <"$ROOT/PHASE" 2>/dev/null)" \
      "$(awk -v e="$(elapsed)" 'BEGIN { printf "%.2f", e / 3600 }')" "$(utc_of "$DEADLINE_EPOCH")" \
      >"$ROOT/HEARTBEAT.tmp" && mv "$ROOT/HEARTBEAT.tmp" "$ROOT/HEARTBEAT"
    sleep "$HEARTBEAT_SECONDS"
  done
}
heartbeat &
HEARTBEAT_PID=$!
echo "$HEARTBEAT_PID" >"$ROOT/heartbeat.pid"
set_phase() {
  phase "$1"
  echo "$1" >"$ROOT/PHASE"
}

# --- 8. finish: every exit comes through here ----------------------------------------------
# shellcheck disable=SC2329 # invoked by the EXIT trap below.
finish() {
  local rc=$? result seq name staging digest status stop_rc
  trap - EXIT ERR
  set +e
  echo "finishing" >"$ROOT/PHASE"
  result="$(head -n 1 "$ROOT/out/RESULT" 2>/dev/null)"
  [ -n "$result" ] || result="HALT sweep.sh stopped with exit code $rc in phase ${PHASE:-start} (see the sweep log)"
  log "sweep ends: $result"
  # The last bundle: this script's own record, so the setup and the ending come home too.
  seq="$(awk 'NF == 6 && $1 ~ /^[0-9]+$/ { s = $1 } END { printf "%02d", s + 1 }' "$ROOT/out/INDEX" 2>/dev/null || echo 01)"
  name="${seq}-sweeplog-norun"
  staging="$ROOT/out/.staging/$name"
  rm -rf "$staging"
  mkdir -p "$staging"
  facts RESULT "$result"
  facts FINISHED_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  facts TOTAL_SECONDS "$(elapsed)"
  sleep 1
  cp -R "$OUT/env" "$OUT/logs" "$staging/" 2>/dev/null
  [ -d "$OUT/results" ] && cp -R "$OUT/results" "$staging/" 2>/dev/null
  for file in out/INDEX out/HALT out/RESULT HEARTBEAT WATCHDOG_HEARTBEAT watchdog.log self-stop.log; do
    [ -f "$ROOT/$file" ] && cp "$ROOT/$file" "$staging/$(basename "$file")"
  done
  # shellcheck disable=SC2086 # SHA256 is a command and its flags.
  (cd "$staging" && find . -type f -print0 | sort -z | xargs -0 $SHA256 >"$ROOT/out/.$name.manifest")
  mv "$ROOT/out/.$name.manifest" "$staging/MANIFEST.sha256"
  tar -C "$ROOT/out/.staging" -czf "$ROOT/out/.$name.tgz.part" "$name" && mv "$ROOT/out/.$name.tgz.part" "$ROOT/out/$name.tgz"
  digest="$($SHA256 "$ROOT/out/$name.tgz" | cut -d' ' -f1)"
  printf '%s  %s\n' "$digest" "$name.tgz" >"$ROOT/out/$name.tgz.sha256"
  rm -rf "$staging"
  status=LOGS
  case "$result" in HALT*) status=LOGS-HALT ;; esac
  echo "$seq sweeplog - $name.tgz $digest $status" >>"$ROOT/out/INDEX"
  echo "$result" >>"$ROOT/out/INDEX"
  log "INDEX: $seq sweeplog - $name.tgz $digest $status"
  log "INDEX: $result"
  echo "exited rc=$rc at $(date -u +%Y-%m-%dT%H:%M:%SZ): $result" >"$ROOT/SWEEP_EXITED"
  echo "waiting for the Mac" >"$ROOT/PHASE"
  log "waiting up to ${ACK_WAIT_SECONDS} s for the Mac to acknowledge every bundle (hard limit $(utc_of "$DEADLINE_EPOCH"))"
  wo5_wait_for_acks "$ROOT" "$ACK_WAIT_SECONDS" "$DEADLINE_EPOCH"
  echo "stopping the pod" >"$ROOT/PHASE"
  # shellcheck disable=SC2086 # SELF_STOP_ARGS is empty or one flag.
  bash "$KIT/self_stop.sh" $SELF_STOP_ARGS
  stop_rc=$?
  if [ "$stop_rc" = 0 ]; then
    echo "stop=ok" >>"$ROOT/SWEEP_EXITED"
  else
    echo "stop=failed rc=$stop_rc (the watchdog retries)" >>"$ROOT/SWEEP_EXITED"
  fi
  kill "$HEARTBEAT_PID" 2>/dev/null
  echo "stopped (self-stop exit $stop_rc)" >"$ROOT/PHASE"
  exit "$rc"
}
trap finish EXIT

facts SWEEP_STARTED_UTC "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
facts RUN_KIND "$RUN_KIND"
facts DEVICE "$DEVICE"
facts HARD_LIMIT_HOURS "$HARD_LIMIT_HOURS"
facts DEADLINE_UTC "$(utc_of "$DEADLINE_EPOCH")"
facts ACK_WAIT_SECONDS "$ACK_WAIT_SECONDS"
log "WO-5 step two: $RUN_KIND sweep on $DEVICE, hard limit ${HARD_LIMIT_HOURS} h (until $(utc_of "$DEADLINE_EPOCH"))"

set_phase PREFLIGHT
wo5_preflight

set_phase REPO
wo5_repo
for file in "$PLAN" src/training/wo5_sweep.py; do
  [ -f "$REPO/$file" ] || fail "$file is missing at $COMMIT: is this commit older than the sweep kit?"
done
cp "$REPO/$PLAN" "$OUT/env/"

set_phase VENV
wo5_venv

set_phase DATA
wo5_data

set_phase COHORT
expected_md5="$(sed -n 's/^- md5: *//p' "$REPO/$COHORT.dvc" | head -n 1)"
md5_of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1 || md5 -q "$1"; }
COHORT_PATH="$REPO/$COHORT"
if [ "$LOCAL_SMOKE" = 1 ]; then
  # A 1% rehearsal never reads the cohort (run_once attaches it at full data only);
  # it is still generated and checked, but beside the sweep, not into the checkout.
  COHORT_PATH="$OUT/cohort-check/users.parquet"
fi
if [ -f "$COHORT_PATH" ] && [ "$(md5_of "$COHORT_PATH")" = "$expected_md5" ]; then
  facts COHORT_SOURCE "present, DVC md5 matches"
elif [ -n "${WO5_COHORT_PARQUET:-}" ]; then
  mkdir -p "$(dirname "$COHORT_PATH")"
  cp "$WO5_COHORT_PARQUET" "$COHORT_PATH"
  facts COHORT_SOURCE "WO5_COHORT_PARQUET"
else
  run_logged 900 "$OUT/logs/cohort.log" \
    "$PY" -m synthetic.cold_start.generator --ratings-csv "$DATA_DIR/ratings.csv" --out "$COHORT_PATH"
  facts COHORT_SOURCE "regenerated from ratings.csv (ADR 0011's fixed seed)"
fi
cohort_md5="$(md5_of "$COHORT_PATH")"
facts COHORT_MD5 "$cohort_md5"
if [ "$cohort_md5" = "$expected_md5" ]; then
  facts COHORT_MD5_MATCHES_DVC true
  log "cohort: $COHORT matches its DVC pointer ($expected_md5)"
else
  # Bytes can differ across pyarrow builds while the rows do not; the protocol hash
  # each run checks before training is what binds the data.
  facts COHORT_MD5_MATCHES_DVC false
  log "warning: cohort md5 $cohort_md5 is not the DVC pointer's $expected_md5; each run's protocol-hash check decides"
fi

set_phase SMOKE
if [ "$LOCAL_SMOKE" = 1 ]; then export KMP_DUPLICATE_LIB_OK=TRUE; fi
run_logged 900 "$OUT/logs/gpu-smoke.log" \
  "$PY" -m src.training.gpu_smoke --device "$DEVICE" --out "$OUT/results/gpu-smoke.json"

set_phase SWEEP
args=(--plan "$REPO/$PLAN" --root "$ROOT" --deadline-epoch "$DEADLINE_EPOCH" --run-kind "$RUN_KIND")
if [ "$LOCAL_SMOKE" = 1 ]; then
  args+=(--sample-fraction "${WO5_SAMPLE_FRACTION:-0.01}" --device "$DEVICE" --epochs 1 --tiny
    --hours-scale "${WO5_HOURS_SCALE:-0.05}" --experiment "${WO5_EXPERIMENT:-wo5-sweep-local-smoke}")
  [ -z "${WO5_CALIBRATION_VERDICTS:-}" ] || args+=(--calibration-verdicts "$WO5_CALIBRATION_VERDICTS")
  facts OVERRIDES "${args[*]}"
fi
sweep_rc=0
"$PY" -m src.training.wo5_sweep run-sweep "${args[@]}" || sweep_rc=$?
log "the sweep runner exited $sweep_rc ($(head -n 1 "$ROOT/out/RESULT" 2>/dev/null || echo 'no RESULT'))"
exit 0
