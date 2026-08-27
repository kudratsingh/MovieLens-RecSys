FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Fail-safe default: every guard in src/config.py keys off ENVIRONMENT, so a
# deployment that forgets the variable would otherwise run with dev-only
# credentials accepted. Compose sets ENVIRONMENT=dev explicitly on the
# services that want the development defaults.
ENV ENVIRONMENT=production

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY infra/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

COPY src ./src

COPY alembic.ini ./
COPY alembic ./alembic
COPY synthetic ./synthetic

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.serving.app:app", "--host", "0.0.0.0", "--port", "8000"]
