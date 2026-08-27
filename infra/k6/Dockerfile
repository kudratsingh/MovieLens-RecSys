# The `loadcheck` job's image: k6 with the load scripts baked in.
#
# The demo stack bind-mounts `./synthetic/load` into the stock `grafana/k6`
# image (docker-compose.demo.yml:158). Production cannot: the host never builds
# and never mounts a source tree into a container, it pulls images CI published
# at a commit. Baking the scripts is what makes `make prod-load` on the box run
# the scripts that commit describes rather than whatever happens to be in the
# checkout.
#
# Build from the repository root, because the COPY path is repo-root-relative:
#   docker build -f infra/k6/Dockerfile -t recsys-k6 .

# Kept in lockstep with infra/ci/k6-version by
# tests/unit/test_deploy_workflows.py, which is also where CI's publish job is
# pinned. A Dockerfile cannot read a file to build its own FROM line, so the pin
# is duplicated here and the duplicate is asserted rather than trusted: ADR 0010
# makes this version the thing that stops local and CI measurements drifting
# apart, and a canary running a different k6 than the gate would quietly undo
# that.
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

# The base image sets ENTRYPOINT ["k6"], which makes any start command written
# as `k6 run ...` expand to `k6 k6 run ...` wherever the command is appended to
# the entrypoint rather than replacing it. Clearing it leaves one unambiguous
# reading: the CMD below is the whole command, `docker compose run loadcheck k6
# run /scripts/pages.js` says what it looks like it says, and the image is
# runnable on its own.
ENTRYPOINT []
CMD ["k6", "run", "/scripts/recommendations.js"]
