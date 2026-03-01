FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Compile bytecode to speed up cold starts; copy mode avoids hard-link issues
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached when pyproject.toml/uv.lock unchanged)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source
COPY app/ app/
COPY prompts/ prompts/
COPY alembic/ alembic/
COPY alembic.ini ./

# Run as non-root
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    LOG_FORMAT=json

EXPOSE 8080

CMD ["python", "-m", "app"]
