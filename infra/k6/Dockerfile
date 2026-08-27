# The `loadcheck` job's image: k6 with the load scripts baked in.
#
# Compose bind-mounts `./synthetic/load` into the stock `grafana/k6` image
# (docker-compose.demo.yml:158). A PaaS service has no repository to mount
# from, so the scripts have to travel inside an image — which is the whole
# reason this file exists.
#
# Build from the repository root, because the COPY path is repo-root-relative:
#   docker build -f infra/k6/Dockerfile -t recsys-k6 .

# Kept in lockstep with infra/ci/k6-version by
# tests/unit/test_railway_config.py. A Dockerfile cannot read a file to build
# its own FROM line, so the pin is duplicated here and the duplicate is
# asserted rather than trusted: ADR 0010 makes this version the thing that
# stops local and CI measurements drifting apart, and a canary running a
# different k6 than the gate would quietly undo that.
ARG K6_VERSION=2.1.0
FROM grafana/k6:${K6_VERSION}

USER root

# The whole directory, not a hand-picked list. `recommendations.js` imports
# `lib/auth.js`, `thresholds.js` and `canary_thresholds.js`, and a missing
# import is a runtime failure inside the job rather than a build failure here —
# so the safe unit of copying is the directory the scripts already treat as
# theirs. The Python helpers and run_gate.sh ride along unused; they are a few
# kilobytes and leaving them out would mean maintaining a list that has to be
# updated every time an import moves.
COPY synthetic/load /scripts

# `handleSummary` writes ${RESULTS_DIR}/summary.json (recommendations.js:425).
# k6 is not relied on to create the parent directory: the job's summary is the
# evidence the deploy workflow reads back, and losing it to a missing directory
# would make a passing canary look like a silent one. 0777 because the runtime
# user is the image's unprivileged k6 account.
RUN mkdir -p /tmp/results && chmod 0777 /tmp/results

USER 12345

# The base image sets ENTRYPOINT ["k6"], which would make a platform start
# command of `k6 run ...` expand to `k6 k6 run ...` on any platform that
# appends the start command to the entrypoint rather than replacing the whole
# command. Clearing it makes `infra/railway/loadcheck.json`'s start command
# correct under either semantics, and leaves the image runnable on its own.
ENTRYPOINT []
CMD ["k6", "run", "/scripts/recommendations.js"]
