# Netra API image. Build from the repository root:
#   docker build -f infrastructure/docker/api.Dockerfile -t netra-api .
#
# Python and uv versions match .python-version and the uv that wrote uv.lock.
# Dependencies come only from the committed lock (`uv sync --locked`): a
# manifest/lock mismatch fails the build instead of resolving something new.
# Record the resolved image digests in infrastructure/aws/RUNBOOK.md when an
# image is first built for deployment.

FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.13.15-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# Dependency layer first, so code changes do not reinstall packages.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY api ./api
COPY worker ./worker
COPY shared ./shared

# The root manifest is an environment, not a package: code runs from source.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/api/src:/app/worker/src

RUN useradd --system --uid 10001 --home-dir /app netra && chown -R netra /app
USER netra

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"

# One process per container; TLS and client addresses come from nginx.
CMD ["uvicorn", "--factory", "netra_api.main:create_app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
