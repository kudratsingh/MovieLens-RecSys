#!/bin/sh
# R-12: prove that rolling back to an older image does not fail on the
# database it is rolling back to.
#
# The failure this exists to catch is the single most likely 02:00 incident in
# the design. A pre-deploy command runs on *every* deploy, a rollback included,
# and an older image's `alembic upgrade head` against a database carrying a
# revision that image has never heard of raises "Can't locate revision" -- so
# the rollback that was supposed to end an incident fails and becomes a second
# one. `src.release.bootstrap schema` is written to compare revisions and apply
# nothing when the database is ahead; this script is what proves it does.
#
# It runs inside the API image, on the production-mode Compose stack, and it
# stands an older image up the only honest way available: by taking this
# image's own migration tree and deleting the newest revision from a copy. The
# copy is what the schema step is pointed at, so what it sees is exactly what
# an image built one migration ago would see -- a database whose recorded
# revision is not in its script directory.
#
#   docker compose -f docker-compose.prod.yml run --rm rollback-rehearsal
#   docker compose -f docker-compose.prod.yml run --rm rollback-rehearsal --dry-run
#
# --dry-run prints the plan and exits without connecting to anything, so the
# script can be checked in CI and read by someone who has no stack up.
#
# Nothing here writes to the database. The schema step is expected to decline
# to act; if it acts, that is the finding and the script fails.
set -eu

DRY_RUN=false
WORK_DIR="${ROLLBACK_WORK_DIR:-/tmp/rollback-rehearsal}"
TREE_ROOT="${ROLLBACK_TREE_ROOT:-/app}"

usage() {
    cat <<'USAGE'
usage: rollback-rehearsal.sh [--dry-run]

  --dry-run   Print what the rehearsal would do and exit 0 without opening a
              database connection or copying anything.

Environment:
  ROLLBACK_TREE_ROOT   where alembic.ini and alembic/ live (default /app)
  ROLLBACK_WORK_DIR    scratch directory for the pruned tree
                       (default /tmp/rollback-rehearsal)
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
    --dry-run)
        DRY_RUN=true
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "rollback-rehearsal: unknown argument '$1'" >&2
        usage >&2
        exit 2
        ;;
    esac
done

if [ "$DRY_RUN" = true ]; then
    cat <<DRYRUN
rollback-rehearsal (dry run) would:
  1. copy ${TREE_ROOT}/alembic.ini and ${TREE_ROOT}/alembic into ${WORK_DIR}
  2. delete the newest revision file from the copy, so the pruned tree is what
     the image one migration older shipped
  3. refuse to continue unless the database records a revision the pruned tree
     does not know -- without that there is no rollback to rehearse
  4. run src.release.bootstrap's schema step against the pruned tree and
     require that it applied nothing and reported the database as ahead
  5. print ROLLBACK-REHEARSAL-OK
Nothing was copied and no connection was opened.
DRYRUN
    exit 0
fi

[ -f "${TREE_ROOT}/alembic.ini" ] || {
    echo "rollback-rehearsal: no alembic.ini under ${TREE_ROOT}; this has to run in the API image" >&2
    exit 1
}

rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"
cp "${TREE_ROOT}/alembic.ini" "${WORK_DIR}/alembic.ini"
cp -R "${TREE_ROOT}/alembic" "${WORK_DIR}/alembic"

# The shell's own glob expansion is sorted, and the revision files are named
# with their zero-padded sequence number, so the last match is the newest.
# Parsing `ls` would be the obvious way and is the wrong one.
newest=""
for candidate in "${WORK_DIR}"/alembic/versions/*.py; do
    newest="${candidate}"
done
[ -f "${newest}" ] || {
    echo "rollback-rehearsal: found no migration files under ${WORK_DIR}/alembic/versions" >&2
    exit 1
}

echo "rollback-rehearsal: pruning $(basename "${newest}") -- the migration the older image does not carry" >&2
rm "${newest}"

python - "${WORK_DIR}/alembic.ini" <<'PY'
"""Run the schema step against the pruned tree and insist it declined to act."""

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

from src.config import Settings
from src.release.bootstrap import apply_schema, database_revisions, known_revisions

config_path = Path(sys.argv[1])
engine = create_engine(Settings().database_url, future=True)
try:
    recorded = set(database_revisions(engine))
    pruned = known_revisions(config_path)
    ahead = sorted(recorded - pruned)
    if not ahead:
        raise SystemExit(
            "the database records no revision the pruned tree is missing, so there is "
            "nothing to roll back to. Run `make prod-seed` first, and check that a "
            "migration newer than the one this script pruned has actually been applied."
        )
    outcome = apply_schema(engine, config_path=config_path)
finally:
    engine.dispose()

if outcome.applied:
    raise SystemExit(
        "FINDING: the older image applied migrations against a newer database. The "
        "DB-ahead no-op in src.release.bootstrap.apply_schema did not fire, which "
        "means a real rollback would fail its pre-deploy command."
    )

print(
    json.dumps(
        {
            "revisions_ahead_of_the_older_image": ahead,
            "applied": outcome.applied,
            "database_revision": outcome.revision,
            "reason": outcome.reason,
        },
        indent=2,
        sort_keys=True,
    )
)
PY

echo "ROLLBACK-REHEARSAL-OK"
