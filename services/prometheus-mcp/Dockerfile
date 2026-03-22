FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

COPY app/ app/

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app
EXPOSE 9000
CMD ["python", "-m", "app.main"]
