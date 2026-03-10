# Multi-stage production Dockerfile for prbot (story-7-1)
# S18: Base image pinned by SHA256 digest
# G-12: uv pinned by version tag AND digest
# S86: No build tools in final image

# --- Builder stage ---
FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0 AS builder

# Install uv — pinned by version + digest (G-12)
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies with hash verification
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code (prompts are package data in src/prbot/prompts/)
COPY src/ src/

# Install the project itself
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime stage ---
FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0

# Create non-root user
RUN groupadd --gid 1000 prbot && \
    useradd --uid 1000 --gid prbot --shell /bin/false --create-home prbot

WORKDIR /app

# Copy virtual environment from builder (prompts are bundled as package data)
COPY --from=builder /app/.venv /app/.venv

# Set environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER prbot

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import prbot" || exit 1

CMD ["prbot"]
