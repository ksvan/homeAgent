from __future__ import annotations

from functools import lru_cache

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

    # Bash tool — CSV string to avoid pydantic-settings JSON-parsing list fields from env vars
    bash_allowed_commands: str = ""
    bash_max_timeout_seconds: int = 60
    bash_max_output_bytes: int = 200_000

    # Python exec tool
    python_max_timeout_seconds: int = 60
    python_max_output_bytes: int = 200_000

    # Web scraping tool
    scrape_timeout_seconds: int = 30
    scrape_max_content_bytes: int = 100_000

    # SharePoint tool
    feature_sharepoint: bool = False
    sharepoint_timeout_seconds: int = 30
    sharepoint_max_file_bytes: int = 10_000_000   # 10 MB raw cap
    sharepoint_max_content_bytes: int = 100_000   # text output cap

    # Web search tool
    search_provider: str = "tavily"
    tavily_api_key: str = ""
    search_max_results: int = 5

    def bash_allowed_commands_list(self) -> list[str]:
        """Parse the CSV bash_allowed_commands string into a list."""
        return [x.strip() for x in self.bash_allowed_commands.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
