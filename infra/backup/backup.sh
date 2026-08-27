#!/usr/bin/env bash
#
# Nightly backup of both production databases: dump, encrypt to a public age
# recipient, copy off-provider, expire what has aged out.
#
# Three properties this is built around.
#
#   1. The dumping role must be able to read past row-level security. Every
#      tenant-scoped table is FORCE ROW LEVEL SECURITY with a policy keyed on
#      current_setting('app.tenant_id') (alembic 0004, 0010), and pg_dump sets
#      no such setting. A dump taken by a role without BYPASSRLS therefore
#      succeeds, exits 0, and contains no rows at all -- the worst failure this
#      script could possibly have, because nothing about it looks wrong until a
#      restore. It is asserted before the first byte is written.
#   2. Encryption happens here, not at the object store. `age -r` takes a public
#      key, so this container cannot decrypt what it produced: a compromised
#      backup job leaks nothing, and the storage provider is never trusted with
#      plaintext identity data.
#   3. The copy leaves the box. On a single host the dumps and the database
#      share one failure domain and one provider account, so a backup that
#      stays on the volume is lost by whatever loses the machine. Off-box
#      storage is the whole recovery story here, not a second copy of one.
#
# Ownership is dropped from the dump (--no-owner) because the owning role is
# deployment-specific, but privileges are kept: the app_user / admin_user grants
# are how ADR 0008's isolation is actually expressed, and a dump stripped of
# them restores into a database the API cannot read. The consequence is that a
# restore target needs the roles pre-created first -- restore-drill.sh checks
# for them before it touches anything.
#
# Redis is deliberately not backed up. It holds only derived state and its
# repair path is `bootstrap materialize`, which is both faster and more correct
# than restoring an RDB.
#
# Required environment:
#   PGHOST_APP / PGUSER_APP / PGPASSWORD_APP    application database (role must
#                                               hold BYPASSRLS; `migrator` does)
#   PGHOST_KC  / PGUSER_KC  / PGPASSWORD_KC     Keycloak database
#   BACKUP_AGE_RECIPIENT                        age public key(s), whitespace
#                                               separated. Public, not a secret;
#                                               list two during a key rotation
#                                               so both identities can decrypt.
#   RCLONE_CONFIG_REMOTE_*                      rclone's own env-var config for
#                                               the remote named by BACKUP_REMOTE
#
# Optional environment (defaults in parentheses):
#   PGPORT_APP (5432) · PGDATABASE_APP (movielens) · PGSSLMODE_APP (prefer)
#   PGPORT_KC  (5432) · PGDATABASE_KC  (keycloak)  · PGSSLMODE_KC  (prefer)
#   BACKUP_REMOTE (remote) · BACKUP_REMOTE_PATH (/recsys)
#   BACKUP_RETENTION_DAILY (7) · BACKUP_RETENTION_WEEKLY (4)
#   BACKUP_RETENTION_MONTHLY (6)
#   BACKUP_WEEKLY_DOW (7, Sunday) · BACKUP_MONTHLY_DOM (01)
#   BACKUP_WORK_DIR (/tmp/backup) · BACKUP_MIN_DUMP_BYTES (1024)
#
# Flags:
#   --dry-run   resolve everything, print every command that would run with the
#               credentials redacted, touch nothing, exit 0
#
# Prints BACKUP-OK as its final line. `docker compose run` does propagate the
# exit code, so the sentinel is not the only signal here -- it is the one that
# proves the script reached its own end, which an exit code cannot distinguish
# from a job the box killed part-way through.

set -euo pipefail
umask 077

DRY_RUN=false
for arg in "$@"; do
	case "$arg" in
	--dry-run) DRY_RUN=true ;;
	-h | --help)
		sed -n '2,60p' "$0"
		exit 0
		;;
	*)
		echo "backup.sh: unknown argument: $arg" >&2
		exit 2
		;;
	esac
done

PGPORT_APP="${PGPORT_APP:-5432}"
PGDATABASE_APP="${PGDATABASE_APP:-movielens}"
PGSSLMODE_APP="${PGSSLMODE_APP:-prefer}"
PGPORT_KC="${PGPORT_KC:-5432}"
PGDATABASE_KC="${PGDATABASE_KC:-keycloak}"
PGSSLMODE_KC="${PGSSLMODE_KC:-prefer}"

BACKUP_REMOTE="${BACKUP_REMOTE:-remote}"
BACKUP_REMOTE_PATH="${BACKUP_REMOTE_PATH:-/recsys}"
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
BACKUP_RETENTION_MONTHLY="${BACKUP_RETENTION_MONTHLY:-6}"
BACKUP_WEEKLY_DOW="${BACKUP_WEEKLY_DOW:-7}"
BACKUP_MONTHLY_DOM="${BACKUP_MONTHLY_DOM:-01}"
BACKUP_WORK_DIR="${BACKUP_WORK_DIR:-/tmp/backup}"
BACKUP_MIN_DUMP_BYTES="${BACKUP_MIN_DUMP_BYTES:-1024}"

RUN_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DOW="$(date -u +%u)"
RUN_DOM="$(date -u +%d)"
STARTED_AT="$(date -u +%s)"

# The log goes to stderr and stdout carries only the JSON summary and the final
# sentinel, so a caller can parse the one without filtering the other.
log() {
	printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
	printf '%s backup: FATAL %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
	exit 1
}

# Every message that mentions a connection goes through this, so a password can
# never reach the log by way of a conninfo string.
describe_target() {
	printf '%s@%s:%s/%s' "$1" "$2" "$3" "$4"
}

require_var() {
	local name="$1"
	if [ -z "${!name:-}" ]; then
		if [ "$DRY_RUN" = true ]; then
			log "MISSING  $name (required for a real run)"
			return 0
		fi
		die "$name must be set"
	fi
	return 0
}

require_binary() {
	local name="$1"
	if ! command -v "$name" >/dev/null 2>&1; then
		if [ "$DRY_RUN" = true ]; then
			log "MISSING  $name (not on PATH)"
			return 0
		fi
		die "$name is not on PATH"
	fi
	return 0
}

# `age` accepts repeated -r, and a rotation needs an overlap window where both
# the outgoing and the incoming identity can read a night's backup.
age_recipient_args() {
	local recipient
	for recipient in ${BACKUP_AGE_RECIPIENT:-}; do
		printf '%s\n' "-r" "$recipient"
	done
}

# Reads a single value with psql. The password travels in the child's
# environment; it is never an argument and never printed.
psql_scalar() {
	local host="$1" port="$2" db="$3" user="$4" password="$5" sslmode="$6" sql="$7"
	PGPASSWORD="$password" PGSSLMODE="$sslmode" psql \
		--host "$host" --port "$port" --username "$user" --dbname "$db" \
		--no-password --no-psqlrc --quiet --tuples-only --no-align \
		--command "$sql" | tr -d '[:space:]'
}

file_size_bytes() {
	# BSD and GNU stat disagree on flags; wc is the portable answer and the
	# files here are small enough that reading them costs nothing worth saving.
	wc -c <"$1" | tr -d ' '
}

# The first 22 bytes of an age file are its version line. Checking them is a
# cheap, absolute guarantee that what we are about to push off-provider is
# ciphertext -- the one mistake in this script that would be unrecoverable and
# silent.
assert_age_ciphertext() {
	local path="$1" header
	header="$(head -c 21 "$path" 2>/dev/null || true)"
	if [ "$header" != "age-encryption.org/v1" ]; then
		die "$path does not begin with an age header; refusing to upload it"
	fi
}

rclone_quiet() {
	rclone --stats=0 "$@"
}

remote_size_bytes() {
	# `rclone lsl` prints "<size> <date> <time> <name>"; the size is what we
	# compare against the local file so a truncated upload cannot pass.
	rclone_quiet lsl "$1" 2>/dev/null | awk 'NR==1 {print $1}'
}

REPORT_ENTRIES=""

append_report_entry() {
	if [ -n "$REPORT_ENTRIES" ]; then
		REPORT_ENTRIES="$REPORT_ENTRIES,"
	fi
	REPORT_ENTRIES="$REPORT_ENTRIES$1"
}

# One database: assert we can read it, dump it, prove the dump is readable,
# encrypt it, push it, prove it arrived, then fan it out to the weekly and
# monthly tiers on the days those tiers are written.
back_up_database() {
	local label="$1" host="$2" port="$3" db="$4" user="$5" password="$6"
	local sslmode="$7" requires_bypassrls="$8"
	local base="${BACKUP_REMOTE}:${BACKUP_REMOTE_PATH}/${label}"
	local object="${RUN_TIMESTAMP}.dump.age"
	local dump="${BACKUP_WORK_DIR}/${label}-${RUN_TIMESTAMP}.dump"
	local encrypted="${dump}.age"
	local started ended toc_entries dump_bytes encrypted_bytes uploaded_bytes
	local bypassrls

	log "database ${label}: $(describe_target "$user" "$host" "$port" "$db")"

	if [ "$DRY_RUN" = true ]; then
		log "would run  pg_dump --format=custom --no-owner -h $host -p $port -U $user -d $db -f $dump"
		log "would run  age $(age_recipient_args | tr '\n' ' ')-o $encrypted $dump"
		log "would run  rclone copyto $encrypted ${base}/daily/${object}"
		if [ "$RUN_DOW" = "$BACKUP_WEEKLY_DOW" ]; then
			log "would run  rclone copyto ${base}/daily/${object} ${base}/weekly/${object}"
		fi
		if [ "$RUN_DOM" = "$BACKUP_MONTHLY_DOM" ]; then
			log "would run  rclone copyto ${base}/daily/${object} ${base}/monthly/${object}"
		fi
		log "would run  rclone delete --min-age ${BACKUP_RETENTION_DAILY}d ${base}/daily"
		log "would run  rclone delete --min-age $((BACKUP_RETENTION_WEEKLY * 7))d ${base}/weekly"
		log "would run  rclone delete --min-age $((BACKUP_RETENTION_MONTHLY * 31))d ${base}/monthly"
		return 0
	fi

	started="$(date -u +%s)"

	if [ "$requires_bypassrls" = true ]; then
		bypassrls="$(psql_scalar "$host" "$port" "$db" "$user" "$password" "$sslmode" \
			"SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")"
		if [ "$bypassrls" != "t" ]; then
			die "${user} can neither bypass RLS nor act as superuser on ${db}: every tenant-scoped table would dump zero rows"
		fi
		log "database ${label}: ${user} can read past row-level security"
	fi

	PGPASSWORD="$password" PGSSLMODE="$sslmode" pg_dump \
		--host "$host" --port "$port" --username "$user" --dbname "$db" \
		--no-password --format=custom --no-owner --file "$dump"

	dump_bytes="$(file_size_bytes "$dump")"
	if [ "$dump_bytes" -lt "$BACKUP_MIN_DUMP_BYTES" ]; then
		die "dump of ${db} is ${dump_bytes} bytes, below the ${BACKUP_MIN_DUMP_BYTES}-byte floor"
	fi

	# A backup nobody can read is not a backup. `pg_restore --list` parses the
	# archive's table of contents without a server, so this catches a truncated
	# or corrupt dump here rather than during an incident.
	toc_entries="$(pg_restore --list "$dump" | grep -c '^[0-9]' || true)"
	if [ "$toc_entries" -lt 1 ]; then
		die "dump of ${db} has an unreadable or empty table of contents"
	fi

	# shellcheck disable=SC2046  # deliberate word splitting: one -r pair per recipient
	age $(age_recipient_args) --output "$encrypted" "$dump"
	assert_age_ciphertext "$encrypted"
	encrypted_bytes="$(file_size_bytes "$encrypted")"
	rm -f "$dump"

	rclone_quiet copyto "$encrypted" "${base}/daily/${object}"
	uploaded_bytes="$(remote_size_bytes "${base}/daily/${object}")"
	if [ "$uploaded_bytes" != "$encrypted_bytes" ]; then
		die "uploaded ${label} object is ${uploaded_bytes:-missing} bytes, expected ${encrypted_bytes}"
	fi
	rm -f "$encrypted"

	# The weekly and monthly tiers are server-side copies of the daily object,
	# so a longer retention costs one API call rather than a second upload.
	if [ "$RUN_DOW" = "$BACKUP_WEEKLY_DOW" ]; then
		rclone_quiet copyto "${base}/daily/${object}" "${base}/weekly/${object}"
		log "database ${label}: promoted to the weekly tier"
	fi
	if [ "$RUN_DOM" = "$BACKUP_MONTHLY_DOM" ]; then
		rclone_quiet copyto "${base}/daily/${object}" "${base}/monthly/${object}"
		log "database ${label}: promoted to the monthly tier"
	fi

	# Retention runs after the new object is confirmed present, so a failed
	# upload can never be followed by an expiry that leaves the tier emptier
	# than it started.
	rclone_quiet delete --min-age "${BACKUP_RETENTION_DAILY}d" "${base}/daily" || true
	rclone_quiet delete --min-age "$((BACKUP_RETENTION_WEEKLY * 7))d" "${base}/weekly" || true
	rclone_quiet delete --min-age "$((BACKUP_RETENTION_MONTHLY * 31))d" "${base}/monthly" || true

	ended="$(date -u +%s)"
	log "database ${label}: ${encrypted_bytes} encrypted bytes in $((ended - started))s -> ${base}/daily/${object}"
	append_report_entry "$(printf '{"database":"%s","object":"%s","toc_entries":%s,"dump_bytes":%s,"encrypted_bytes":%s,"seconds":%s}' \
		"$label" "${base}/daily/${object}" "$toc_entries" "$dump_bytes" "$encrypted_bytes" "$((ended - started))")"
}

main() {
	if [ "$DRY_RUN" = true ]; then
		log "DRY RUN -- nothing is dumped, encrypted, uploaded or deleted"
	fi

	require_binary pg_dump
	require_binary pg_restore
	require_binary psql
	require_binary age
	require_binary rclone

	require_var PGHOST_APP
	require_var PGUSER_APP
	require_var PGPASSWORD_APP
	require_var PGHOST_KC
	require_var PGUSER_KC
	require_var PGPASSWORD_KC
	require_var BACKUP_AGE_RECIPIENT

	log "run ${RUN_TIMESTAMP} (weekly tier on day ${BACKUP_WEEKLY_DOW}, today ${RUN_DOW}; monthly tier on day ${BACKUP_MONTHLY_DOM}, today ${RUN_DOM})"
	log "retention: ${BACKUP_RETENTION_DAILY} daily, ${BACKUP_RETENTION_WEEKLY} weekly, ${BACKUP_RETENTION_MONTHLY} monthly"

	if [ "$DRY_RUN" != true ]; then
		mkdir -p "$BACKUP_WORK_DIR"
		# The dumps stage on the container's ephemeral filesystem and the only
		# copy that outlives the process is the encrypted one, so the cleanup
		# has to hold even when a stage fails.
		trap 'rm -rf "$BACKUP_WORK_DIR"' EXIT
	fi

	back_up_database "movielens" "${PGHOST_APP:-}" "$PGPORT_APP" "$PGDATABASE_APP" \
		"${PGUSER_APP:-}" "${PGPASSWORD_APP:-}" "$PGSSLMODE_APP" true
	back_up_database "keycloak" "${PGHOST_KC:-}" "$PGPORT_KC" "$PGDATABASE_KC" \
		"${PGUSER_KC:-}" "${PGPASSWORD_KC:-}" "$PGSSLMODE_KC" false

	if [ "$DRY_RUN" = true ]; then
		log "DRY RUN complete"
		return 0
	fi

	printf '%s\n' "$(printf '{"timestamp":"%s","seconds":%s,"databases":[%s]}' \
		"$RUN_TIMESTAMP" "$(($(date -u +%s) - STARTED_AT))" "$REPORT_ENTRIES")"
	echo "BACKUP-OK"
}

main "$@"
