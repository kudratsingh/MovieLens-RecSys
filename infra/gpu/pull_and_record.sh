#!/usr/bin/env bash
#
# WO-5 step one, back on the Mac: check a timing tarball, unpack it, record it.
#
#   infra/gpu/pull_and_record.sh <tarball> [--sha256 HEX] [--label rented-gpu|local-smoke]
#       [--provider NAME] [--instance TEXT] [--rate-usd-per-hour N]
#       [--tracking-uri http://localhost:5001] [--experiment phase-a-sasrec]
#
# The tarball is whatever infra/gpu/bootstrap.sh printed after "PULL:", already
# copied to this machine by any means (runpodctl, scp, a browser download); this
# script never talks to the pod or to RunPod. Its SHA-256 is checked against
# <tarball>.sha256 beside it, or against --sha256 (the line the pod printed),
# before anything is unpacked.
#
# Then, because a timing is a run under non-negotiable 12 as far as it applies
# (there is no model, no per-user file and no archive to keep):
#
#   1. unpack into artifacts/wo5-timing/<UTC timestamp>/ and check the
#      MANIFEST.sha256 the pod wrote inside;
#   2. refuse anything that looks like a credential in the text it carries;
#   3. log ONE run into the shared MLflow store, experiment phase-a-sasrec,
#      tagged timing_only=true and not_a_result_of_record=true with the provider,
#      instance, GPU, driver and hourly rate, every result file, log and cells
#      file attached, and read it back through the REST API;
#   4. print, without running them, the backup commands: a fresh pg_dump of the
#      mlflow database and the tarball, into ~/movielens-backups.
#
# --label defaults to the session's own run kind. --rate-usd-per-hour has no
# default for a rented session: the rate is whatever the console showed.
set -Eeuo pipefail

REPO="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
TRACKING_URI="http://localhost:5001"
EXPERIMENT="phase-a-sasrec"
BACKUPS="${MOVIELENS_BACKUPS:-$HOME/movielens-backups}"
TARBALL=""
EXPECTED_SHA=""
LABEL=""
PROVIDER=""
INSTANCE=""
RATE=""

die() {
  printf 'pull_and_record: %s\n' "$*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
  --sha256) EXPECTED_SHA="${2:?}" && shift 2 ;;
  --label) LABEL="${2:?}" && shift 2 ;;
  --provider) PROVIDER="${2:?}" && shift 2 ;;
  --instance) INSTANCE="${2:?}" && shift 2 ;;
  --rate-usd-per-hour) RATE="${2:?}" && shift 2 ;;
  --tracking-uri) TRACKING_URI="${2:?}" && shift 2 ;;
  --experiment) EXPERIMENT="${2:?}" && shift 2 ;;
  -h | --help)
    sed -n '3,30p' "$0"
    exit 0
    ;;
  -*) die "unknown option $1" ;;
  *)
    [ -z "$TARBALL" ] || die "one tarball at a time"
    TARBALL="$1" && shift
    ;;
  esac
done
[ -n "$TARBALL" ] || die "usage: $0 <tarball> [options]; see --help"
[ -f "$TARBALL" ] || die "$TARBALL does not exist"
[ -x "$PYTHON" ] || die "no Python at $PYTHON; set PYTHON to the repository's venv interpreter"
TARBALL="$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL")"
NAME="$(basename "$TARBALL")"

# --- the tarball is the one the pod wrote ----------------------------------------
actual="$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)"
if [ -z "$EXPECTED_SHA" ]; then
  [ -f "$TARBALL.sha256" ] || die "no $NAME.sha256 beside it and no --sha256; copy the .sha256 too, or pass the hash the pod printed"
  EXPECTED_SHA="$(cut -d' ' -f1 "$TARBALL.sha256")"
fi
[ "$actual" = "$EXPECTED_SHA" ] || die "SHA-256 mismatch: $NAME is $actual, expected $EXPECTED_SHA"
echo "sha256 ok: $actual"

# --- unpack and check what is inside ----------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$REPO/artifacts/wo5-timing/$STAMP"
mkdir -p "$DEST"
tar -xzf "$TARBALL" -C "$DEST"
SESSION_DIR="$(find "$DEST" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$SESSION_DIR" ] && [ -f "$SESSION_DIR/MANIFEST.sha256" ] || die "$NAME holds no session directory with a MANIFEST.sha256"
(cd "$SESSION_DIR" && shasum -a 256 -c MANIFEST.sha256 >/dev/null) || die "a file inside $NAME does not match its MANIFEST.sha256"
cp "$TARBALL" "$DEST/"
printf '%s  %s\n' "$actual" "$NAME" >"$DEST/$NAME.sha256"
echo "unpacked: $SESSION_DIR ($(find "$SESSION_DIR" -type f | wc -l | tr -d ' ') files, manifest ok)"

# The tarball goes into MLflow and a backup repository; nothing credential-shaped
# may ride along. The pod already drops RUNPOD_* names that look like keys.
if grep -rIniE '(api[_-]?key|secret|passw(or)?d|bearer|token)[[:space:]]*[:=]' "$SESSION_DIR" >"$DEST/credential-scan.txt"; then
  cat "$DEST/credential-scan.txt" >&2
  die "credential-shaped text in the session (above); nothing was recorded"
fi
rm -f "$DEST/credential-scan.txt"
echo "credential scan: clean"

fact() { sed -n "s/^$1=//p" "$SESSION_DIR/env/bootstrap-facts.env" | tail -n 1; }
RUN_KIND="$(fact RUN_KIND)"
[ -n "$LABEL" ] || LABEL="${RUN_KIND:-unknown}"
case "$LABEL" in
rented-gpu)
  [ -n "$PROVIDER" ] || PROVIDER="runpod"
  [ -n "$INSTANCE" ] || INSTANCE="secure-cloud 1x $(fact GPU_NAME) on-demand"
  [ -n "$RATE" ] || die "a rented session needs --rate-usd-per-hour (the rate the console showed)"
  ;;
*)
  [ -n "$PROVIDER" ] || PROVIDER="local"
  [ -n "$INSTANCE" ] || INSTANCE="$(uname -m) $(fact DEVICE)"
  [ -n "$RATE" ] || RATE="0"
  ;;
esac

# --- one MLflow run, read back -------------------------------------------------------
curl -fsS -m 10 "$TRACKING_URI/health" >/dev/null ||
  die "no MLflow at $TRACKING_URI (start the dev stack's mlflow service, then rerun; the unpacked files stay in $DEST)"
(cd "$REPO" && PYTHONPATH="$REPO" "$PYTHON" -m src.training.sasrec_timing record "$SESSION_DIR" \
  --tracking-uri "$TRACKING_URI" --experiment "$EXPERIMENT" --label "$LABEL" \
  --provider "$PROVIDER" --instance "$INSTANCE" --rate-usd-per-hour "$RATE" \
  --tarball "$DEST/$NAME" --out "$DEST/mlflow-run.json") || die "the MLflow record failed; see above"
RUN_ID="$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["run_id"])' "$DEST/mlflow-run.json")"

# --- the backup, printed for the owner to run ------------------------------------------
DATE="$(date -u +%Y%m%dT%H%M%SZ)"
cat <<EOF

Recorded: run $RUN_ID in experiment $EXPERIMENT at $TRACKING_URI ($LABEL).
Files: $DEST

Backup (non-negotiable 12) -- run these yourself, in order, then report the commit:

  cd $REPO && docker compose -p movielens-coldstart exec -T postgres \\
    pg_dump -U recsys -d mlflow | gzip > $BACKUPS/mlflow-post-wo5-timing-$DATE.sql.gz
  gzip -t $BACKUPS/mlflow-post-wo5-timing-$DATE.sql.gz
  cp "$DEST/$NAME" "$DEST/$NAME.sha256" $BACKUPS/
  cd $BACKUPS && git add mlflow-post-wo5-timing-$DATE.sql.gz "$NAME" "$NAME.sha256" \\
    && git commit -m "WO-5 step one: timing session $NAME (run $RUN_ID) and the mlflow dump after it" \\
    && git push && git rev-parse HEAD
EOF
