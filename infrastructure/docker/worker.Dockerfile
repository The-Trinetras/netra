# Netra worker image (leased PostgreSQL jobs: ingestion, OCR, projections).
# Build from the repository root:
#   docker build -f infrastructure/docker/worker.Dockerfile -t netra-worker .
#
# Same Python, uv and locked environment as the API image. The worker also
# needs the Tesseract binary and English language data for the OCR fallback;
# record the installed Tesseract version with the image digest at deploy time
# (runtime-baseline: "Record the Tesseract binary build and OCR language-data
# versions").

FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.13.15-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY api ./api
COPY worker ./worker
COPY shared ./shared

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/api/src:/app/worker/src \
    NETRA_TESSERACT_EXECUTABLE=/usr/bin/tesseract

RUN useradd --system --uid 10001 --home-dir /app netra && chown -R netra /app
USER netra

# SIGTERM stops claiming, finishes or releases the current attempt, then flushes
# telemetry with a bounded deadline (worker/src/netra_worker/main.py).
STOPSIGNAL SIGTERM
CMD ["python", "-m", "netra_worker.main"]
