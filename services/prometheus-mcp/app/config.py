from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Walk up from this file looking for a .env (works locally).
# In Docker no .env exists on any parent — pydantic-settings silently skips it.
_ROOT_ENV = next(
    (p / ".env" for p in Path(__file__).resolve().parents if (p / ".env").exists()),
    Path("/nonexistent/.env"),
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Prometheus backend
    # ------------------------------------------------------------------
    prometheus_url: str = "http://localhost:9090"

    # Optional Bearer token for Prometheus auth (leave empty for unauthenticated LAN)
    prometheus_bearer_token: str = ""

    # ------------------------------------------------------------------
    # MCP server
    # ------------------------------------------------------------------
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 9000

    # ------------------------------------------------------------------
    # Guardrails — numeric limits only
    # ------------------------------------------------------------------
    # Request timeout when calling the Prometheus API
    prom_timeout_seconds: float = 10.0

    # Maximum range window for prom_query_range (hours)
    prom_max_range_hours: int = 24

    # Minimum step for prom_query_range (seconds) — prevents overly dense queries
    prom_min_step_seconds: int = 60

    # Maximum number of series returned by a single query
    prom_max_series: int = 80

    # Maximum total datapoints across all series (pre-checked from range/step)
    prom_max_datapoints: int = 10_000

    # Maximum response body size in bytes (applied before parsing)
    prom_max_response_bytes: int = 2_000_000

    # Optional metric name prefix allowlist (comma-separated). Empty = allow all.
    # Example: "node_,process_,up" — queries not containing any of these prefixes
    # as a substring will be rejected.
    prom_metric_prefix_allowlist: list[str] = []

    def prom_headers(self) -> dict[str, str]:
        if self.prometheus_bearer_token:
            return {"Authorization": f"Bearer {self.prometheus_bearer_token}"}
        return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
