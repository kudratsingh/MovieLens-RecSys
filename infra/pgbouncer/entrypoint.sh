#!/bin/sh
# Render pgBouncer's production config from the environment, then exec pgBouncer.
#
# Why render instead of ship a file: a PaaS has no repository to bind-mount
# from, so the dev stack's two mounted files (pgbouncer.ini + userlist.txt) have
# no equivalent there, and baking either into an image layer would publish the
# credential that fronts every RLS-scoped connection in the system. The template
# travels in the image, the values arrive as environment variables, and nothing
# secret is committed or layered.
#
# Two client-authentication modes, chosen with PGBOUNCER_AUTH_MODE:
#
#   auth_query  pgBouncer looks each connecting role up in Postgres through
#               pgbouncer_auth.user_lookup (infra/postgres/pgbouncer-auth.sql)
#               and verifies the client against the SCRAM secret Postgres
#               already stores. Only the lookup role's own password reaches
#               disk, and rotating an application password is ALTER ROLE plus a
#               variable update. It was the target mode and it does not work
#               against a forced-user alias — see below.
#   userlist    pgBouncer reads the passwords from a userlist.txt rendered here
#               at 0600. THIS IS THE MODE THAT WORKS, and the deployment's
#               default. A stored SCRAM secret is enough to *verify* a client
#               but not to *log in* to Postgres, and both [databases] aliases
#               pin a forced user, so pgBouncer opens the server connection
#               itself instead of passing the client's exchange through -- and
#               in auth_query mode it has nothing to present. R-7 measured
#               exactly that on 2026-08-27: the client leg succeeded and the
#               server leg failed with `server login failed: FATAL password
#               authentication failed for user "app_user"`, refusing every
#               connection through both aliases. auth_query is kept because it
#               is one variable away and would become correct the moment the
#               forced users go, and switching is a variable change with no
#               rebuild.
#
# In either mode the admin user is rendered into the userlist: pgbouncer_admin
# is a pgBouncer-internal identity with no Postgres role behind it — it is what
# the API's boot check authenticates as to read SHOW POOLS / SHOW CONFIG — so an
# auth_query lookup for it would find nothing and the API would never boot.
set -eu

CONFIG_DIR=/etc/pgbouncer
CONFIG_FILE="${CONFIG_DIR}/pgbouncer.prod.ini"
USERLIST_FILE="${CONFIG_DIR}/userlist.txt"
TEMPLATE_FILE=/opt/pgbouncer/pgbouncer.prod.ini.tmpl

# Upstream Postgres. No default host: pointing the pooler at the wrong database
# server is not a failure we want to discover from a connection error.
: "${PGB_UPSTREAM_HOST:?PGB_UPSTREAM_HOST must be set to the Postgres host pgBouncer fronts}"
: "${PGB_UPSTREAM_PORT:=5432}"
: "${PGB_UPSTREAM_DB:=movielens}"

# Listener. 6432 matches the API's APP_USER_DB_PORT default (src/config.py).
: "${PGBOUNCER_LISTEN_PORT:=6432}"

# Admin console, read at boot by src/serving/startup_checks.py.
: "${PGBOUNCER_ADMIN_USER:=pgbouncer_admin}"
: "${PGBOUNCER_ADMIN_PASSWORD:?PGBOUNCER_ADMIN_PASSWORD must be set; the API authenticates to the admin console with it}"

: "${PGBOUNCER_AUTH_MODE:=userlist}"

# Pool sizing and TLS carry the dev stack's values as defaults so a deployment
# that sets neither behaves the way the load gate measured.
: "${PGB_MAX_CLIENT_CONN:=200}"
: "${PGB_DEFAULT_POOL_SIZE:=25}"
: "${PGB_RESERVE_POOL_SIZE:=5}"
: "${PGB_RESERVE_POOL_TIMEOUT:=3}"
: "${PGB_SERVER_TLS_SSLMODE:=prefer}"

# userlist.txt is one `"user" "password"` pair per line, so a value carrying a
# double quote, a space or a newline would produce a file pgBouncer parses as
# something other than what was meant — and the failure would look like a wrong
# password rather than a malformed file. Every secret in this deployment is
# generated with secrets.token_urlsafe, whose alphabet contains none of those;
# refuse anything else rather than start on an auth file that does not mean what
# it says.
assert_userlist_safe() {
    _name=$1
    _value=$2
    case ${_value} in
        '')
            echo "pgbouncer entrypoint: ${_name} is empty" >&2
            exit 1
            ;;
        *'"'* | *' '* | *"$(printf '\t')"*)
            echo "pgbouncer entrypoint: ${_name} contains a quote, space or tab, which userlist.txt cannot represent" >&2
            exit 1
            ;;
    esac
    if [ "$(printf '%s' "${_value}" | wc -l | tr -d ' ')" != "0" ]; then
        echo "pgbouncer entrypoint: ${_name} contains a newline, which userlist.txt cannot represent" >&2
        exit 1
    fi
}

append_userlist_entry() {
    assert_userlist_safe "$2" "$3"
    printf '"%s" "%s"\n' "$1" "$3" >>"${USERLIST_FILE}"
}

# Anything this script creates is for pgBouncer's own user and nobody else.
umask 077
mkdir -p "${CONFIG_DIR}"
: >"${USERLIST_FILE}"
# The image ships an empty world-readable userlist.txt, and truncating a file
# does not change its mode.
chmod 0600 "${USERLIST_FILE}"

append_userlist_entry "${PGBOUNCER_ADMIN_USER}" PGBOUNCER_ADMIN_PASSWORD "${PGBOUNCER_ADMIN_PASSWORD}"

case "${PGBOUNCER_AUTH_MODE}" in
auth_query)
    : "${PGBOUNCER_AUTH_USER:=pgbouncer_auth}"
    : "${PGBOUNCER_AUTH_PASSWORD:?PGBOUNCER_AUTH_PASSWORD must be set when PGBOUNCER_AUTH_MODE=auth_query}"
    # pgBouncer takes auth_user's own password from auth_file — the lookup role
    # is the one credential this mode cannot delegate to the lookup.
    append_userlist_entry "${PGBOUNCER_AUTH_USER}" PGBOUNCER_AUTH_PASSWORD "${PGBOUNCER_AUTH_PASSWORD}"
    # The schema and function name are fixed by infra/postgres/pgbouncer-auth.sql.
    # The placeholder in auth_query below is escaped: it is pgBouncer's query
    # parameter, and this heredoc would otherwise expand it away to nothing.
    PGB_AUTH_SECTION=$(
        cat <<INI
; Client authentication against the SCRAM secrets Postgres already stores,
; fetched through a SECURITY DEFINER lookup owned by the superuser and
; executable only by a login role that can do nothing else
; (infra/postgres/pgbouncer-auth.sql). No application password exists in this
; file, in the image, or in the repository: rotation is ALTER ROLE ... PASSWORD
; plus a variable update, with no redeploy of the pooler.
auth_type = scram-sha-256
auth_file = ${USERLIST_FILE}
auth_user = ${PGBOUNCER_AUTH_USER}
auth_query = SELECT usename, passwd FROM pgbouncer_auth.user_lookup(\$1)
INI
    )
    ;;
userlist)
    : "${APP_USER_DB_PASSWORD:?APP_USER_DB_PASSWORD must be set when PGBOUNCER_AUTH_MODE=userlist}"
    : "${ADMIN_USER_DB_PASSWORD:?ADMIN_USER_DB_PASSWORD must be set when PGBOUNCER_AUTH_MODE=userlist}"
    # Only the two roles that reach Postgres through the pooler. migrator and
    # the superuser connect direct, so their passwords have no business here.
    append_userlist_entry app_user APP_USER_DB_PASSWORD "${APP_USER_DB_PASSWORD}"
    append_userlist_entry admin_user ADMIN_USER_DB_PASSWORD "${ADMIN_USER_DB_PASSWORD}"
    # Read by the template, not by this script: pgbouncer.prod.ini.tmpl carries
    # a ${PGB_AUTH_SECTION} placeholder that the `eval "cat <<EOF"` at the end
    # expands. shellcheck cannot see through that indirection.
    # shellcheck disable=SC2034
    PGB_AUTH_SECTION=$(
        cat <<INI
; Client authentication against a userlist rendered from the environment at
; container start, mode 0600 and never written to an image layer. The passwords
; are plain text in that file, which is what lets pgBouncer both verify a
; SCRAM client and complete SCRAM against Postgres as a forced user — the
; property auth_query mode turned out not to have (R-7, 2026-08-27).
auth_type = scram-sha-256
auth_file = ${USERLIST_FILE}
INI
    )
    ;;
*)
    echo "pgbouncer entrypoint: PGBOUNCER_AUTH_MODE must be 'auth_query' or 'userlist', got '${PGBOUNCER_AUTH_MODE}'" >&2
    exit 1
    ;;
esac

[ -r "${TEMPLATE_FILE}" ] || {
    echo "pgbouncer entrypoint: template ${TEMPLATE_FILE} is missing from the image" >&2
    exit 1
}

# The template is expanded by the shell, which means a backtick or a $( ) in it
# would be *run*, not printed — a comment mentioning the pgbouncer binary in
# backticks silently executed it and rendered an empty word in its place. That
# is a config-corrupting edit that looks harmless in review, so refuse it here
# rather than let a container boot on the result.
if grep -q '[`]' "${TEMPLATE_FILE}" || grep -q '[$][(]' "${TEMPLATE_FILE}"; then
    echo "pgbouncer entrypoint: ${TEMPLATE_FILE} contains a backtick or \$( ); the renderer would execute it. Use plain text." >&2
    exit 1
fi

# Expand the template's ${...} placeholders with the shell itself, so the image
# needs no envsubst and no interpreter beyond /bin/sh. Values substituted in are
# not re-expanded, so an environment value containing $( ) or a backtick lands
# as text. set -u above makes a placeholder nobody set a hard failure rather
# than an empty setting.
eval "cat <<PGB_RENDER_EOF
$(cat "${TEMPLATE_FILE}")
PGB_RENDER_EOF" >"${CONFIG_FILE}"
chmod 0600 "${CONFIG_FILE}"

printf 'pgbouncer entrypoint: rendered %s — auth_mode=%s upstream=%s:%s/%s listen=*:%s pool_mode=transaction\n' \
    "${CONFIG_FILE}" "${PGBOUNCER_AUTH_MODE}" "${PGB_UPSTREAM_HOST}" "${PGB_UPSTREAM_PORT}" \
    "${PGB_UPSTREAM_DB}" "${PGBOUNCER_LISTEN_PORT}"

exec "$@"
