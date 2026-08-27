#!/usr/bin/env bash
#
# One release, start to finish, on the box.
#
#   infra/deploy/deploy.sh <40-character git sha>   deploy that release
#   infra/deploy/deploy.sh --rollback               go back to the previous one
#
# CI runs the first form over SSH on every merge to main, after checking out
# the same SHA into /opt/movielens. An operator runs either form by hand.
#
# The sequence and why it is that order:
#
#   1. pull every image at IMAGE_TAG=<sha>, and refuse to continue unless all
#      of them arrived. The box has a checkout, so a missed pull would
#      otherwise be silently repaired by `up` building the image locally --
#      which is how a release ends up running something CI never tested;
#   2. run the release jobs: role provisioning, realm provisioning, migrations
#      and seed, then feature materialization. Before the new containers start,
#      because the schema has to be ahead of the code that queries it;
#   3. `up -d --wait` the long-lived services, which is where the outage is:
#      Compose recreates the containers whose image changed and the site is
#      down for the ten or twenty seconds that takes. One box, one failure
#      domain -- this is the honest cost of the design (ADR 0013);
#   4. verify. Not "is it running" but the whole post-deploy matrix: issuer
#      equality, realm invariants, learned serving, the write path, artifact
#      provenance, cross-tenant isolation, the non-latency promises;
#   5. if verification fails, roll straight back to the previous release and
#      verify that. An unverified release is not left serving.
#
# The last line is a sentinel -- DEPLOY-OK <sha> or ROLLBACK-OK <sha> -- and
# the CI job fails if DEPLOY-OK is absent. Sentinels rather than exit codes
# alone because the interesting failure is the one where the rollback worked:
# the exit code is non-zero either way, and only the sentinel says which
# release is now serving.
#
# Nothing here prints a secret. .env.prod is passed to Compose by path and is
# never read, echoed or copied by this script.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.prod"
RELEASE_DIR="$REPO_ROOT/.release"
CURRENT_FILE="$RELEASE_DIR/current"
PREVIOUS_FILE="$RELEASE_DIR/previous"
LOCK_DIR="$RELEASE_DIR/lock"

# `make` drives the release steps rather than this script reimplementing them:
# the Makefile already owns the compose invocation, the project name, the
# env-file path and the order the jobs run in, and a second copy of that here
# would be a second thing to keep in step. Overridable for testing this script
# with the steps stubbed out.
MAKE="${DEPLOY_MAKE:-make}"

usage() {
	cat <<'USAGE'
usage: deploy.sh <sha>
       deploy.sh --rollback

  <sha>        deploy this release: a 40-character git commit SHA, which is
               also the image tag CI published for it
  --rollback   redeploy the recorded previous release

Environment:
  DEPLOY_DRY_RUN=1     print each command instead of running it
  DEPLOY_SKIP_PULL=1   skip the registry fetch; the images must already be
                       local. The presence assertion still runs. Rehearsal
                       only -- on the box the pull is the point
  DEPLOY_MAKE=<cmd>    the make to drive the release steps with

Reads .env.prod and .release/ from the repository root. Prints DEPLOY-OK <sha>
or ROLLBACK-OK <sha> on success.
USAGE
}

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$1"; }
die() {
	printf 'deploy: %s\n' "$1" >&2
	exit 1
}

run() {
	printf '+ %s\n' "$*"
	if [ "${DEPLOY_DRY_RUN:-0}" = "1" ]; then
		return 0
	fi
	"$@"
}

# --- arguments --------------------------------------------------------------

mode=deploy
requested_sha=""
case "${1:-}" in
-h | --help)
	usage
	exit 0
	;;
--rollback)
	[ $# -eq 1 ] || die "--rollback takes no other argument"
	mode=rollback
	;;
"")
	usage >&2
	exit 2
	;;
*)
	requested_sha="$1"
	[ $# -eq 1 ] || die "expected exactly one argument"
	# Refusing anything but a full SHA is deliberate. A short SHA and a
	# branch name are both perfectly good git references and neither is a
	# tag CI published, so accepting them would turn a typo into a pull
	# failure halfway through a release rather than a refusal before it.
	case "$requested_sha" in
	*[!0-9a-f]* | "") die "expected a 40-character git sha, got '$requested_sha'" ;;
	esac
	[ ${#requested_sha} -eq 40 ] || die "expected a 40-character git sha, got '$requested_sha'"
	;;
esac

cd "$REPO_ROOT"

# --- preconditions ----------------------------------------------------------

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; copy infra/deploy/production.env.example and fill it in"

# A world-readable secrets file is worth saying out loud, but it is not worth
# refusing a release over -- the fix is one chmod and the release is unaffected.
if [ "$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")" != "600" ]; then
	log "warning: $ENV_FILE is not mode 0600"
fi

mkdir -p "$RELEASE_DIR"

# Two deploys at once would interleave `up -d` with a rollback's `up -d` and
# leave the box running an unrepeatable mix. The GitHub workflows share a
# concurrency group, so this catches the other case: a human deploying while a
# scheduled job is mid-flight.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
	die "another release is in progress ($LOCK_DIR exists; remove it if that is stale)"
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

read_release() { [ -s "$1" ] && tr -d '[:space:]' <"$1" || true; }

current="$(read_release "$CURRENT_FILE")"
previous="$(read_release "$PREVIOUS_FILE")"

# --- the release steps ------------------------------------------------------

deploy_release() {
	local sha="$1"
	export IMAGE_TAG="$sha"

	# Every step returns explicitly instead of leaning on `set -e`. This
	# function is only ever called as an `if` condition, and bash suspends
	# errexit for everything evaluated in a condition -- including the body
	# of a function called there. Without these, a failed pull would fall
	# through to `prod-serve`, which would happily build the missing image
	# out of the checkout and ship something CI never tested, and the run
	# would still end in DEPLOY-OK because the function's status is only its
	# last command's. The same silence would swallow a failed migration.
	log "pulling images at $sha"
	run "$MAKE" prod-pull || return 1

	log "running the release jobs (provisioning, migrations, seed, materialize)"
	run "$MAKE" prod-release || return 1

	log "starting the serving tier"
	run "$MAKE" prod-serve || return 1

	log "verifying"
	run "$MAKE" prod-verify || return 1
}

write_release_record() {
	printf '%s\n' "$1" >"$CURRENT_FILE"
	printf '%s\n' "$2" >"$PREVIOUS_FILE"
}

# --- explicit rollback ------------------------------------------------------

if [ "$mode" = rollback ]; then
	[ -n "$previous" ] || die "no previous release recorded in $PREVIOUS_FILE"
	log "rolling back to $previous (from ${current:-unknown})"
	# The checkout is left where it is on purpose: this script is running out
	# of it, and rewinding the tree underneath a running bash would be
	# rewriting the program mid-execution. Images go back, the compose file
	# and this script stay at the deployed SHA -- which is safe as long as
	# they can still describe the older images, and is why a change to the
	# compose file's shape is a release worth watching. To rewind the tree
	# too, check out the older SHA and run this again forwards.
	if deploy_release "$previous"; then
		# The release now serving is the one that was previously known
		# good, so it becomes `current` -- and `previous` is cleared
		# rather than pointed at the release we just rolled away from,
		# because the next `--rollback` reinstating a known-bad release
		# would be the worst possible response to a second incident.
		write_release_record "$previous" ""
		log "rollback verified"
		printf 'ROLLBACK-OK %s\n' "$previous"
		exit 0
	fi
	printf 'ROLLBACK-FAILED %s\n' "$previous"
	die "the rollback to $previous did not verify; the box needs a human"
fi

# --- forward deploy ---------------------------------------------------------

# The images are pinned by SHA but the compose file, the Makefile and this
# script come from the checkout, so a tree that is not at the release being
# deployed is a release nobody has ever tested. CI checks the SHA out before
# calling this; a hand-run deploy has to do the same.
if git rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
	head_sha="$(git rev-parse HEAD)"
	[ "$head_sha" = "$requested_sha" ] ||
		die "the checkout is at $head_sha, not $requested_sha; run 'git fetch origin && git checkout --detach $requested_sha' first"
fi

if [ "$requested_sha" = "$current" ]; then
	log "note: $requested_sha is already the recorded current release; redeploying it"
else
	previous="$current"
fi

# Recorded before the release runs, not after. If the box loses power halfway
# through, `current` names the release whose images are on disk and whose
# migrations may already have applied -- which is what the next operator needs
# to know. A record written only on success would say the old release was
# still serving, which by then would be a lie.
write_release_record "$requested_sha" "$previous"

log "deploying $requested_sha (previous: ${previous:-none})"
if deploy_release "$requested_sha"; then
	log "verified"
	printf 'DEPLOY-OK %s\n' "$requested_sha"
	exit 0
fi

# Any of the four steps can land here, not just verification -- a pull that
# did not complete or a migration that failed is the same decision.
log "release failed for $requested_sha"

if [ -z "$previous" ]; then
	# The first deploy, or a box whose history was cleared. There is nothing
	# to roll back to and inventing one would be worse than stopping: the
	# stack is left exactly as it is so the failure can be read off it.
	printf 'DEPLOY-FAILED %s\n' "$requested_sha"
	die "no previous release recorded, so nothing to roll back to; the stack is left as it is"
fi

log "rolling back to $previous"
if deploy_release "$previous"; then
	write_release_record "$previous" ""
	log "rollback verified; $previous is serving"
	printf 'ROLLBACK-OK %s\n' "$previous"
	# Non-zero even though the rollback worked. The release did not ship, and
	# a green CI run against a rolled-back box would be the most misleading
	# possible outcome. The sentinel above is what says the site is up.
	exit 1
fi

printf 'ROLLBACK-FAILED %s\n' "$previous"
die "$requested_sha failed to verify and the rollback to $previous also failed; the box needs a human"
