from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FeatureFlags(BaseModel):
    cheap_background_models: bool = True
    fallback_model: bool = True
    policy_gate: bool = True
    action_verify: bool = True
    whatsapp: bool = False
    voice: bool = False
    multi_home: bool = False
    local_model: bool = False
    bash: bool = False
    python: bool = False
    scrape: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM providers
    # ------------------------------------------------------------------
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------
    model_primary: str = "claude-sonnet-4-5"
    model_background: str = "claude-haiku-4-5-20251001"
    model_fallback: str = "gpt-4o"
    model_background_fallback: str = "gpt-4o-mini"
    model_embedding: str = "text-embedding-3-small"

    # ------------------------------------------------------------------
    # Token limits
    # ------------------------------------------------------------------
    max_tokens_conversation_input: int = 16_000
    max_tokens_conversation_output: int = 2_048
    max_tokens_home_control_input: int = 8_000
    max_tokens_home_control_output: int = 1_024
    max_tokens_planning_input: int = 16_000
    max_tokens_planning_output: int = 4_096
    max_tokens_background_input: int = 4_000
    max_tokens_background_output: int = 512

    # ------------------------------------------------------------------
    # Feature flags  (env prefix: FEATURE_*)
    # ------------------------------------------------------------------
    feature_cheap_background_models: bool = True
    feature_fallback_model: bool = True
    feature_policy_gate: bool = True
    feature_action_verify: bool = True
    feature_whatsapp: bool = False
    feature_voice: bool = False
    feature_multi_home: bool = False
    feature_local_model: bool = False
    feature_bash: bool = False
    feature_python: bool = False
    feature_scrape: bool = False
    feature_search: bool = False

    @property
    def features(self) -> FeatureFlags:
        return FeatureFlags(
            cheap_background_models=self.feature_cheap_background_models,
            fallback_model=self.feature_fallback_model,
            policy_gate=self.feature_policy_gate,
            action_verify=self.feature_action_verify,
            whatsapp=self.feature_whatsapp,
            voice=self.feature_voice,
            multi_home=self.feature_multi_home,
            local_model=self.feature_local_model,
            bash=self.feature_bash,
            python=self.feature_python,
            scrape=self.feature_scrape,
        )

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""
    telegram_confirm_timeout_seconds: int = 60

    # ------------------------------------------------------------------
    # Homey MCP  (local LAN app — no auth required)
    # ------------------------------------------------------------------
    homey_mcp_url: str = ""   # e.g. http://192.168.1.x:3000/mcp
    homey_poll_interval_seconds: int = 300
    homey_verify_delay_seconds: int = 2

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "console"
    port: int = 8080
    app_secret_key: str = ""
    agent_name: str = "Home"
    household_timezone: str = "UTC"  # e.g. Europe/Oslo — used in agent time context
    prompts_dir: str = "prompts"

    # ------------------------------------------------------------------
    # Rate limiting / alerting
    # ------------------------------------------------------------------
    rate_limit_per_user_per_minute: int = 50
    alert_cooldown_minutes: int = 30

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------
    allowed_telegram_ids: list[int] = []
    admin_telegram_ids: list[int] = []

    @field_validator("allowed_telegram_ids", "admin_telegram_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: object) -> object:
        """Accept comma-separated string or a bare int from env, as well as a real list."""
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v

    # ------------------------------------------------------------------
    # Bash tool  (requires feature_bash=true)
    # ------------------------------------------------------------------
    bash_workspace_dir: str = "data/workspace"
    # Empty = use built-in DEFAULT_ALLOWED set; set in .env to override completely.
    bash_allowed_commands: list[str] = []
    bash_max_timeout_seconds: int = 60
    bash_max_output_bytes: int = 200_000

    @field_validator("bash_allowed_commands", mode="before")
    @classmethod
    def parse_str_list(cls, v: object) -> object:
        """Accept comma-separated string from env, as well as a real list."""
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    # ------------------------------------------------------------------
    # Python exec tool  (requires feature_python=true)
    # Uses the same bash_workspace_dir as the bash tool.
    # ------------------------------------------------------------------
    python_max_timeout_seconds: int = 60
    python_max_output_bytes: int = 200_000

    # ------------------------------------------------------------------
    # Web scraping tool  (requires feature_scrape=true)
    # ------------------------------------------------------------------
    scrape_timeout_seconds: int = 30
    scrape_max_content_bytes: int = 100_000

    # ------------------------------------------------------------------
    # Web search tool  (requires feature_search=true)
    # ------------------------------------------------------------------
    search_provider: str = "tavily"   # currently only "tavily" supported
    tavily_api_key: str = ""
    search_max_results: int = 5

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    db_dir: str = "data/db"
    chroma_dir: str = "data/chroma"
    event_log_retention_days: int = 90
    run_log_retention_days: int = 90

    # ------------------------------------------------------------------
    # Competing action detection
    # ------------------------------------------------------------------
    competing_action_window_seconds: int = 60

    # ------------------------------------------------------------------
    # Testing (optional — only set in dev/test)
    # ------------------------------------------------------------------
    homey_test_device_id: Optional[str] = None
    integration_test_max_llm_calls: int = 10

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def db_path(self, name: str) -> str:
        """Return the full path to a named SQLite database file."""
        return str(Path(self.db_dir) / f"{name}.db")

    def prompts_path(self) -> Path:
        return Path(self.prompts_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
