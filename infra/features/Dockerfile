FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

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
