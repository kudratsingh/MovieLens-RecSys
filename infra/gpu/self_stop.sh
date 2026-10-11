#!/usr/bin/env bash
#
# The pod stops itself: RunPod's *stop*, never terminate, so its volume and every
# result on it survive until they are verified at home (owner condition 2).
#
#   bash self_stop.sh             stop this pod (sweep.sh and watchdog.sh call this)
#   bash self_stop.sh --probe     prove the pod's own key authenticates; stop nothing
#   bash self_stop.sh --simulate  rehearsal: log what would be tried; no network at all
#
# The pod's own RUNPOD_API_KEY and RUNPOD_POD_ID are in PID 1's environment, not
# in an SSH shell's, so they are read from /proc/1/environ (root only), falling
# back to this shell's environment. The key is never printed, logged or written
# anywhere but runpodctl's own config file under ~/.runpod (never under /workspace,
# so it can never ride along in a bundle).
#
# Four ways to stop, tried in this order until one works:
#
#   A  runpodctl pod stop <id>        the current CLI (v2), key in RUNPOD_API_KEY
#   B  runpodctl stop pod <id>        the legacy spelling (v1.x, still accepted by v2),
#                                     after `runpodctl config --apiKey` if A had no key
#   C  GraphQL podStop mutation       POST https://api.runpod.io/graphql, Bearer key
#   D  REST POST /v1/pods/<id>/stop   https://rest.runpod.io/v1, Bearer key
#
# --probe runs each method's read instead (pod get / get pod / the GraphQL pod
# query / REST GET) and prints which authenticate; it exits 0 if any does. On
# 2026-10-11 the pod key got HTTP 403 from REST GET, so D's read is expected to
# fail; A, B or C must pass, and then the real stop/start test proves the stop.
#
# Exit 0 once a stop call was accepted (the container may be killed before this
# returns), 1 when every method failed, 2 when the key or pod id is missing.
#
# Test hooks (rehearsals and tests only): WO5_SELF_STOP_ENVIRON, WO5_RUNPODCTL,
# WO5_CURL, WO5_RUNPOD_GRAPHQL_URL, WO5_RUNPOD_REST_URL, WO5_SELF_STOP_LOG,
# WO5_SELF_STOP_MARKER (--simulate writes it).
set -uo pipefail

MODE=stop
case "${1:-}" in
"") ;;
--probe) MODE=probe ;;
--simulate) MODE=simulate ;;
-h | --help)
  sed -n '2,34p' "$0"
  exit 0
  ;;
*)
  echo "usage: $0 [--probe|--simulate]" >&2
  exit 64
  ;;
esac

ENVIRON="${WO5_SELF_STOP_ENVIRON:-/proc/1/environ}"
RUNPODCTL="${WO5_RUNPODCTL:-runpodctl}"
CURL="${WO5_CURL:-curl}"
GRAPHQL_URL="${WO5_RUNPOD_GRAPHQL_URL:-https://api.runpod.io/graphql}"
REST_URL="${WO5_RUNPOD_REST_URL:-https://rest.runpod.io/v1}"
LOG="${WO5_SELF_STOP_LOG:-/workspace/wo5-sweep/self-stop.log}"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
say() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) self-stop[$MODE] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$LOG" 2>/dev/null || true
}
# Whatever an API answered, shortened, on one line. It never holds the key.
brief() { tr '\n' ' ' | cut -c1-300; }

from_environ() {
  [ -r "$ENVIRON" ] || return 0
  tr '\0' '\n' <"$ENVIRON" | sed -n "s/^$1=//p" | head -n 1
}
KEY="$(from_environ RUNPOD_API_KEY)"
POD="$(from_environ RUNPOD_POD_ID)"
KEY_SOURCE="$ENVIRON"
[ -n "$KEY" ] || {
  KEY="${RUNPOD_API_KEY:-}"
  KEY_SOURCE="this shell"
}
[ -n "$POD" ] || POD="${RUNPOD_POD_ID:-}"
say "pod=${POD:-MISSING} key=$([ -n "$KEY" ] && echo "present (${#KEY} chars, from $KEY_SOURCE)" || echo MISSING)"

if [ "$MODE" = simulate ]; then
  say "would try: A '$RUNPODCTL pod stop $POD', B '$RUNPODCTL stop pod $POD', C GraphQL podStop at $GRAPHQL_URL, D POST $REST_URL/pods/$POD/stop"
  [ -z "${WO5_SELF_STOP_MARKER:-}" ] || say "SIMULATED STOP of pod ${POD:-unknown}" >>"$WO5_SELF_STOP_MARKER"
  say "SIMULATED: nothing was called"
  exit 0
fi
if [ -z "$KEY" ] || [ -z "$POD" ]; then
  say "cannot stop: RUNPOD_API_KEY or RUNPOD_POD_ID is missing (is this a RunPod pod, run as root?)"
  exit 2
fi

# The Bearer header goes through a mode-600 file, so the key is not on curl's argv.
HEADER="$(mktemp)"
chmod 600 "$HEADER"
printf 'Authorization: Bearer %s\n' "$KEY" >"$HEADER"
trap 'rm -f "$HEADER"' EXIT

have_runpodctl() { command -v "$RUNPODCTL" >/dev/null 2>&1; }
if have_runpodctl; then
  say "runpodctl: $(command -v "$RUNPODCTL"), version: $("$RUNPODCTL" version 2>&1 | brief)"
else
  say "runpodctl: not found ($RUNPODCTL); methods A and B are skipped"
fi

# A: runpodctl v2. B: the legacy spelling, configuring the key if it was not seen.
ctl() { RUNPOD_API_KEY="$KEY" "$RUNPODCTL" "$@" 2>&1; }
legacy_configured=0
configure_legacy() {
  [ "$legacy_configured" = 1 ] && return 0
  legacy_configured=1
  # v1.x reads only its config file; this writes ~/.runpod/config.toml.
  if "$RUNPODCTL" config --apiKey "$KEY" >/dev/null 2>&1; then
    say "runpodctl config --apiKey: written (~/.runpod)"
  else
    say "runpodctl config --apiKey: not accepted by this runpodctl"
  fi
}
graphql() {
  "$CURL" -sS -m 30 -H 'Content-Type: application/json' -H "@$HEADER" \
    --data "{\"query\":\"$1\"}" "$GRAPHQL_URL" 2>&1
}
rest() {
  "$CURL" -sS -m 30 -o /dev/stderr -w '%{http_code}' -X "$1" -H "@$HEADER" "$REST_URL$2" 2>&1
}
graphql_ok() { printf '%s' "$1" | grep -q '"desiredStatus"' && ! printf '%s' "$1" | grep -q '"errors"'; }

method_a() {
  local verb="$1" out code
  out="$(ctl pod "$verb" "$POD")"
  code=$?
  say "[A runpodctl pod $verb] exit $code: $(printf '%s' "$out" | brief)"
  [ "$code" = 0 ] && ! printf '%s' "$out" | grep -qi '"error"'
}
method_b() {
  local verb="$1" out code
  out="$(ctl "$verb" pod "$POD")"
  code=$?
  if [ "$code" != 0 ] && printf '%s' "$out" | grep -qiE 'api key|apikey|credential|unauthori'; then
    configure_legacy
    out="$(ctl "$verb" pod "$POD")"
    code=$?
  fi
  say "[B runpodctl $verb pod] exit $code: $(printf '%s' "$out" | brief)"
  [ "$code" = 0 ] && ! printf '%s' "$out" | grep -qiE '^error|"error"'
}
method_c() {
  local out
  if [ "$1" = probe ]; then
    out="$(graphql "query { pod(input: {podId: \\\"$POD\\\"}) { id desiredStatus } }")"
  else
    out="$(graphql "mutation { podStop(input: {podId: \\\"$POD\\\"}) { id desiredStatus } }")"
  fi
  say "[C graphql $1] $(printf '%s' "$out" | brief)"
  graphql_ok "$out"
}
method_d() {
  local out
  if [ "$1" = probe ]; then
    out="$(rest GET "/pods/$POD")"
  else
    out="$(rest POST "/pods/$POD/stop")"
  fi
  say "[D rest $1] $(printf '%s' "$out" | brief)"
  printf '%s' "$out" | grep -Eq '(^|[^0-9])20[0-9]$'
}

if [ "$MODE" = probe ]; then
  passed=""
  if have_runpodctl; then
    method_a get && passed="$passed A"
    method_b get && passed="$passed B"
  fi
  method_c probe && passed="$passed C"
  method_d probe && passed="$passed D"
  if [ -n "$passed" ]; then
    say "PROBE PASS: the pod's own key authenticates through:$passed"
    exit 0
  fi
  say "PROBE FAIL: no method authenticated; the pod cannot stop itself (do not start the sweep)"
  exit 1
fi

say "STOPPING pod $POD (stop, not terminate)"
if have_runpodctl; then
  method_a stop && {
    say "STOPPED via A (runpodctl pod stop)"
    exit 0
  }
  method_b stop && {
    say "STOPPED via B (runpodctl stop pod)"
    exit 0
  }
fi
method_c stop && {
  say "STOPPED via C (GraphQL podStop)"
  exit 0
}
method_d stop && {
  say "STOPPED via D (REST POST /pods/$POD/stop)"
  exit 0
}
say "FAILED: every method failed; the pod is still running and billing"
exit 1
