FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Compile bytecode to speed up cold starts; copy mode avoids hard-link issues
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Create non-root user before installing deps (avoids chown-R layer duplication)
RUN adduser --disabled-password --gecos "" appuser

# Install dependencies first (cached when pyproject.toml/uv.lock unchanged)
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source
COPY --chown=appuser:appuser app/ app/
COPY --chown=appuser:appuser prompts/ prompts/
COPY --chown=appuser:appuser alembic/ alembic/
COPY --chown=appuser:appuser alembic.ini ./

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    LOG_FORMAT=json

EXPOSE 8080

CMD ["python", "-m", "app"]
