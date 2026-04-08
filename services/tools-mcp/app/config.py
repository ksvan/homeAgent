from __future__ import annotations

import os
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

    # Subprocess env passthrough — CSV list of env var names forwarded to
    # bash and python subprocesses. Use for skill credentials and API keys.
    # Example: TOOLS_PASSTHROUGH_ENV=VEGVESEN_DATEX_USERNAME,VEGVESEN_DATEX_PASSWORD
    tools_passthrough_env: str = ""

    def bash_allowed_commands_list(self) -> list[str]:
        """Parse the CSV bash_allowed_commands string into a list."""
        return [x.strip() for x in self.bash_allowed_commands.split(",") if x.strip()]

    def passthrough_env_dict(self) -> dict[str, str]:
        """Return env vars that should be forwarded to subprocesses."""
        result = {}
        for name in self.tools_passthrough_env.split(","):
            name = name.strip()
            if name and name in os.environ:
                result[name] = os.environ[name]
        return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
