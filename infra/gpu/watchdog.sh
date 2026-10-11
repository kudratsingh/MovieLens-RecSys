#!/usr/bin/env bash
#
# WO-5 step two: the hard limit and the last resort, kept apart from sweep.sh.
#
#   bash watchdog.sh <sweep root>
#
# sweep.sh starts this in its own session (setsid nohup), so a dropped terminal, a
# killed sweep.sh or a crashed runner cannot take it down. Every
# WO5_WATCHDOG_INTERVAL seconds (default 60) it writes WATCHDOG_HEARTBEAT, then:
#
#   1. past the hard deadline (the root's DEADLINE file): stops the sweep's processes
#      and stops the pod at once -- no waiting for the Mac (owner, 2026-10-11);
#   2. sweep.sh is gone without having written SWEEP_EXITED (it was killed, so its own
#      ending never ran): waits for the Mac's acknowledgements as sweep.sh would (at
#      most WO5_ACK_WAIT_SECONDS, never past the deadline), then stops the pod;
#   3. SWEEP_EXITED says the stop was called and accepted: exits, its job done;
#      says the stop failed: calls self_stop.sh again every 10 minutes until it works.
#
# It never deletes anything, and its only writes are its own heartbeat, pid and log.
set -uo pipefail

ROOT="${1:?usage: watchdog.sh <sweep root>}"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL="${WO5_WATCHDOG_INTERVAL:-60}"
ACK_WAIT_SECONDS="${WO5_ACK_WAIT_SECONDS:-1800}"
SELF_STOP_ARGS="${WO5_SELF_STOP_ARGS:-}"
STARTED_EPOCH="$(date +%s)"
# shellcheck source=infra/gpu/gpu_common.sh
. "$KIT/gpu_common.sh"

echo "$$" >"$ROOT/watchdog.pid"
deadline() { cat "$ROOT/DEADLINE" 2>/dev/null || echo 0; }
sweep_alive() { [ -f "$ROOT/sweep.pid" ] && kill -0 "$(cat "$ROOT/sweep.pid")" 2>/dev/null; }

# The INDEX's last line, when sweep.sh could not write it: the Mac's harvester
# finishes only once the INDEX has ended.
end_index() {
  grep -Eq '^(DONE|HALT)' "$ROOT/out/INDEX" 2>/dev/null || echo "HALT $1 (written by the watchdog)" >>"$ROOT/out/INDEX"
}

stop_pod() {
  log "watchdog: calling self_stop.sh ($1)"
  # shellcheck disable=SC2086 # SELF_STOP_ARGS is empty or one flag.
  bash "$KIT/self_stop.sh" $SELF_STOP_ARGS
}

# Everything the sweep started, so a rehearsal ends as cleanly as a stopped pod does.
stop_sweep_processes() {
  local pid
  cat "$ROOT/sweep.pid" "$ROOT/heartbeat.pid" 2>/dev/null | while read -r pid; do
    kill "$pid" 2>/dev/null || true
  done
  pkill -f "src.training.wo5_sweep run-sweep .*--root $ROOT" 2>/dev/null || true
  pkill -f "src.training.wo5_sweep train-cell .*$ROOT" 2>/dev/null || true
}

log "watchdog: started for $ROOT, hard deadline $(utc_of "$(deadline)"), every ${INTERVAL} s"
retrying=0
while :; do
  now="$(date +%s)"
  printf '%s watchdog pid=%s sweep_alive=%s deadline_utc=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" "$(sweep_alive && echo yes || echo no)" \
    "$(utc_of "$(deadline)")" >"$ROOT/WATCHDOG_HEARTBEAT"

  if [ "$(deadline)" -gt 0 ] && [ "$now" -ge "$(deadline)" ]; then
    log "watchdog: HARD LIMIT reached at $(utc_of "$now"); stopping the sweep and the pod now"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) hard limit reached; the watchdog stopped the pod" >>"$ROOT/out/HALT"
    stop_sweep_processes
    end_index "hard limit reached"
    if stop_pod "hard limit"; then exit 0; fi
    sleep 600
    continue
  fi

  if [ -f "$ROOT/SWEEP_EXITED" ]; then
    if grep -q '^stop=ok' "$ROOT/SWEEP_EXITED"; then
      log "watchdog: sweep.sh ended and its self-stop was accepted; exiting"
      exit 0
    fi
    if grep -q '^stop=failed' "$ROOT/SWEEP_EXITED"; then
      log "watchdog: sweep.sh's self-stop failed; retrying every 10 minutes"
      if stop_pod "retry after a failed stop"; then
        echo "stop=ok (watchdog retry)" >>"$ROOT/SWEEP_EXITED"
        exit 0
      fi
      sleep 600
      continue
    fi
    # SWEEP_EXITED without a stop line: sweep.sh is still waiting for the Mac, or
    # died while waiting.
    if ! sweep_alive; then
      log "watchdog: sweep.sh died during its ending; finishing it"
      end_index "sweep.sh died during its ending"
      wo5_wait_for_acks "$ROOT" "$ACK_WAIT_SECONDS" "$(deadline)"
      stop_pod "sweep.sh died while waiting" && exit 0
      sleep 600
      continue
    fi
  elif ! sweep_alive && [ -f "$ROOT/sweep.pid" ] && [ "$retrying" = 0 ]; then
    retrying=1
    log "watchdog: sweep.sh is gone without its ending (killed?); waiting for the Mac, then stopping"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sweep.sh died without finishing; the watchdog stopped the pod" >>"$ROOT/out/HALT"
    stop_sweep_processes
    end_index "sweep.sh died without finishing"
    wo5_wait_for_acks "$ROOT" "$ACK_WAIT_SECONDS" "$(deadline)"
    if stop_pod "sweep.sh died"; then
      echo "exited (watchdog) stop=ok" >"$ROOT/SWEEP_EXITED"
      exit 0
    fi
    retrying=0
    sleep 600
    continue
  fi
  sleep "$INTERVAL"
done
