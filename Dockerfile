# UNVALIDATED: written without a Docker daemon or network. First real test is the CI `docker` job.
# Multi-stage, pinned base tag (pin by digest once resolved: `docker buildx imagetools inspect`),
# non-root, no secrets baked in. The index is built from the SYNTHETIC fixtures at build time.

FROM python:3.15-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
COPY pyproject.toml ./
COPY src ./src
COPY fixtures ./fixtures
ENV PYTHONPATH=/build/src
RUN mkdir -p /build/data && /opt/venv/bin/python -m ota.cli --db /build/data/index.db ingest --source fixtures --fixtures-dir fixtures

FROM python:3.15-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH OTA_DB=/app/data/index.db
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app src ./src
COPY --chown=app:app --from=build /build/data ./data
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"
# Default: offline extractive model. Use `--model claude` and inject ANTHROPIC_API_KEY at runtime.
ENTRYPOINT ["python", "-m", "ota.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
