# Production pgBouncer — the dev stack's pooler, configured from the
# environment instead of from two bind-mounted files.
#
# Same upstream image and tag docker-compose.yml pins, deliberately: the pooler
# that fronts production is then the one every Phase 3 latency measurement and
# every tenant-isolation run has already been taken against. The only additions
# are the config template and the entrypoint that renders it.
#
# Build from the repository root:
#   docker build -f infra/pgbouncer/Dockerfile -t recsys-pgbouncer .
FROM edoburu/pgbouncer:v1.24.1-p1

# root only long enough to install two files. The pooler itself keeps the
# image's unprivileged postgres user.
USER root

COPY infra/pgbouncer/pgbouncer.prod.ini.tmpl /opt/pgbouncer/pgbouncer.prod.ini.tmpl
COPY infra/pgbouncer/entrypoint.sh /usr/local/bin/pgbouncer-entrypoint.sh

# /etc/pgbouncer is where the entrypoint writes the rendered config and the
# auth file, so it has to be writable by the runtime user — the base image
# leaves it group-owned by root.
RUN chmod 0755 /usr/local/bin/pgbouncer-entrypoint.sh \
    && chmod 0444 /opt/pgbouncer/pgbouncer.prod.ini.tmpl \
    && chown postgres:postgres /etc/pgbouncer

USER postgres

EXPOSE 6432

ENTRYPOINT ["/usr/local/bin/pgbouncer-entrypoint.sh"]
CMD ["/usr/bin/pgbouncer", "/etc/pgbouncer/pgbouncer.prod.ini"]
