# API + pipeline worker (one process; the worker runs in a background thread).
# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.12-slim AS base

# OpenCV (used by the OCR engine) needs these shared libraries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY data/ /app/data/
COPY prompts/ /app/prompts/

RUN useradd --create-home --uid 10001 app && mkdir -p /var/data && chown app /var/data
USER app
ENV PATH="/app/backend/.venv/bin:$PATH" STORAGE_DIR=/var/data/storage
EXPOSE 8000
CMD ["sh", "-c", "uvicorn intake.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
