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
COPY src ./src
RUN useradd --create-home --uid 10001 feastuser \
    && mkdir -p /app/models/serving \
    && chown -R feastuser:feastuser /app
USER feastuser

EXPOSE 6566
CMD ["feast", "-c", "src/features/feast_repo", "serve", "--host", "0.0.0.0", "--port", "6566"]
