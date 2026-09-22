"""Загрузка конфигурации: .env + YAML."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Настройки приложения, читаются из .env и переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # InfluxDB
    influx_url: str = Field(default="http://localhost:8086", alias="INFLUX_URL")
    influx_token: str = Field(default="", alias="INFLUX_TOKEN")
    influx_org: str = Field(default="heliogeo", alias="INFLUX_ORG")
    influx_bucket: str = Field(default="solar", alias="INFLUX_BUCKET")

    # DAV
    dav_base_url: str = Field(default="", alias="DAV_BASE_URL")
    dav_user: str = Field(default="", alias="DAV_USER")
    dav_password: str = Field(default="", alias="DAV_PASSWORD")

    # LLM
    llm_provider: str = Field(default="alice", alias="LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="yandexgpt", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2000, alias="LLM_MAX_TOKENS")

    # Telegram
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # Database
    database_url: str = Field(default="", alias="DATABASE_URL")

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    tz: str = Field(default="UTC", alias="TZ")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Получить настройки (кэшируется)."""
    return Settings()
