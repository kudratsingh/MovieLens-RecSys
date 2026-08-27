# Production Keycloak image (ADR 0007).
#
# Two stages so the running container starts with `start --optimized`: the
# augmentation Keycloak would otherwise perform on every boot is done once,
# here, and the result is frozen with the Postgres store and the health
# endpoints already compiled in. That matters more than the seconds it saves —
# `--optimized` refuses to start if a build option is contradicted at runtime,
# so the image, not the environment, is what decides how this server is built.
#
# Deliberately no `--import-realm`. An import never overwrites a realm that
# already exists in the database, so after the first boot an edited template
# would look applied and change nothing. Realms come from provision.sh, run as
# its own one-shot job against the running server.

FROM quay.io/keycloak/keycloak:25.0 AS builder

# Build-time options only. Health is enabled here because the runtime flag
# alone does nothing on an optimized server.
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true

FROM quay.io/keycloak/keycloak:25.0

COPY --from=builder --chown=1000:0 /opt/keycloak/ /opt/keycloak/

# Restating the two build options keeps `kc.sh show-config` honest about what
# this image was built with. They must match the builder stage above; every
# other setting (database host, hostname, proxy headers) is runtime
# configuration and belongs to the environment.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true

# The realm templates and the provisioning script ride along in the image, so
# the job that provisions a server is the same artifact as the server it
# provisions. provision.sh finds its templates relative to itself, which is why
# this layout mirrors the repository's.
COPY --chown=1000:0 infra/keycloak/realms/prod/ /opt/keycloak/realms/prod/
COPY --chown=1000:0 infra/keycloak/provision.sh /opt/keycloak/provision.sh

EXPOSE 8080

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized"]
