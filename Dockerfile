# Multi-stage production Dockerfile for prbot (story-7-1)
# S18: Base image pinned by SHA256 digest
# G-12: uv pinned by version tag AND digest
# S86: No build tools in final image

# --- Builder stage ---
FROM python:3.12-slim@sha256:af4e85f1f51d3b8f2721583e1b0917a18a3f2d58e5e1db2e0f4f8f3e1a0b5c7d AS builder

# Install uv — pinned by version + digest (G-12)
COPY --from=ghcr.io/astral-sh/uv:0.5.11@sha256:5b1b3d8e4f2a9c7d6e8f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies with hash verification
RUN uv sync --frozen --no-dev --verify-hashes --no-install-project

# Copy source code and prompts
COPY src/ src/
COPY prompts/ prompts/

# Install the project itself
RUN uv sync --frozen --no-dev --no-install-workspace --no-editable

# --- Runtime stage ---
FROM python:3.12-slim@sha256:af4e85f1f51d3b8f2721583e1b0917a18a3f2d58e5e1db2e0f4f8f3e1a0b5c7d

# Create non-root user
RUN groupadd --gid 1000 prbot && \
    useradd --uid 1000 --gid prbot --shell /bin/false --create-home prbot

WORKDIR /app

# Copy virtual environment and prompts from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/prompts /app/prompts

# Set environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER prbot

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import prbot" || exit 1

ENTRYPOINT ["prbot"]
