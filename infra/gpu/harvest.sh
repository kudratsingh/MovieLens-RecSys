#!/usr/bin/env bash
#
# WO-5 step two, on the Mac: bring every sweep bundle home, record it, re-score it,
# back it up, and tell the pod (src/training/wo5_harvest.py does the work).
#
#   infra/gpu/harvest.sh install --host <pod ip> --port <ssh port> [--key ~/.ssh/id_ed25519]
#   infra/gpu/harvest.sh status
#   infra/gpu/harvest.sh uninstall
#   infra/gpu/harvest.sh run|once <the same arguments as install>
#
# `install` writes ~/Library/LaunchAgents/io.movielens.wo5-harvest.plist and loads it:
# launchd runs `harvest.sh run ...` under `caffeinate -i` (KeepAlive, RunAtLoad), so it
# survives a closed terminal, a Claude session ending and a crash, and starts again at
# login. When the pod's INDEX has ended (DONE or HALT) and every bundle is home and
# acknowledged, it removes its own LaunchAgent; `uninstall` does the same by hand.
#
# Run from the main checkout: the CPU re-score reads data/raw/ml-25m and the cold-start
# cohort parquet from it. Other options: --interval (seconds, default 300), --dest
# (default artifacts/wo5-sweep), --backup-repo (default ~/movielens-backups), --no-push,
# --tracking-uri (default http://localhost:5001), --local-dir (a rehearsal's sweep root
# instead of --host), --label (default io.movielens.wo5-harvest). The runbook is
# docs/model-planning/experiments/wo5-sweep-runbook.md.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
case "${1:-}" in
install | uninstall | status | run | once | rescore) ;;
*)
  sed -n '6,9p' "$0" >&2
  exit 64
  ;;
esac
[ -x "$PYTHON" ] || {
  echo "no Python at $PYTHON; set PYTHON to the repository's venv interpreter" >&2
  exit 1
}
export PYTHONPATH="$REPO"
# One thread, and the pair of OpenMP runtimes the Mac's venv carries (the repository's
# tests and runs set the same).
export OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE
cd "$REPO"
exec "$PYTHON" -m src.training.wo5_harvest "$@"
