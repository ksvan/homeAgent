FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install common shell tools used by the bash runner
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq ripgrep git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

COPY app/ app/

# Non-root user; pre-create workspace mount point
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /workspace \
    && chown appuser:appuser /workspace /app
USER appuser

ENV PYTHONPATH=/app
EXPOSE 9001
CMD ["python", "-m", "app.main"]
