#!/usr/bin/env bash
#
# Restore drill: a script with an exit code, not a document.
#
# Pull the latest encrypted dump, decrypt it, restore it into a scratch
# database, and then prove the restored database is the thing the application
# needs -- schema at the image's alembic head, and the rows the product serves
# actually present. Optionally boot the API image against it and run the
# behavioral smoke.
#
# THE SEED STEP IS DELIBERATELY SKIPPED. `make demo-seed` would repopulate the
# personas, the catalog and the movie state from fixtures, at which point the
# drill proves the seeder works and says nothing whatsoever about the backup.
# Every assertion below is made against restored rows only. (`make demo-reset`
# is `down --volumes` plus a reseed -- a destructive rebuild, not a restore
# test, which is why this script exists at all.)
#
# What the drill is checking for, beyond "pg_restore exited 0":
#
#   * A silently empty backup. Tenant-scoped tables are FORCE ROW LEVEL
#     SECURITY (alembic 0004, 0010) and pg_dump sets no app.tenant_id, so a dump
#     taken without BYPASSRLS restores cleanly and contains nothing. The row
#     counts below are the end-to-end check for that.
#   * A schema/code mismatch. The restored alembic_version is compared against
#     the head carried by the image that would be deployed, not against a number
#     written down somewhere.
#   * A restore target missing the application roles. backup.sh keeps privileges
#     in the dump (the app_user grants are ADR 0008's isolation, and a dump
#     stripped of them restores into a database the API cannot read), so the
#     roles have to exist on the target first. That is asserted before the
#     restore rather than discovered halfway through it.
#
# Usage:
#   restore-drill.sh [--dry-run] [--database movielens|keycloak]
#                    [--dump PATH_OR_OBJECT] [--target-dsn DSN]
#                    [--expected-head REVISION] [--skip-api-smoke]
#                    [--keep] [--report PATH]
#
#   --dry-run          resolve and print every stage with credentials redacted,
#                      touch nothing, exit 0. Safe with nothing configured.
#   --database         which dump line to restore (default movielens). The
#                      alembic and API stages apply to movielens only.
#   --dump             a local decrypted-or-encrypted file, or an object name
#                      inside the tier. Default: the newest object in the tier.
#   --target-dsn       restore into this existing, empty database. Without it,
#                      RESTORE_ADMIN_DSN is used to create a scratch database
#                      that is dropped again on exit.
#   --skip-api-smoke   an explicit operator choice; the run then reports
#                      RESTORE-DRILL-PARTIAL instead of RESTORE-DRILL-OK.
#   --keep             leave the scratch database in place for inspection.
#
# Environment:
#   RESTORE_REMOTE (remote) · RESTORE_REMOTE_PATH (/recsys) · RESTORE_TIER (daily)
#   RESTORE_ADMIN_DSN            maintenance DSN used to create and drop the
#                                scratch database; its role must hold BYPASSRLS
#                                or every row count below reads zero
#   RESTORE_TARGET_DSN           alternative to --target-dsn
#   RESTORE_AGE_IDENTITY_FILE    age identity (private key) file, or
#   RESTORE_AGE_IDENTITY         its contents, written to a 0600 temp file
#   RESTORE_REQUIRED_ROLES       ("app_user admin_user")
#   RESTORE_WORK_DIR (/tmp/restore-drill) · RESTORE_DRILL_REPORT
#   DRILL_API_IMAGE              image to boot for the smoke stage
#   DRILL_API_ENV_FILE           its environment. Must point APP_USER_DB_* at a
#                                pooler alias resolving to the restored database:
#                                src/serving/startup_checks.py opens pgBouncer's
#                                admin console and refuses to boot without it, so
#                                the API cannot be pointed straight at Postgres
#   DRILL_API_PORT (8100) · DRILL_API_WAIT_SECONDS (120)
#   DRILL_WEB_URL                readiness-probed by the smoke; the drill does
#                                not restore the web service, this is the
#                                deployed one
#   DRILL_KEYCLOAK_URL · DRILL_SMOKE_REALM (demo) · DRILL_SMOKE_CLIENT_ID
#   DRILL_SMOKE_CLIENT_SECRET · DRILL_SMOKE_GRANT_TYPE (client_credentials)
#   DRILL_SMOKE_USERNAME · DRILL_SMOKE_PASSWORD
#   DRILL_DOCKER_NETWORK         docker network the scratch API joins
#
# The smoke credentials are passed to the containerised smoke as arguments, so
# they are visible in that container's own process list for the seconds it runs.
# Nothing is written to this script's output: every log line that mentions a
# connection is redacted.
#
# Final line is RESTORE-DRILL-OK <object> <seconds> (or RESTORE-DRILL-PARTIAL),
# and the JSON report carries the dump identity, the durations and the outcome.

set -euo pipefail
umask 077

DRY_RUN=false
DATABASE="movielens"
DUMP_ARG=""
TARGET_DSN="${RESTORE_TARGET_DSN:-}"
EXPECTED_HEAD="${RESTORE_EXPECTED_HEAD:-}"
SKIP_API_SMOKE=false
KEEP_TARGET=false
REPORT_PATH="${RESTORE_DRILL_REPORT:-}"

while [ "$#" -gt 0 ]; do
	case "$1" in
	--dry-run) DRY_RUN=true ;;
	--database)
		DATABASE="${2:-}"
		shift
		;;
	--dump)
		DUMP_ARG="${2:-}"
		shift
		;;
	--target-dsn)
		TARGET_DSN="${2:-}"
		shift
		;;
	--expected-head)
		EXPECTED_HEAD="${2:-}"
		shift
		;;
	--report)
		REPORT_PATH="${2:-}"
		shift
		;;
	--skip-api-smoke) SKIP_API_SMOKE=true ;;
	--keep) KEEP_TARGET=true ;;
	-h | --help)
		sed -n '2,90p' "$0"
		exit 0
		;;
	*)
		echo "restore-drill.sh: unknown argument: $1" >&2
		exit 2
		;;
	esac
	shift
done

case "$DATABASE" in
movielens | keycloak) ;;
*)
	echo "restore-drill.sh: --database must be movielens or keycloak" >&2
	exit 2
	;;
esac

RESTORE_REMOTE="${RESTORE_REMOTE:-${BACKUP_REMOTE:-remote}}"
RESTORE_REMOTE_PATH="${RESTORE_REMOTE_PATH:-${BACKUP_REMOTE_PATH:-/recsys}}"
RESTORE_TIER="${RESTORE_TIER:-daily}"
RESTORE_REQUIRED_ROLES="${RESTORE_REQUIRED_ROLES:-app_user admin_user}"
RESTORE_WORK_DIR="${RESTORE_WORK_DIR:-/tmp/restore-drill}"
DRILL_API_PORT="${DRILL_API_PORT:-8100}"
DRILL_API_WAIT_SECONDS="${DRILL_API_WAIT_SECONDS:-120}"
DRILL_SMOKE_REALM="${DRILL_SMOKE_REALM:-demo}"
DRILL_SMOKE_GRANT_TYPE="${DRILL_SMOKE_GRANT_TYPE:-client_credentials}"

RUN_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SCRATCH_DB="restore_drill_$(printf '%s' "$RUN_TIMESTAMP" | tr '[:upper:]' '[:lower:]')"
STARTED_AT="$(date -u +%s)"
REMOTE_TIER="${RESTORE_REMOTE}:${RESTORE_REMOTE_PATH}/${DATABASE}/${RESTORE_TIER}"

CREATED_SCRATCH_DB=false
API_CONTAINER=""
IDENTITY_FILE=""
DUMP_OBJECT=""
DUMP_BYTES=0
TOC_ENTRIES=0
ENCRYPTED_PATH=""
PLAIN_PATH=""
RESTORE_SECONDS=0
RESTORED_HEAD=""
ROW_COUNTS=""
OUTCOME="failed"

# The log goes to stderr and stdout carries only the JSON report and the final
# sentinel, so a caller can parse the one without filtering the other.
log() {
	printf '%s drill: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
	printf '%s drill: FATAL %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
	exit 1
}

redact_dsn() {
	printf '%s' "$1" | sed -E 's|://([^:/?@]+):[^@]*@|://\1:***@|'
}

dsn_database() {
	printf '%s' "$1" | sed -E 's|^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?]*/?||; s|\?.*$||'
}

# Swap the database out of a DSN, keeping the query string (sslmode and friends)
# intact. Used to turn the maintenance DSN into the scratch database's DSN.
dsn_with_database() {
	printf '%s' "$1" | sed -E "s|^([a-zA-Z][a-zA-Z0-9+.-]*://[^/?]*)(/[^?]*)?|\1/$2|"
}

require_binary() {
	if ! command -v "$1" >/dev/null 2>&1; then
		if [ "$DRY_RUN" = true ]; then
			log "MISSING  $1 (not on PATH)"
			return 0
		fi
		die "$1 is not on PATH"
	fi
	return 0
}

require_value() {
	local label="$1" value="$2"
	if [ -z "$value" ]; then
		if [ "$DRY_RUN" = true ]; then
			log "MISSING  $label (required for a real run)"
			return 0
		fi
		die "$label must be set"
	fi
	return 0
}

target_scalar() {
	psql "$TARGET_DSN" --no-psqlrc --quiet --tuples-only --no-align \
		--command "$1" | tr -d '[:space:]'
}

cleanup() {
	local status=$?
	if [ -n "$API_CONTAINER" ]; then
		docker rm --force "$API_CONTAINER" >/dev/null 2>&1 || true
	fi
	if [ "$CREATED_SCRATCH_DB" = true ] && [ "$KEEP_TARGET" != true ]; then
		psql "${RESTORE_ADMIN_DSN:-}" --no-psqlrc --quiet \
			--command "DROP DATABASE IF EXISTS ${SCRATCH_DB} WITH (FORCE)" >/dev/null 2>&1 ||
			log "could not drop scratch database ${SCRATCH_DB}; drop it by hand"
	fi
	# The decrypted dump is the only plaintext copy of production data this
	# script ever creates, so its removal is not conditional on success.
	rm -rf "$RESTORE_WORK_DIR"
	# A drill that failed is exactly the drill worth having a record of, so the
	# report is written on the way out too -- with whatever the run got as far
	# as establishing.
	if [ "$status" -ne 0 ] && [ "$OUTCOME" = "failed" ]; then
		write_report "$(($(date -u +%s) - STARTED_AT))" || true
	fi
	exit "$status"
}

stage_preflight() {
	log "restore drill ${RUN_TIMESTAMP}, database ${DATABASE}, tier ${REMOTE_TIER}"
	if [ "$DRY_RUN" = true ]; then
		log "DRY RUN -- nothing is downloaded, decrypted, restored, created or dropped"
	fi

	require_binary psql
	require_binary pg_restore
	# Only a --dump that names an existing local file avoids the remote; a bare
	# object name is still resolved against the tier.
	if [ -z "$DUMP_ARG" ] || [ ! -f "$DUMP_ARG" ]; then
		require_binary rclone
	fi
	require_binary age

	if [ -z "$TARGET_DSN" ]; then
		require_value "RESTORE_ADMIN_DSN (or --target-dsn)" "${RESTORE_ADMIN_DSN:-}"
		if [ -n "${RESTORE_ADMIN_DSN:-}" ]; then
			TARGET_DSN="$(dsn_with_database "$RESTORE_ADMIN_DSN" "$SCRATCH_DB")"
		fi
	fi
	require_value "an age identity (RESTORE_AGE_IDENTITY_FILE or RESTORE_AGE_IDENTITY)" \
		"${RESTORE_AGE_IDENTITY_FILE:-}${RESTORE_AGE_IDENTITY:-}"

	if [ "$SKIP_API_SMOKE" != true ] && [ "$DATABASE" = "movielens" ]; then
		require_binary docker
		require_binary curl
		require_value "DRILL_API_IMAGE" "${DRILL_API_IMAGE:-}"
		require_value "DRILL_API_ENV_FILE" "${DRILL_API_ENV_FILE:-}"
		require_value "DRILL_WEB_URL" "${DRILL_WEB_URL:-}"
		require_value "DRILL_KEYCLOAK_URL" "${DRILL_KEYCLOAK_URL:-}"
	fi

	log "target database: $(redact_dsn "${TARGET_DSN:-<unset>}")"
}

stage_fetch() {
	ENCRYPTED_PATH="${RESTORE_WORK_DIR}/dump.age"
	mkdir -p "$RESTORE_WORK_DIR"

	if [ -n "$DUMP_ARG" ] && [ -f "$DUMP_ARG" ]; then
		DUMP_OBJECT="$DUMP_ARG"
		log "using the local dump ${DUMP_ARG}"
		cp "$DUMP_ARG" "$ENCRYPTED_PATH"
		return 0
	fi

	if [ -n "$DUMP_ARG" ]; then
		DUMP_OBJECT="${REMOTE_TIER}/${DUMP_ARG}"
	else
		# Object names are UTC timestamps, so the newest is the last one in
		# lexicographic order -- no reliance on the remote's mtime.
		local newest
		newest="$(rclone --stats=0 lsf --files-only "$REMOTE_TIER" | sort | tail -n 1)"
		[ -n "$newest" ] || die "no objects under ${REMOTE_TIER}"
		DUMP_OBJECT="${REMOTE_TIER}/${newest}"
	fi

	log "fetching ${DUMP_OBJECT}"
	rclone --stats=0 copyto "$DUMP_OBJECT" "$ENCRYPTED_PATH"
}

stage_decrypt() {
	local encrypted="$ENCRYPTED_PATH" plain="${RESTORE_WORK_DIR}/dump.pgc"

	if [ -n "${RESTORE_AGE_IDENTITY_FILE:-}" ]; then
		IDENTITY_FILE="$RESTORE_AGE_IDENTITY_FILE"
	else
		IDENTITY_FILE="${RESTORE_WORK_DIR}/identity.age"
		printf '%s\n' "${RESTORE_AGE_IDENTITY:-}" >"$IDENTITY_FILE"
		chmod 0600 "$IDENTITY_FILE"
	fi

	# An already-decrypted local dump is accepted so a drill can be rehearsed
	# without the production identity in the room.
	if head -c 21 "$encrypted" | grep -q '^age-encryption.org/v1$'; then
		age --decrypt --identity "$IDENTITY_FILE" --output "$plain" "$encrypted"
	else
		log "the supplied dump is not age-encrypted; using it as-is"
		mv "$encrypted" "$plain"
	fi

	DUMP_BYTES="$(wc -c <"$plain" | tr -d ' ')"
	TOC_ENTRIES="$(pg_restore --list "$plain" | grep -c '^[0-9]' || true)"
	[ "$TOC_ENTRIES" -ge 1 ] || die "the decrypted dump has no readable table of contents"
	log "decrypted ${DUMP_BYTES} bytes, ${TOC_ENTRIES} table-of-contents entries"
	PLAIN_PATH="$plain"
}

stage_prepare_target() {
	local role roles_found role_list expected_roles bypassrls tables

	# The scratch database is ours to create only when the target DSN is the one
	# this script derived from the maintenance DSN. An operator-supplied target
	# is never created and never dropped.
	if [ -n "${RESTORE_ADMIN_DSN:-}" ] && [ "$(dsn_database "$TARGET_DSN")" = "$SCRATCH_DB" ]; then
		log "creating scratch database ${SCRATCH_DB}"
		psql "$RESTORE_ADMIN_DSN" --no-psqlrc --quiet \
			--command "CREATE DATABASE ${SCRATCH_DB}"
		CREATED_SCRATCH_DB=true
	fi

	# Row counts taken by a role that cannot bypass RLS read zero on every
	# tenant-scoped table, which would make an empty restore look like a
	# successful one. The drill refuses to draw conclusions from those counts.
	bypassrls="$(target_scalar "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")"
	[ "$bypassrls" = "t" ] ||
		die "the drill's own role can neither bypass RLS nor act as superuser: every row count would read zero"

	tables="$(target_scalar "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')")"
	[ "$tables" = "0" ] ||
		die "the restore target already holds ${tables} tables; a drill restores into an empty database"

	role_list=""
	for role in $RESTORE_REQUIRED_ROLES; do
		role_list="${role_list}${role_list:+,}'${role}'"
	done
	expected_roles="$(printf '%s\n' "$RESTORE_REQUIRED_ROLES" | wc -w | tr -d ' ')"
	roles_found="$(target_scalar "SELECT count(*) FROM pg_roles WHERE rolname IN (${role_list})")"
	[ "$roles_found" = "$expected_roles" ] ||
		die "the restore target is missing application roles (${RESTORE_REQUIRED_ROLES}); run the one-time provisioning SQL first -- the dump carries their grants"
}

stage_restore() {
	local started ended
	started="$(date -u +%s)"
	log "restoring into $(redact_dsn "$TARGET_DSN")"
	pg_restore --no-owner --exit-on-error --dbname "$TARGET_DSN" "$PLAIN_PATH"
	ended="$(date -u +%s)"
	RESTORE_SECONDS="$((ended - started))"
	log "restore finished in ${RESTORE_SECONDS}s"
}

# Expected head comes from the image that would be deployed, never from a value
# written down. `alembic heads` reads the script directory only, so it needs no
# database and no settings.
resolve_expected_head() {
	if [ -n "$EXPECTED_HEAD" ]; then
		printf '%s' "$EXPECTED_HEAD"
		return 0
	fi
	if [ -n "${DRILL_API_IMAGE:-}" ] && command -v docker >/dev/null 2>&1; then
		docker run --rm "$DRILL_API_IMAGE" alembic heads |
			awk 'NF {head=$1} END {print head}'
		return 0
	fi
	if command -v alembic >/dev/null 2>&1 && [ -f alembic.ini ]; then
		alembic heads | awk 'NF {head=$1} END {print head}'
		return 0
	fi
	die "cannot resolve the expected alembic head: pass --expected-head, or set DRILL_API_IMAGE"
}

stage_assert_schema() {
	local expected
	expected="$(resolve_expected_head)"
	RESTORED_HEAD="$(target_scalar "SELECT version_num FROM alembic_version")"
	[ -n "$RESTORED_HEAD" ] || die "the restored database has no alembic_version row"
	[ "$RESTORED_HEAD" = "$expected" ] ||
		die "restored schema is at ${RESTORED_HEAD}, the image's head is ${expected}"
	log "schema: alembic_version ${RESTORED_HEAD} matches the image head"
}

# Row counts from restored rows only. Nothing here runs the seeder.
stage_assert_content() {
	local table count entry required
	local required_tables="public.tenants movies movie_catalog_metadata demo_personas user_movie_state"
	local reported_tables="ratings user_feedback_events recommendation_audits request_audits feature_store.user_features feature_store.item_features feature_store.user_item_features"

	if [ "$DATABASE" = "keycloak" ]; then
		required_tables="realm"
		reported_tables="user_entity client"
	fi

	ROW_COUNTS=""
	for table in $required_tables $reported_tables; do
		count="$(target_scalar "SELECT count(*) FROM ${table}" 2>/dev/null || true)"
		[ -n "$count" ] || count="-1"
		entry="$(printf '"%s":%s' "$table" "$count")"
		ROW_COUNTS="${ROW_COUNTS}${ROW_COUNTS:+,}${entry}"
		required=false
		case " $required_tables " in
		*" $table "*) required=true ;;
		esac
		if [ "$required" = true ] && [ "$count" -lt 1 ]; then
			die "restored ${table} holds ${count} rows; the backup or the dumping role is the problem, and the seeder is deliberately not being run to hide it"
		fi
		log "rows: ${table}=${count}"
	done
	log "seed step: SKIPPED deliberately -- every count above came out of the dump"
}

stage_api_smoke() {
	local waited=0
	local args run_args
	API_CONTAINER="restore-drill-api-${RUN_TIMESTAMP}"

	# Seeded with a label so the array is never empty -- `set -u` and an empty
	# array expansion do not get along on the bash that ships with macOS.
	run_args=(--label "restore-drill=${RUN_TIMESTAMP}")
	if [ -n "${DRILL_DOCKER_NETWORK:-}" ]; then
		run_args+=(--network "$DRILL_DOCKER_NETWORK")
	fi

	log "booting ${DRILL_API_IMAGE} on 127.0.0.1:${DRILL_API_PORT} with ENVIRONMENT=production"
	docker run --detach --name "$API_CONTAINER" \
		"${run_args[@]}" \
		--env-file "$DRILL_API_ENV_FILE" \
		--env ENVIRONMENT=production \
		--publish "127.0.0.1:${DRILL_API_PORT}:8000" \
		"$DRILL_API_IMAGE" \
		uvicorn src.serving.app:app --host 0.0.0.0 --port 8000 --workers 2 --no-access-log >/dev/null

	until curl --silent --fail --max-time 5 "http://127.0.0.1:${DRILL_API_PORT}/readyz" >/dev/null; do
		waited=$((waited + 2))
		if [ "$waited" -ge "$DRILL_API_WAIT_SECONDS" ]; then
			docker logs --tail 40 "$API_CONTAINER" >&2 || true
			die "the restored-database API never became ready within ${DRILL_API_WAIT_SECONDS}s"
		fi
		sleep 2
	done
	log "the API is ready against the restored database"

	# Built as an array so the credentials are never interpolated into a string
	# this script might log.
	args=(--api-url "http://127.0.0.1:8000"
		--web-url "$DRILL_WEB_URL"
		--keycloak-url "$DRILL_KEYCLOAK_URL"
		--realm "$DRILL_SMOKE_REALM"
		--grant-type "$DRILL_SMOKE_GRANT_TYPE")
	if [ -n "${DRILL_SMOKE_CLIENT_ID:-}" ]; then
		args+=(--client-id "$DRILL_SMOKE_CLIENT_ID")
	fi
	if [ -n "${DRILL_SMOKE_CLIENT_SECRET:-}" ]; then
		args+=(--client-secret "$DRILL_SMOKE_CLIENT_SECRET")
	fi
	if [ -n "${DRILL_SMOKE_USERNAME:-}" ]; then
		args+=(--username "$DRILL_SMOKE_USERNAME")
	fi
	if [ -n "${DRILL_SMOKE_PASSWORD:-}" ]; then
		args+=(--password "$DRILL_SMOKE_PASSWORD")
	fi

	log "running synthetic.smoke.demo against the restored database"
	docker exec "$API_CONTAINER" python -m synthetic.smoke.demo "${args[@]}"
}

write_report() {
	local seconds="$1" report
	report="$(printf '{"timestamp":"%s","database":"%s","dump":"%s","dump_bytes":%s,"toc_entries":%s,"alembic_head":"%s","target":"%s","seed_step":"skipped","row_counts":{%s},"restore_seconds":%s,"seconds":%s,"outcome":"%s"}' \
		"$RUN_TIMESTAMP" "$DATABASE" "$DUMP_OBJECT" "$DUMP_BYTES" "$TOC_ENTRIES" \
		"$RESTORED_HEAD" "$(redact_dsn "$TARGET_DSN")" "$ROW_COUNTS" "$RESTORE_SECONDS" \
		"$seconds" "$OUTCOME")"
	printf '%s\n' "$report"
	if [ -n "$REPORT_PATH" ]; then
		printf '%s\n' "$report" >"$REPORT_PATH"
		log "report written to ${REPORT_PATH}"
	fi
}

dry_run_plan() {
	local identity="the identity in RESTORE_AGE_IDENTITY"
	local disposal="dropped on exit"
	if [ -n "${RESTORE_AGE_IDENTITY_FILE:-}" ]; then
		identity="$RESTORE_AGE_IDENTITY_FILE"
	fi
	if [ "$KEEP_TARGET" = true ]; then
		disposal="kept"
	fi

	log "would fetch    ${DUMP_ARG:-the newest object under ${REMOTE_TIER}}"
	log "would decrypt  with ${identity}"
	if [ "$(dsn_database "${TARGET_DSN:-}")" = "$SCRATCH_DB" ]; then
		log "would create   scratch database ${SCRATCH_DB} (${disposal})"
	fi
	log "would assert   roles ${RESTORE_REQUIRED_ROLES} exist, the target is empty, and the drill role bypasses RLS"
	log "would restore  pg_restore --no-owner --exit-on-error --dbname $(redact_dsn "${TARGET_DSN:-<unset>}")"
	if [ "$DATABASE" = "movielens" ]; then
		log "would assert   alembic_version == ${EXPECTED_HEAD:-the head reported by ${DRILL_API_IMAGE:-the API image}}"
		log "would assert   non-zero rows in public.tenants, movies, movie_catalog_metadata, demo_personas, user_movie_state"
	else
		log "would assert   non-zero rows in realm"
	fi
	log "would NOT run  the seeder -- a restore that needs it has proven nothing"
	if [ "$SKIP_API_SMOKE" = true ] || [ "$DATABASE" = "keycloak" ]; then
		log "would skip     the API smoke stage"
	else
		log "would boot     ${DRILL_API_IMAGE:-<unset>} on 127.0.0.1:${DRILL_API_PORT} and run synthetic.smoke.demo"
	fi
	log "DRY RUN complete"
}

main() {
	stage_preflight

	if [ "$DRY_RUN" = true ]; then
		dry_run_plan
		return 0
	fi

	trap cleanup EXIT

	local total
	stage_fetch
	stage_decrypt
	stage_prepare_target
	stage_restore

	if [ "$DATABASE" = "movielens" ]; then
		stage_assert_schema
	fi
	stage_assert_content

	if [ "$SKIP_API_SMOKE" = true ] || [ "$DATABASE" = "keycloak" ]; then
		OUTCOME="partial"
		log "API smoke stage skipped -- this run does not prove the restored database serves traffic"
	else
		stage_api_smoke
		OUTCOME="ok"
	fi

	total="$(($(date -u +%s) - STARTED_AT))"
	write_report "$total"
	if [ "$OUTCOME" = "ok" ]; then
		printf 'RESTORE-DRILL-OK %s %ss\n' "$DUMP_OBJECT" "$total"
	else
		printf 'RESTORE-DRILL-PARTIAL %s %ss\n' "$DUMP_OBJECT" "$total"
	fi
}

main
