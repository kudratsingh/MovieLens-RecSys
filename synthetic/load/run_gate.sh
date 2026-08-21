#!/bin/sh
# Run one authenticated k6 SLO window, with evidence, and at most one honest repeat.
#
# The gate's verdict is k6's exit status and nothing else: no threshold, arrival
# rate, or workload is decided here. What this wrapper adds is the ability to
# tell a breach apart from a measurement taken while the machine was not being
# scheduled. It samples /proc/stat for the whole window, joins CPU steal against
# the per-second latency buckets, and applies exactly one rule:
#
#   re-measure  <=>  p99 breached  AND  the seconds that breached line up with
#                    the host being preempted (see summarize.py's constants)
#
# A breach without preemption underneath it is the service's fault and fails
# immediately. A re-measured window is final whatever its steal looks like, so
# this can never loop, and the second window reuses the warm stack so it is a
# repeat of the same measurement rather than a different one.
#
# Inputs (all set by the Makefile):
#   DEMO_COMPOSE        the compose invocation, word-split on purpose
#   LOAD_PROFILE        smoke | nightly
#   LOAD_RESULTS_DIR    host directory for this run's evidence
#   API_LOAD_WORKERS    uvicorn workers on api-load; the k6 warm-up sizes off it
#   K6_VERSION, K6_PUSH_INTERVAL
set -eu

: "${DEMO_COMPOSE:?DEMO_COMPOSE must be set}"
: "${LOAD_PROFILE:=smoke}"
: "${LOAD_RESULTS_DIR:=./artifacts/load-smoke}"
: "${API_LOAD_WORKERS:=4}"
: "${K6_PUSH_INTERVAL:=2m}"
: "${K6_VERSION:=}"
PYTHON="${PYTHON:-python3}"

export K6_VERSION LOAD_PROFILE LOAD_RESULTS_DIR API_LOAD_WORKERS
export K6_PROMETHEUS_RW_PUSH_INTERVAL="$K6_PUSH_INTERVAL"

# Prometheus is a nightly-trend feature (ADR 0010). The 60-second smoke keeps it
# out of the process list entirely and writes its evidence to the artifact.
if [ "$LOAD_PROFILE" = "nightly" ]; then
	export K6_OUTPUTS_BASE="experimental-prometheus-rw,json="
else
	export K6_OUTPUTS_BASE="json="
fi

# Start from an empty directory so a stale window-2 from a previous run cannot
# be mistaken for this one's evidence. Guarded: this deletes a tree.
case "$LOAD_RESULTS_DIR" in
	./artifacts/* | artifacts/*) rm -rf "$LOAD_RESULTS_DIR" ;;
	*)
		echo "refusing to clear $LOAD_RESULTS_DIR (expected a path under ./artifacts/)" >&2
		exit 2
		;;
esac
mkdir -p "$LOAD_RESULTS_DIR"
# k6 runs as an unprivileged user inside its container and has to write here.
chmod 0777 "$LOAD_RESULTS_DIR"

# Recreating these three is what gives every run the same process-cache
# boundary; the k6 warm-up then pays that boundary off before measuring.
# shellcheck disable=SC2086
$DEMO_COMPOSE --profile load up -d --force-recreate --wait --wait-timeout 120 \
	feature-server model-server api-load
if [ "$LOAD_PROFILE" = "nightly" ]; then
	# shellcheck disable=SC2086
	$DEMO_COMPOSE --profile load up -d --wait --wait-timeout 120 prometheus
fi

capture() {
	when="$1"
	# shellcheck disable=SC2086
	containers=$($DEMO_COMPOSE --profile load ps -q 2>/dev/null || true)
	if [ -n "$containers" ]; then
		# Snapshots, not polling: repeated `docker stats` during a run
		# measurably perturbs the run it is describing.
		# shellcheck disable=SC2086
		docker stats --no-stream \
			--format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $containers \
			> "$LOAD_RESULTS_DIR/docker-stats-$when.txt" 2>&1 || true
		# shellcheck disable=SC2086
		docker inspect \
			--format '{{.Name}} CpuShares={{.HostConfig.CpuShares}} NanoCpus={{.HostConfig.NanoCpus}}' \
			$containers >> "$LOAD_RESULTS_DIR/docker-stats-$when.txt" 2>&1 || true
	else
		echo "no load-profile containers running" > "$LOAD_RESULTS_DIR/docker-stats-$when.txt"
	fi
	: > "$LOAD_RESULTS_DIR/cpu-stat-$when.txt"
	for service in api-load model-server feature-server; do
		# shellcheck disable=SC2086
		container=$($DEMO_COMPOSE --profile load ps -q "$service" 2>/dev/null || true)
		{
			echo "== $service"
			if [ -n "$container" ]; then
				docker exec "$container" cat /sys/fs/cgroup/cpu.stat 2>/dev/null \
					|| echo "cpu.stat unavailable"
			else
				echo "not running"
			fi
		} >> "$LOAD_RESULTS_DIR/cpu-stat-$when.txt"
	done
}

# One measured window. Everything that distinguishes window 1 from window 2 is
# the directory it writes into; the workload is byte-identical.
run_window() {
	window="$1"
	window_dir="$LOAD_RESULTS_DIR/$window"
	mkdir -p "$window_dir"
	chmod 0777 "$window_dir"

	# On a Linux runner this reads /proc/stat directly. On Docker Desktop there
	# is no host /proc, so it falls back to reading it from inside a container,
	# which reports the Linux VM's kernel — the layer that gets preempted there.
	# shellcheck disable=SC2086
	probe_container=$($DEMO_COMPOSE --profile load ps -q postgres 2>/dev/null || true)
	"$PYTHON" synthetic/load/probe_host_cpu.py \
		--output "$window_dir/host-cpu.jsonl" \
		--container "$probe_container" &
	probe_pid=$!

	export K6_RESULTS_DIR="/results/$window"
	export K6_OUTPUTS="${K6_OUTPUTS_BASE}/results/$window/raw-metrics.json.gz"
	# `set -o pipefail` is not portable to dash, so k6's status is written from
	# inside the subshell rather than read off the pipeline.
	# shellcheck disable=SC2086
	# `set +e` inside the subshell matters: errexit is inherited, and without it
	# a breached threshold would abort the subshell before it records why.
	( set +e; $DEMO_COMPOSE --profile load run --rm k6; echo $? > "$window_dir/k6-exit" ) 2>&1 \
		| tee "$window_dir/k6-stdout.txt"
	kill "$probe_pid" 2>/dev/null || true
	wait "$probe_pid" 2>/dev/null || true
	return 0
}

capture before
run_window window-1
capture after

WINDOW_1_STATUS=$(cat "$LOAD_RESULTS_DIR/window-1/k6-exit")
"$PYTHON" synthetic/load/summarize.py "$LOAD_RESULTS_DIR/window-1" \
	| tee "$LOAD_RESULTS_DIR/window-1/breakdown.txt"

if [ "$WINDOW_1_STATUS" -ne 0 ] && grep -qx "REMEASURE=yes" "$LOAD_RESULTS_DIR/window-1/breakdown.txt"; then
	echo
	echo "=== re-measuring: window 1 breached while the host was being preempted ==="
	echo "=== the stack stays up and warm; window 2's verdict is final ==="
	run_window window-2
	capture after-window-2
	WINDOW_2_STATUS=$(cat "$LOAD_RESULTS_DIR/window-2/k6-exit")
	"$PYTHON" synthetic/load/summarize.py "$LOAD_RESULTS_DIR/window-2" --final \
		| tee "$LOAD_RESULTS_DIR/window-2/breakdown.txt"
	echo
	echo "gate verdict: window 1 exit $WINDOW_1_STATUS (re-measured), window 2 exit $WINDOW_2_STATUS (final)"
	exit "$WINDOW_2_STATUS"
fi

echo
echo "gate verdict: window 1 exit $WINDOW_1_STATUS (not re-measured)"
exit "$WINDOW_1_STATUS"
