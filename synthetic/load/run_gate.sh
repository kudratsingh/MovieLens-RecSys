#!/bin/sh
# Run one authenticated k6 window, with evidence, and at most one honest repeat.
#
# The gate's verdict comes from k6 and nothing else: no threshold, arrival rate,
# or workload is decided here. What this wrapper adds is the ability to tell a
# breach apart from a measurement taken while the machine was not being
# scheduled. It samples /proc/stat for the whole window, joins CPU steal against
# the per-second latency buckets, and applies exactly one rule:
#
#   re-measure  <=>  latency breached  AND  the seconds that breached line up
#                    with the host being preempted (see summarize.py's constants)
#
# A breach without preemption underneath it is the service's fault and fails
# immediately. A re-measured window is final whatever its steal looks like, so
# this can never loop, and the second window reuses the warm stack so it is a
# repeat of the same measurement rather than a different one.
#
# Alongside CPU it collects two more kinds of evidence, both informational and
# neither feeding the rule above:
#
#   disk-fsync.jsonl   fdatasync latency on Postgres's own volume
#                      (probe_disk_fsync.py). Every commit costs one fdatasync,
#                      so a stalling device shows up in every traffic class
#                      including the cold path — which is what the failing runs
#                      look like. A burst before the window by default;
#                      LOAD_FSYNC_PROBE=on adds per-second sampling *during* it,
#                      which is diagnostic only because it perturbs the gate.
#   server-side.json   the audit rows for the window and Postgres's WAL/IO
#                      counters from either side of it (server_side.py). The
#                      audit's latency_ms is timed around the handler only, so
#                      k6 minus handler is the share spent outside it.
#
# It also records which medium Postgres's data directory was on, so an evidence
# directory says what it measured rather than leaving that to be inferred from
# the job that produced it. CI layers docker-compose.ci-load.yml and gets tmpfs;
# a laptop run gets Docker's volume driver unless that file is passed too.
#
# Two workloads use this wrapper:
#
#   recommendations  the pinned p99 gate (non-negotiables #4/#11). k6's exit
#                    status is the verdict, unchanged.
#   pages            the page-shaped workloads in pages.js. Correctness always
#                    fails the gate; the per-step latency budgets can be
#                    reported instead (LOAD_LATENCY_ENFORCED=false) while they
#                    are still new. summarize.py turns that into a GATE verdict.
#
# Inputs (all set by the Makefile):
#   DEMO_COMPOSE           the compose invocation, word-split on purpose
#   LOAD_PROFILE           smoke | nightly
#   LOAD_SCRIPT            the k6 entry point inside the container
#   LOAD_WORKLOAD          recommendations | pages
#   LOAD_LATENCY_ENFORCED  true | false (pages only)
#   LOAD_RESULTS_DIR       host directory for this run's evidence
#   API_LOAD_WORKERS       uvicorn workers on api-load; the k6 warm-up sizes off it
#   K6_VERSION, K6_PUSH_INTERVAL
set -eu

: "${DEMO_COMPOSE:?DEMO_COMPOSE must be set}"
: "${LOAD_PROFILE:=smoke}"
: "${LOAD_SCRIPT:=/scripts/recommendations.js}"
: "${LOAD_WORKLOAD:=recommendations}"
: "${LOAD_LATENCY_ENFORCED:=true}"
: "${LOAD_RESULTS_DIR:=./artifacts/load-smoke}"
: "${API_LOAD_WORKERS:=4}"
: "${K6_PUSH_INTERVAL:=2m}"
: "${K6_VERSION:=}"
# The image the disk probe runs from. It carries src/ and synthetic/ already,
# so the probe needs no image of its own.
: "${LOAD_PROBE_IMAGE:=movielens-recsys/api:demo}"
# Inside Postgres's data directory on purpose: same filesystem as the WAL, and
# a dotfile Postgres itself never looks at. Removed by the probe on exit.
: "${FSYNC_PROBE_PATH:=/var/lib/postgresql/data/.load-gate-fsync-probe}"
# off (default): one baseline burst before the window opens, which costs the
#   measurement nothing and still records what the device was doing beforehand.
# on: sample throughout the window too, for the per-second fsync column.
#   Opt-in because an fdatasync is a *device* cache flush, not a per-file one:
#   measured on a Docker Desktop host, sampling every 250 ms moved this gate's
#   p95 from 10.57 ms to 47.71 ms and its p99 from 29.64 ms to 124.74 ms — the
#   probe measuring itself. Turn it on deliberately, on a runner whose result
#   is being investigated rather than gated. See probe_disk_fsync.py.
: "${LOAD_FSYNC_PROBE:=off}"
PYTHON="${PYTHON:-python3}"

export K6_VERSION LOAD_PROFILE LOAD_RESULTS_DIR API_LOAD_WORKERS LOAD_SCRIPT
export K6_PROMETHEUS_RW_PUSH_INTERVAL="$K6_PUSH_INTERVAL"

# Prometheus is a nightly-trend feature (ADR 0010). The short smoke keeps it
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

# Recorded before anything is recreated, so the artifact says when the gate as
# a whole started. Each window records its own start separately and *that* is
# what bounds the audit export — otherwise a re-measured run would export
# window 1's rows into window 2's evidence.
GATE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$GATE_STARTED_AT" > "$LOAD_RESULTS_DIR/gate-started-at.txt"

# Recreating these three is what gives every run the same process-cache
# boundary; the k6 warm-up then pays that boundary off before measuring.
# shellcheck disable=SC2086
$DEMO_COMPOSE --profile load up -d --force-recreate --wait --wait-timeout 120 \
	feature-server model-server api-load
if [ "$LOAD_PROFILE" = "nightly" ]; then
	# shellcheck disable=SC2086
	$DEMO_COMPOSE --profile load up -d --wait --wait-timeout 120 prometheus
fi

# Host memory, before and after the window. Purely evidence: nothing here feeds
# a threshold or the re-measure rule, both of which stay exactly as ADR 0010
# defines them.
#
# The gate already records CPU steal, which is what catches a preempted runner.
# Memory is the blind spot beside it. A host deep in swap or compressing hard
# serves the same requests more slowly, and it does so without moving steal at
# all -- so the run comes back looking clean and the tail looks like the
# service. That is precisely the confusion this file exists to prevent, so the
# numbers go in the evidence directory and a reader can decide whether the
# machine was fit to be measured.
#
# macOS and Linux answer this differently and neither is guaranteed present, so
# every branch is best-effort and the file always gets written.
host_memory() {
	target="$1"
	{
		echo "== host memory"
		if [ -r /proc/meminfo ]; then
			# Linux, which is what CI runs. MemAvailable is the honest one:
			# MemFree omits reclaimable page cache and reads alarmingly low on
			# a perfectly healthy machine.
			grep -E '^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Dirty|Writeback):' \
				/proc/meminfo 2>/dev/null || echo "meminfo unreadable"
		elif command -v vm_stat >/dev/null 2>&1; then
			# macOS, which is where local runs happen. `Pages occupied by
			# compressor` is the one to read: on Apple silicon it grows long
			# before swap does, so swap alone understates the pressure.
			page_size=$(sysctl -n hw.pagesize 2>/dev/null || echo 16384)
			echo "page_size_bytes=$page_size"
			vm_stat 2>/dev/null | grep -E 'Pages (free|active|inactive|wired down|occupied by compressor)' \
				|| echo "vm_stat unavailable"
			sysctl -n vm.swapusage 2>/dev/null || echo "swapusage unavailable"
		else
			echo "no supported memory source on this host"
		fi
	} > "$target" 2>&1 || true
}

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
	host_memory "$LOAD_RESULTS_DIR/host-memory-$when.txt"
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

# Which medium Postgres's data directory is on for this run: `yes` for tmpfs,
# `no` for anything else, `unknown` when there is no container to ask.
#
# Every request commits a durable audit row before it answers, so the device
# under the WAL is inside every percentile this gate reports. The CI load job
# takes the runner's disk out of the measurement by layering
# docker-compose.ci-load.yml (see ADR 0010's 2026-08-28 note); nothing else
# does. Reading it back from the container rather than from the caller's
# intent is what lets the breakdown state which of the two it measured.
pgdata_storage() {
	# shellcheck disable=SC2086
	container=$($DEMO_COMPOSE --profile load ps -q postgres 2>/dev/null || true)
	if [ -z "$container" ]; then
		echo unknown
		return 0
	fi
	# Both spellings, because Compose has two: a long-syntax `type: tmpfs`
	# entry shows up in .Mounts, while the short `tmpfs:` key lands only in
	# HostConfig.Tmpfs. Ranging over a nil map yields nothing, so the second
	# half is silent when the first one answered.
	found=$(docker inspect --format \
		'{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}} {{end}}{{end}}{{range $target, $opts := .HostConfig.Tmpfs}}{{if eq $target "/var/lib/postgresql/data"}}tmpfs {{end}}{{end}}' \
		"$container" 2>/dev/null || true)
	case "$found" in
		*tmpfs*) echo yes ;;
		"") echo unknown ;;
		*) echo no ;;
	esac
}

# fdatasync latency on the filesystem Postgres commits to. Runs in a throwaway
# container rather than on the host because the volume is Compose-managed:
# `--volumes-from` inherits postgres's own mounts, so the probe file is
# guaranteed to land on the same filesystem as the WAL without this script
# having to reconstruct the project-prefixed volume name the Makefile chose.
#
# Two shapes, selected by LOAD_FSYNC_PROBE. `--once` runs the baseline burst
# and exits, so it can run in the foreground before the window opens; the
# continuous form runs detached and is stopped afterwards. Failure to start is
# never fatal — the window runs without the evidence and summarize.py reports
# it as absent.
start_fsync_probe() {
	window_dir="$1"
	fsync_probe=""
	# shellcheck disable=SC2086
	postgres_container=$($DEMO_COMPOSE --profile load ps -q postgres 2>/dev/null || true)
	if [ -z "$postgres_container" ]; then
		echo "[fsync-probe] no postgres container; skipping disk evidence" >&2
		return 0
	fi
	abs_window_dir=$(cd "$window_dir" && pwd)
	if [ "$LOAD_FSYNC_PROBE" = "on" ]; then
		fsync_probe=$(run_fsync_probe "$postgres_container" "$abs_window_dir" -d) \
			|| fsync_probe=""
		if [ -z "$fsync_probe" ]; then
			echo "[fsync-probe] could not start; see $LOAD_RESULTS_DIR/fsync-probe.log" >&2
		fi
		return 0
	fi
	run_fsync_probe "$postgres_container" "$abs_window_dir" --once > /dev/null \
		|| echo "[fsync-probe] baseline burst failed; see $LOAD_RESULTS_DIR/fsync-probe.log" >&2
}

# uid 999 is the postgres image's own user, which is what can write inside the
# data directory. The trailing argument is either `-d` (detached, continuous)
# or `--once` (foreground, burst only); both are placed where they belong
# because docker's flags precede the image and the probe's follow it.
run_fsync_probe() {
	container="$1"
	results="$2"
	mode="$3"
	detach=""
	probe_mode=""
	if [ "$mode" = "-d" ]; then
		detach="-d"
	else
		probe_mode="$mode"
	fi
	# stdout is the container id when detached, so only stderr can be logged —
	# and it has to go somewhere readable, because "the probe did not run" is
	# otherwise indistinguishable from "the probe found nothing".
	# shellcheck disable=SC2086
	docker run --rm $detach --user 999 \
		--volumes-from "$container" \
		-v "$results:/results" \
		"$LOAD_PROBE_IMAGE" \
		python -m synthetic.load.probe_disk_fsync \
		--path "$FSYNC_PROBE_PATH" \
		--output /results/disk-fsync.jsonl $probe_mode 2>> "$LOAD_RESULTS_DIR/fsync-probe.log"
}

stop_fsync_probe() {
	if [ -n "${fsync_probe:-}" ]; then
		# SIGTERM, which the probe handles so it removes its file before exit.
		docker stop -t 10 "$fsync_probe" > /dev/null 2>&1 || true
		fsync_probe=""
	fi
}

# One server-side capture: `--snapshot-only` for the before side, `--since` for
# the export. Runs through the demo-setup service because that is the container
# already configured with the admin DSN. A failure leaves no file behind rather
# than a half-written one, and summarize.py reads a missing file as "n/a".
server_side() {
	target="$1"
	shift
	# shellcheck disable=SC2086
	if $DEMO_COMPOSE --profile load run --rm -T demo-setup \
		python -m synthetic.load.server_side "$@" \
		> "$target" 2>> "$LOAD_RESULTS_DIR/server-side.log"; then
		return 0
	fi
	echo "[server-side] capture failed; see $LOAD_RESULTS_DIR/server-side.log" >&2
	rm -f "$target"
}

# One measured window. Everything that distinguishes window 1 from window 2 is
# the directory it writes into; the workload is byte-identical.
run_window() {
	window="$1"
	window_dir="$LOAD_RESULTS_DIR/$window"
	mkdir -p "$window_dir"
	chmod 0777 "$window_dir"

	# Postgres's counters are cumulative, so only a pair of snapshots says what
	# this window cost. Taken before the probes start so the setup container's
	# own IO is not attributed to the window.
	server_side "$window_dir/server-side-before.json" --snapshot-only

	# Before the window on purpose: in the default `--once` shape this is a
	# burst of syncs, and a burst belongs outside the measurement.
	start_fsync_probe "$window_dir"

	window_started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
	echo "$window_started_at" > "$window_dir/started-at.txt"

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
	stop_fsync_probe
	server_side "$window_dir/server-side.json" --since "$window_started_at"
	return 0
}

# Summarize one window and leave `GATE=pass|fail` and `REMEASURE=yes|no` in its
# breakdown for the caller to grep.
summarize_window() {
	window_dir="$1"
	shift
	k6_exit=$(cat "$window_dir/k6-exit")
	if [ "$LOAD_WORKLOAD" = "pages" ] && [ "$LOAD_LATENCY_ENFORCED" != "true" ]; then
		advisory="--advisory-latency"
	else
		advisory=""
	fi
	# shellcheck disable=SC2086
	"$PYTHON" synthetic/load/summarize.py "$window_dir" \
		--workload "$LOAD_WORKLOAD" --k6-exit "$k6_exit" $advisory \
		--postgres-data-on-tmpfs "$PGDATA_STORAGE" \
		--disk-fsync "$window_dir/disk-fsync.jsonl" \
		--server-side "$window_dir/server-side.json" \
		--server-side-before "$window_dir/server-side-before.json" "$@" \
		| tee "$window_dir/breakdown.txt"
}

verdict_of() {
	if grep -qx "GATE=pass" "$1/breakdown.txt"; then
		echo 0
	else
		echo 1
	fi
}

# Read once: postgres is not recreated between windows, and both windows are
# summarized against the same fact.
PGDATA_STORAGE=$(pgdata_storage)
echo "$PGDATA_STORAGE" > "$LOAD_RESULTS_DIR/postgres-storage.txt"

capture before
run_window window-1
capture after

WINDOW_1_K6=$(cat "$LOAD_RESULTS_DIR/window-1/k6-exit")
summarize_window "$LOAD_RESULTS_DIR/window-1"
WINDOW_1_VERDICT=$(verdict_of "$LOAD_RESULTS_DIR/window-1")

if [ "$WINDOW_1_VERDICT" -ne 0 ] && grep -qx "REMEASURE=yes" "$LOAD_RESULTS_DIR/window-1/breakdown.txt"; then
	echo
	echo "=== re-measuring: window 1 breached while the host was being preempted ==="
	echo "=== the stack stays up and warm; window 2's verdict is final ==="
	run_window window-2
	capture after-window-2
	summarize_window "$LOAD_RESULTS_DIR/window-2" --final
	WINDOW_2_VERDICT=$(verdict_of "$LOAD_RESULTS_DIR/window-2")
	WINDOW_2_K6=$(cat "$LOAD_RESULTS_DIR/window-2/k6-exit")
	echo
	echo "gate verdict: window 1 k6 exit $WINDOW_1_K6 (re-measured), window 2 k6 exit $WINDOW_2_K6 (final)"
	exit "$WINDOW_2_VERDICT"
fi

echo
echo "gate verdict: window 1 k6 exit $WINDOW_1_K6 (not re-measured)"
exit "$WINDOW_1_VERDICT"
