# Backup and restore-drill image.
#
# postgres:16 is the base because pg_dump refuses a server newer than itself and
# the Railway Postgres template is 16 — the client major has to track it, and the
# API image (python:3.11-slim) carries no Postgres client tools at all. Pinning
# the base here means a template upgrade breaks loudly at pg_dump's own version
# check rather than quietly producing dumps nobody can restore.
#
# age does the client-side encryption and rclone the off-provider copy; both come
# from Debian bookworm's archive, so the image downloads nothing of its own.
# curl belongs to restore-drill.sh, which polls a scratch API's /readyz.
FROM postgres:16

ENV LANG=C.UTF-8 \
    PGCONNECT_TIMEOUT=15

RUN apt-get update \
    && apt-get install --yes --no-install-recommends age ca-certificates curl rclone \
    && rm -rf /var/lib/apt/lists/*

COPY infra/backup/backup.sh infra/backup/restore-drill.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/restore-drill.sh

# The base image's entrypoint exists to initialise and start a server. This image
# only ever runs client tools, so it is cleared rather than worked around.
ENTRYPOINT []

# Dumps stage on the ephemeral filesystem and the service has no volume, so the
# work directory is a plain writable path and the running identity needs nothing
# beyond it.
USER postgres
WORKDIR /tmp

CMD ["backup.sh"]
