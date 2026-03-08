from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MCP server
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 9001

    # Feature gates — read from same .env as homeagent
    feature_bash: bool = False
    feature_python: bool = False
    feature_scrape: bool = False
    feature_search: bool = False

    # Workspace root (set to /workspace inside Docker)
    workspace_dir: str = "/workspace"

    # Bash tool
    bash_allowed_commands: list[str] = []
    bash_max_timeout_seconds: int = 60
    bash_max_output_bytes: int = 200_000

    # Python exec tool
    python_max_timeout_seconds: int = 60
    python_max_output_bytes: int = 200_000

    # Web scraping tool
    scrape_timeout_seconds: int = 30
    scrape_max_content_bytes: int = 100_000

    # Web search tool
    search_provider: str = "tavily"
    tavily_api_key: str = ""
    search_max_results: int = 5

    @field_validator("bash_allowed_commands", mode="before")
    @classmethod
    def parse_str_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
