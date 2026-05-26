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

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy the source code and necessary files
COPY src src
COPY alembic alembic
COPY alembic.ini .

# Create data directory
RUN mkdir -p data

EXPOSE 8000

# Run uvicorn directly
CMD ["uvicorn", "denoiser.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
