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
    admin_host: str = "0.0.0.0"
    app_secret_key: str = ""
    agent_name: str = "Home"
    household_timezone: str = "UTC"  # e.g. Europe/Oslo — used in agent time context
    prompts_dir: str = "prompts"
    skills_dir: str = "app/skills"

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
        """Accept a JSON array '[1,2]', a bare int, or a real list. Comma-separated strings
        are NOT supported — pydantic-settings JSON-decodes list fields before this runs."""
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
    # Wine cellar  (requires FEATURE_WINE=true)
    # ------------------------------------------------------------------
    feature_wine: bool = False

    wine_graph_tenant_id: str = ""
    wine_graph_client_id: str = ""
    wine_graph_client_secret: str = ""
    wine_graph_drive_id: str = ""
    wine_graph_item_id: str = ""
    wine_excel_table_name: str = ""
    wine_worksheet_name: str = ""
    wine_cache_ttl_seconds: int = 21600         # 6 hours
    wine_refresh_cron: str = "0 6 * * *"        # daily at 06:00 household TZ
    wine_search_default_limit: int = 20

    # ------------------------------------------------------------------
    # Flight monitor  (requires FEATURE_FLIGHT_MONITOR=true)
    # ------------------------------------------------------------------
    feature_flight_monitor: bool = False

    flight_provider: str = "aerodatabox"

    # AeroDataBox via RapidAPI
    flight_aerodatabox_rapidapi_key: str = ""
    flight_aerodatabox_rapidapi_host: str = "aerodatabox.p.rapidapi.com"
    flight_aerodatabox_base_url: str = "https://aerodatabox.p.rapidapi.com"
    flight_aerodatabox_alerts_enabled: bool = True
    flight_aerodatabox_allow_airport_alerts: bool = False

    # Public base URL used when building per-watch webhook URLs for the provider
    flight_webhook_public_base_url: str = ""

    # Suppress polling when a webhook already refreshed within this window (minutes)
    flight_poll_recent_webhook_suppress_minutes: int = 20

    # How many days before departure to start retrying deferred alert subscriptions
    flight_subscription_retry_lead_days: int = 7

    # Consecutive provider errors before a watch is moved to FAILED
    flight_watch_fail_consecutive_errors: int = 5

    # Low alert-credit warning threshold (AeroDataBox credits)
    flight_alert_min_credits: int = 25

    # How many hours after scheduled arrival monitoring remains active before cleanup
    flight_monitor_ends_hours_after_arrival: int = 24

    # Retention periods
    flight_raw_event_retention_days: int = 60
    flight_completed_watch_retention_days: int = 180

    # ------------------------------------------------------------------
    # Email channel via AgentMail  (requires FEATURE_EMAIL_CHANNEL=true)
    # ------------------------------------------------------------------
    feature_email_channel: bool = False

    agentmail_api_key: str = ""
    agentmail_inbox_id: str = ""           # inbox email address, e.g. agent@agentmail.to
    agentmail_address: str = ""            # same address for loopback rejection
    agentmail_webhook_id: str = ""         # Svix webhook endpoint id
    agentmail_webhook_secret: str = ""     # Svix signing secret (whsec_...)
    agentmail_webhook_public_url: str = "" # public URL AgentMail posts to

    email_channel_require_mapped_sender: bool = True
    email_channel_allow_reply_to: bool = False   # send ack reply after Telegram confirm
    email_channel_save_history: bool = False
    email_channel_max_agent_chars: int = 12_000
    email_channel_max_raw_body_bytes: int = 1_048_576
    email_channel_lookback_hours: int = 24
    email_channel_force_check_limit: int = 10
    email_channel_retention_days: int = 90

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
