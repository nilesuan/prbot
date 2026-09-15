# Multi-stage production Dockerfile for prbot (story-7-1)
# S18: Base image pinned by SHA256 digest
# G-12: uv pinned by version tag AND digest
# S86: No build tools in final image

# --- Builder stage ---
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

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
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# Apply Debian security updates on top of the pinned base. The digest pin
# fixes what is built from; it does not stop the packages inside it ageing,
# and every CRITICAL and HIGH in this image was a Debian package with a
# security fix already published. This trades some build reproducibility for
# a scannable image, which is the right way round for something that ships.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user, and strip the installers the base image ships with.
# S86 asks for no build tools in the final image; the virtual environment
# never had them, but python:N-slim carries pip, setuptools and wheel in the
# system interpreter and PATH finds them.
#
# The site-packages path is asked of the interpreter rather than written out.
# It used to be hardcoded to python3.12, so bumping the base to 3.14 pointed
# every rm at a path that did not exist. rm -rf succeeds on a missing path,
# so the build stayed green while pip survived in the image, and pip vendors
# its own copy of msgpack, which is how a base image bump arrived carrying
# two HIGH advisories that the previous image did not have.
#
# The import checks at the end are the actual guard. Without them this is a
# command that cannot fail, and a strip that silently removes nothing looks
# exactly like one that worked.
RUN groupadd --gid 1000 prbot && \
    useradd --uid 1000 --gid prbot --shell /bin/false --create-home prbot && \
    SITE="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" && \
    rm -rf "$SITE"/pip* "$SITE"/setuptools* "$SITE"/wheel* \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* && \
    if python -c 'import pip' 2>/dev/null; then echo "pip survived the strip in $SITE" >&2; exit 1; fi && \
    if python -c 'import setuptools' 2>/dev/null; then echo "setuptools survived the strip in $SITE" >&2; exit 1; fi

WORKDIR /app

# Copy virtual environment from builder (prompts are bundled as package data)
COPY --from=builder /app/.venv /app/.venv

# Set environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER prbot

# ENTRYPOINT, not CMD: with only a CMD, `docker run image --dry-run` replaces
# the command with "--dry-run" and the container fails with "executable file
# not found". Arguments now reach prbot, which is what the documented
# `docker run ... --platform github` invocations need. GitLab Runner overrides
# the entrypoint for job images, so its `script:` block is unaffected.
ENTRYPOINT ["prbot"]
CMD []
