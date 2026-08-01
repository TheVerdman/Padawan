from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Deterministic process configuration loaded only at composition boundaries."""

    model_config = SettingsConfigDict(
        env_prefix="PADAWAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "sqlite+aiosqlite:///./padawan.sqlite3"
    artifact_root: Path = Path("artifacts")
    log_level: str = "INFO"
    environment: str = "development"
    code_revision: str = "unknown"
    worker_id: str = "local-worker"
    lease_seconds: int = Field(default=300, ge=5, le=86_400)
    heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    external_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    external_retry_attempts: int = Field(default=3, ge=1, le=10)
    openai_base_url: str = "https://api.openai.com"
    openai_model: str | None = None
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PADAWAN_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str | None = None
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PADAWAN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    inkling_base_url: str = "http://127.0.0.1:8000"
    inkling_model: str | None = None
    inkling_runtime_revision: str = "aa0a70e40ddab8f5fb00f111814ae9a3073e952a"
    inkling_tensor_parallel_size: int = Field(default=4, ge=1, le=64)
    compatible_base_url: str | None = None
    compatible_model: str | None = None
    compatible_api_key: SecretStr | None = None
    raw_artifact_retention_days: int | None = Field(default=None, ge=1)
    export_private_reasoning: bool = False

    @field_validator("database_url")
    @classmethod
    def require_async_database_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        if value.startswith("sqlite:///"):
            return value.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if not value.startswith(("postgresql+psycopg://", "sqlite+aiosqlite:///")):
            raise ValueError("database_url must use postgresql+psycopg or sqlite+aiosqlite")
        return value

    def redacted_manifest(self) -> dict[str, object]:
        values = self.model_dump(
            exclude={"openai_api_key", "anthropic_api_key", "compatible_api_key"},
            mode="json",
        )
        values["database_url"] = make_url(self.database_url).render_as_string(hide_password=True)
        values["openai_api_key_configured"] = self.openai_api_key is not None
        values["anthropic_api_key_configured"] = self.anthropic_api_key is not None
        values["compatible_api_key_configured"] = self.compatible_api_key is not None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
