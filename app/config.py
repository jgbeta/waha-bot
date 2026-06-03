from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful personal WhatsApp assistant. Reply naturally and concisely. "
    "Use the same language as the user unless they ask otherwise."
)
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "CHANGE_ME", "PLACEHOLDER")


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def is_blank_or_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    upper = stripped.upper()
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # WAHA
    waha_base_url: str = "http://waha:3000"
    waha_api_key: str = ""
    waha_session: str = "default"
    waha_timeout_seconds: float = 15.0
    waha_send_retries: int = 3
    waha_webhook_hmac_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WAHA_WEBHOOK_HMAC_KEY", "WHATSAPP_HOOK_HMAC_KEY"),
    )

    # OpenAI-compatible API
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini-2025-04-14"
    openai_max_tokens: int = 2048
    openai_timeout_seconds: float = 30.0
    openai_retries: int = 2

    # Bot access control
    bot_allowed_chat_id: str | None = None      # legacy single value
    bot_allowed_chat_ids: str = ""             # comma-separated list
    bot_allowed_phone: str | None = None        # legacy single value
    bot_allowed_phones: str = ""               # comma-separated list
    bot_require_allowlist: bool = True
    bot_allow_groups: bool = False

    # Bot behavior
    bot_clear_command: str = "clear history"
    bot_history_max_messages: int = 20
    bot_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    bot_system_prompt_file: str | None = None
    bot_queue_maxsize: int = 100
    bot_dedupe_ttl_seconds: int = 24 * 60 * 60
    bot_lid_cache_ttl_seconds: int = 24 * 60 * 60
    bot_send_seen: bool = True
    bot_send_typing: bool = True
    bot_error_reply: str = "Sorry, I had trouble generating a reply. Please try again."
    bot_clear_reply: str = "History cleared."
    bot_autoreply_enabled: bool = True
    bot_dry_run: bool = False

    # Storage
    bot_store: Literal["memory", "sqlite", "postgres"] = "sqlite"
    bot_sqlite_path: str = "/data/bot.sqlite3"
    bot_history_store: Literal["sqlite", "memory", "postgres", "none"] = "sqlite"
    bot_retain_processed_message_body: bool = False

    # PostgreSQL (advanced/optional)
    pg_host: str = ""
    pg_port: int = 5432
    pg_user: str = ""
    pg_password: str = ""
    pg_dbname: str = ""
    pg_table: str = "lang_chat"
    pg_contact_name: str = "user"

    # Logs
    log_level: str = "INFO"

    @field_validator(
        "waha_webhook_hmac_key",
        "bot_allowed_chat_id",
        "bot_allowed_phone",
        "bot_system_prompt_file",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        if value == "":
            return None
        return value

    @field_validator("bot_history_max_messages")
    @classmethod
    def history_limit_positive(cls, value: int) -> int:
        return max(2, value)

    @field_validator("bot_lid_cache_ttl_seconds")
    @classmethod
    def lid_cache_ttl_positive(cls, value: int) -> int:
        return max(1, value)

    @model_validator(mode="after")
    def compatible_history_store(self) -> "Settings":
        if self.bot_history_store == "sqlite" and self.bot_store != "sqlite":
            raise ValueError("BOT_HISTORY_STORE=sqlite requires BOT_STORE=sqlite. For BOT_STORE=postgres, set BOT_HISTORY_STORE=postgres or BOT_HISTORY_STORE=none.")
        if self.bot_history_store == "postgres" and self.bot_store != "postgres":
            raise ValueError("BOT_HISTORY_STORE=postgres requires BOT_STORE=postgres.")
        return self


def has_configured_allowlist(settings: Settings) -> bool:
    return any(
        [
            bool(settings.bot_allowed_chat_id),
            bool(split_csv(settings.bot_allowed_chat_ids)),
            bool(settings.bot_allowed_phone),
            bool(split_csv(settings.bot_allowed_phones)),
        ]
    )


def validate_runtime_settings(settings: Settings) -> None:
    errors: list[str] = []
    if is_blank_or_placeholder(settings.waha_api_key):
        errors.append("WAHA_API_KEY must be set to a real value.")
    if settings.bot_autoreply_enabled and is_blank_or_placeholder(settings.openai_api_key):
        errors.append("OPENAI_API_KEY must be set to a real value when BOT_AUTOREPLY_ENABLED=true.")
    if settings.bot_require_allowlist and not has_configured_allowlist(settings):
        errors.append(
            "BOT_REQUIRE_ALLOWLIST=true requires BOT_ALLOWED_PHONES, BOT_ALLOWED_PHONE, "
            "BOT_ALLOWED_CHAT_IDS, or BOT_ALLOWED_CHAT_ID."
        )
    if settings.bot_store == "postgres" or settings.bot_history_store == "postgres":
        missing = [
            name
            for name in ("pg_host", "pg_user", "pg_password", "pg_dbname")
            if is_blank_or_placeholder(getattr(settings, name))
        ]
        if missing:
            errors.append(f"PostgreSQL storage requires {', '.join(name.upper() for name in missing)}.")
    if errors:
        raise ValueError(" ".join(errors))


def load_system_prompt(settings: Settings) -> str:
    if settings.bot_system_prompt_file:
        path = Path(settings.bot_system_prompt_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return settings.bot_system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
