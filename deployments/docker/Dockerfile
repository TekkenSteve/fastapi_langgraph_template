ARG PY_VERSION=3.12
FROM python:${PY_VERSION}-slim-bookworm AS base
ARG PY_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /app

# Create non-root user for runtime
RUN addgroup --system app && adduser --system --ingroup app app

# -----------------------------
# Builder stage
# -----------------------------
FROM base AS builder

# Retrieve the uv binary directly from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /bin/uv

# Copy package files and lockfile
COPY pyproject.toml README.md ./
COPY uv.lock ./

# Install dependencies from lockfile (includes dev deps for example agents).
# All dependencies ship manylinux wheels, so no compiler/toolchain is needed.
RUN uv export --frozen --no-emit-project --format=requirements-txt > requirements.txt && \
    uv pip install --system --compile-bytecode -r requirements.txt && \
    rm requirements.txt

# Copy the project source (alembic assets ship inside the package).
COPY src/ ./src/

# Install the project package itself.
RUN uv pip install --system --compile-bytecode --no-deps .

# -----------------------------
# Final, minimal runtime image
# -----------------------------
FROM base AS final

# ARGs are stage-scoped: final must re-declare PY_VERSION or the COPY below
# expands to /usr/local/lib/python/site-packages (empty) and the build fails.
ARG PY_VERSION

# The slim base already ships ca-certificates (needed for LLM API TLS).

# Copy installed Python packages from the builder stage.
COPY --from=builder /usr/local/lib/python${PY_VERSION}/site-packages/ /usr/local/lib/python${PY_VERSION}/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy runtime assets required by the app
COPY langgraph.json ./langgraph.json
COPY src/graphs/ ./src/graphs/
COPY src/shop/ ./src/shop/
COPY src/hub/ ./src/hub/
COPY src/ml/ ./src/ml/
COPY src/http_app.py ./src/http_app.py
COPY deployments/docker/entrypoint.sh ./entrypoint.sh
# Shell scripts must be READABLE by the runtime user (bash reads them).
RUN chmod 755 ./entrypoint.sh

EXPOSE 2026

# Run as non-root
USER app

# entrypoint.sh handles optional custom-CA injection, then execs the CMD.
ENTRYPOINT ["./entrypoint.sh"]

# Default command - can be overridden by docker-compose
CMD ["uvicorn", "agent_server.app.main:app", "--host", "0.0.0.0", "--port", "2026"]
