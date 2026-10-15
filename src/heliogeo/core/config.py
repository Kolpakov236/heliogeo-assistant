"""Загрузка конфигурации: .env + YAML."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
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

    # Database: полный DSN приоритетнее POSTGRES_* (см. _build_database_url)
    database_url: str = Field(default="", alias="DATABASE_URL")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5433, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="heliogeo", alias="POSTGRES_USER")
    postgres_password: str = Field(default="heliogeo", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="heliogeo", alias="POSTGRES_DB")

    # SWX API
    swx_base_url: str = Field(
        default="https://503.ifz.ru/swx",
        alias="SWX_BASE_URL",
    )
    # Хранилище объектов: local — каталог (разработка, тесты), s3 — Yandex Object Storage.
    # Ключи доступа к S3 сюда не входят: boto3 берёт их из AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
    object_store: Literal["local", "s3"] = Field(default="local", alias="OBJECT_STORE")
    object_store_dir: Path = Field(
        default=_PROJECT_ROOT / "var" / "objects", alias="OBJECT_STORE_DIR"
    )
    bucket_prefix: str = Field(default="heliogeo", alias="BUCKET_PREFIX")
    s3_endpoint_url: str = Field(default="https://storage.yandexcloud.net", alias="S3_ENDPOINT_URL")
    s3_region: str = Field(default="ru-central1", alias="S3_REGION")
    # Контракты: доменные YAML под версионированием (по умолчанию contracts/ в корне репозитория)
    contracts_dir: Path = Field(default=_PROJECT_ROOT / "contracts", alias="CONTRACTS_DIR")

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    tz: str = Field(default="UTC", alias="TZ")

    @field_validator("contracts_dir", "object_store_dir")
    @classmethod
    def _resolve_relative_dirs(cls, value: Path) -> Path:
        """Относительный путь считается от корня репозитория, а не от текущего каталога."""
        return value if value.is_absolute() else _PROJECT_ROOT / value

    @model_validator(mode="after")
    def _build_database_url(self) -> Self:
        """Если DATABASE_URL не задан, собрать DSN (psycopg 3) из POSTGRES_*."""
        if not self.database_url:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            db = quote(self.postgres_db, safe="")
            self.database_url = (
                f"postgresql+psycopg://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{db}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Получить настройки (кэшируется)."""
    return Settings()
