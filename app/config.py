from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator
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
    world_model_tools: bool = True
    world_model_proposals: bool = False
    multi_step_tasks: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM providers  (global fallback keys)
    # ------------------------------------------------------------------
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Per-slot API keys — optional, takes precedence over global keys above.
    # Set these to explicitly bind a model slot to a specific key/provider.
    model_primary_api_key: str = ""
    model_background_api_key: str = ""
    model_fallback_api_key: str = ""
    model_background_fallback_api_key: str = ""
    model_embedding_api_key: str = ""

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------
    model_primary: str = "claude-sonnet-4-6"
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
    feature_world_model_tools: bool = True
    feature_world_model_proposals: bool = False
    feature_multi_step_tasks: bool = False

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
            world_model_tools=self.feature_world_model_tools,
            world_model_proposals=self.feature_world_model_proposals,
            multi_step_tasks=self.feature_multi_step_tasks,
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
    homey_tool_timeout_secs: int = 15
    # Inbound event webhook — shared secret for POST /webhook/homey/event
    homey_webhook_secret: str = ""
    # Feature flag — set to false to disable the event dispatcher entirely
    event_dispatcher_enabled: bool = True

    # ------------------------------------------------------------------
    # Prometheus MCP  (services/prometheus-mcp/ — read-only metrics)
    # ------------------------------------------------------------------
    prometheus_mcp_url: str = ""  # e.g. http://192.168.1.x:9000/mcp

    # ------------------------------------------------------------------
    # Tools MCP  (services/tools-mcp/ — sandboxed execution tools)
    # ------------------------------------------------------------------
    tools_mcp_url: str = ""  # e.g. http://tools:9001/mcp in Docker

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "console"
    port: int = 8080
    admin_port: int = 9090
    app_secret_key: str = ""
    agent_name: str = "Home"
    household_timezone: str = "UTC"  # e.g. Europe/Oslo — used in agent time context
    prompts_dir: str = "prompts"

    # ------------------------------------------------------------------
    # Per-run token enforcement
    # ------------------------------------------------------------------
    max_tokens_per_run: int = 4096
    token_cost_warn_threshold: int = 3000  # input tokens; emit warning event if exceeded

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

    @model_validator(mode="after")
    def _require_webhook_secret(self) -> "Settings":
        if self.telegram_bot_token and not self.telegram_webhook_secret:
            raise ValueError(
                "TELEGRAM_WEBHOOK_SECRET must be set when TELEGRAM_BOT_TOKEN is configured. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return self

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
    # Storage
    # ------------------------------------------------------------------
    db_dir: str = "data/db"
    chroma_dir: str = "data/chroma"
    event_log_retention_days: int = 90
    run_log_retention_days: int = 90
    # Episodic memory lifecycle — idle TTL per importance tier (0 = never purge)
    memory_ttl_ephemeral_days: int = 30
    memory_ttl_normal_days: int = 90
    memory_ttl_important_days: int = 365
    # Near-duplicate suppression — L2 distance threshold in sqlite-vec space
    memory_dedup_distance_threshold: float = 0.15

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
