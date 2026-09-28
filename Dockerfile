FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Enable bytecode compilation and specify the virtual environment path
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy package requirements
COPY pyproject.toml uv.lock ./
# Install build tools required for C extensions (e.g. hdbscan, numpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies (without the project itself yet)
RUN uv sync --frozen --no-install-project --no-dev

# Copy project source code
COPY src src
COPY alembic alembic
COPY alembic.ini .
COPY README.md .

# Install the project
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runner

LABEL org.opencontainers.image.title="SemanticOS API" \
      org.opencontainers.image.source="https://github.com/ATULSHARMA1234/DE-Noiser" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Run as a non-root user (uid must match the Helm chart's securityContext).
RUN groupadd --system --gid 1001 semanticos \
 && useradd --system --uid 1001 --gid semanticos --home-dir /app --shell /usr/sbin/nologin semanticos

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy the source code and necessary files
COPY src src
COPY alembic alembic
COPY alembic.ini .

# Create the data directory and hand ownership to the non-root user.
RUN mkdir -p data && chown -R semanticos:semanticos /app

USER semanticos

EXPOSE 8000

# Liveness probe for plain `docker run` / compose (K8s uses the chart's probes).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/live', timeout=3).status==200 else 1)" || exit 1

# Run uvicorn directly
CMD ["uvicorn", "denoiser.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
