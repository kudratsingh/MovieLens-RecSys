#!/bin/sh
# One image, three jobs. The API service, the release job and the verify cron
# all run from infra/api/Dockerfile -- it is the only image that carries
# alembic/, alembic.ini and synthetic/ alongside src/ -- so the difference
# between them is a mode word rather than a second build.
#
#   serve                  uvicorn, ${API_WORKERS:-4} workers on ${PORT:-8000}
#   bootstrap [args...]    python -m src.release.bootstrap
#   verify    [args...]    python -m src.release.verify
#
# Anything else is exec'd as given. That default is load-bearing in two places
# and worth keeping: the Compose services already pass explicit commands
# (`python -m src.data.demo_setup`, the four-worker `uvicorn` line for the load
# profile) which an ENTRYPOINT would otherwise turn into nonsense, and a
# platform whose custom start command *appends* to the image ENTRYPOINT instead
# of replacing the CMD lands here too. Neither case needs to know this file
# exists.
set -eu

mode="${1:-serve}"

case "$mode" in
serve)
    shift
    # No --no-access-log here. The deployed API sets it through its own start
    # command (it serves behind an edge and logs a request twice otherwise);
    # the dev stack wants the request log, and a default that removed it would
    # take it away from everyone who has never heard of this script.
    exec uvicorn src.serving.app:app \
        --host 0.0.0.0 \
        --port "${PORT:-8000}" \
        --workers "${API_WORKERS:-4}" \
        "$@"
    ;;
bootstrap)
    shift
    exec python -m src.release.bootstrap "$@"
    ;;
verify)
    shift
    exec python -m src.release.verify "$@"
    ;;
*)
    exec "$@"
    ;;
esac
