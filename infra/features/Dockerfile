FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Fail-safe default: every guard in src/config.py keys off ENVIRONMENT, so a
# deployment that forgets the variable would otherwise run with dev-only
# credentials accepted. Compose sets ENVIRONMENT=dev explicitly on the
# services that want the development defaults.
ENV ENVIRONMENT=production

# ADR 0010 treats single-threaded BLAS/OpenMP as a serving invariant, not a
# local test convenience: the ranker runs one small scoring batch per request
# under a multi-worker uvicorn, so every thread a library spawns per worker is
# contention, not throughput. Left unpinned, the measured p99 was 903.64 ms at
# 0% CPU steal; pinning these four alone brought it to 48.99 ms. Baked here so
# the invariant travels with the image instead of depending on a variable panel.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1

WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY infra/features/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

# The CPU pin above is easy to lose in a future edit — a plain `torch==` line
# resolves to the CUDA wheel on this platform and inflates the image by roughly
# an order of magnitude. A developer machine cannot catch that: macOS has no
# CUDA build, so the mistake is invisible until it reaches a linux/amd64 build.
# So the build asserts on the result rather than trusting the pin, and fails
# here instead of shipping a GPU runtime to a box that has no GPU.
RUN set -eu; \
    if pip list --format=freeze | grep -qi '^nvidia-'; then \
        echo "refusing: CUDA packages present; the torch pin lost its +cpu build" >&2; \
        pip list --format=freeze | grep -i '^nvidia-' >&2; \
        exit 1; \
    fi; \
    python -c "import torch, faiss; print('torch', torch.__version__, '| faiss', faiss.__version__)"
COPY src ./src

# feature_store.yaml interpolates these unconditionally, so `feast apply` fails
# on interpolation long before it would fail on connectivity. They are ARG and
# not ENV deliberately: an ENV default would still be set at runtime, so a
# deployment that forgot to pass the real online/offline store would quietly
# resolve to a host called `build` instead of refusing to start. Nothing here
# reaches either store — verified by running the same apply with
# `docker run --network none`, which exits 0 and writes the registry.
ARG FEAST_POSTGRES_HOST=build
ARG FEAST_POSTGRES_PORT=5432
ARG FEAST_POSTGRES_DB=build
ARG FEAST_POSTGRES_USER=build
ARG FEAST_POSTGRES_PASSWORD=build
ARG REDIS_CONNECTION_STRING=build:6379

# Bake the Feast registry. The feature views declare explicit schemas
# (src/features/feast_repo/features.py), so `apply` never has to infer one from
# the offline store, which is what makes it a build step rather than runtime
# state on a shared volume — baking it is also what makes rolling the image
# back roll the registry back with it (ADR 0013). The check that
# follows is semantic and never a checksum: apply writes a protobuf carrying
# `last_updated`, so byte equality would fail every build.
RUN feast -c src/features/feast_repo apply \
    && python -m src.features.registry_check

# The migration graph, for reading only. This image runs no migrations — the
# release job in the API image does — but it does run the model-server's
# pre-deploy fence (`src.release.bootstrap materialize --wait-for-schema`), and
# that fence has to answer two questions about the database it is waiting for:
# is it at this release's head, and if it is not, is it *ahead* of this image
# because a rollback is in progress. Both are questions about which revisions
# this image knows, so the image has to carry them. Copied after `feast apply`
# so a migration never invalidates the registry layer.
COPY alembic.ini ./
COPY alembic ./alembic

# The serving bundle is a build input, not runtime state: it is a pure function
# of the committed persona fixture, whose epoch and catalog snapshot are frozen.
# Baking it makes a model rollback an image rollback, and the manifest's
# SHA-256s are re-verified by ServingArtifactBundle.load on every boot.
COPY infra/model-bundle/ ./models/serving/

# The second bundle, at its own path. The image carries both — the compact demo
# fixture above at /app/models/serving, and the full-data served bundle here at
# /app/models/served-bundle — and MODEL_ARTIFACT_DIR picks one. Which bundle an
# image serves is therefore an environment decision readable off a compose file,
# rather than a build-time one invisible from outside the image.
# docker-compose.demo.yml keeps the fixture; docker-compose.prod.yml names the
# served path as a literal, because production serving the compact fixture must
# not be one forgotten variable away.
#
# Two paths a directory apart rather than a filename apart, for the same reason
# infra/model-bundle-served/ is its own directory: the bundles have the same
# file layout, so nothing but the path tells them apart until something opens
# bundle-kind.json.
#
# infra/model-bundle-served/ ships a .gitkeep so this COPY works before the
# payload lands — a COPY of a missing directory fails the build outright. Until
# a bundle is published into it the directory holds no manifest, and a container
# pointed at it refuses to boot rather than falling back to the fixture. That is
# ADR 0013's rule and not an oversight: a deployment that cannot realise the
# bundle it was handed must stop, never quietly serve something else.
COPY infra/model-bundle-served/ ./models/served-bundle/

RUN useradd --create-home --uid 10001 feastuser \
    && mkdir -p /app/models/serving \
    && chown -R feastuser:feastuser /app
USER feastuser

EXPOSE 6566
CMD ["feast", "-c", "src/features/feast_repo", "serve", "--host", "0.0.0.0", "--port", "6566"]
