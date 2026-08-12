from __future__ import annotations

import os
import warnings
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from stat import S_IMODE
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class EnvFileSecurityWarning(UserWarning):
    """The explicitly selected development secret file has broad permissions."""


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
    artifact_backend: Literal["local", "gcs"] = "local"
    artifact_root: Path = Path("artifacts")
    gcs_project: str | None = None
    gcs_bucket: str | None = None
    gcs_prefix: str = "padawan/artifacts"
    gcs_timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    log_level: str = "INFO"
    environment: str = "development"
    code_revision: str = "unknown"
    worker_id: str = "local-worker"
    domain_id: str = "math.algebra"
    lean_project_root: Path = Path("lean")
    lean_lake_executable: Path = Path(".tools/elan/bin/lake")
    lean_elan_home: Path = Path(".tools/elan")
    lean_sandbox_mode: Literal["required", "best_effort", "off"] = "required"
    lean_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    lean_output_limit_bytes: int = Field(default=262_144, ge=4_096, le=16_777_216)
    lean_memory_limit_mb: int = Field(default=4_096, ge=512, le=65_536)
    magellan_repository_root: Path | None = None
    magellan_handshake_path: Path | None = None
    lease_seconds: int = Field(default=300, ge=5, le=86_400)
    heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    external_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    external_retry_attempts: int = Field(default=3, ge=1, le=10)
    run_retry_budget: int = Field(default=3, ge=0, le=100)
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
    inkling_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PADAWAN_INKLING_API_KEY", "INKLING_API_KEY"),
    )
    inkling_runtime_revision: str = "aa2e7dd0f8f5fd1be0e4449f802ae5b72ffc534a"
    inkling_edge_image_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    inkling_edge_deployment_revision: str | None = Field(default=None, min_length=1)
    inkling_tensor_parallel_size: int = Field(default=4, ge=1, le=64)
    inkling_timeout_seconds: float = Field(default=3_600.0, ge=3_600.0, le=3_600.0)
    compatible_base_url: str | None = None
    compatible_model: str | None = None
    compatible_api_key: SecretStr | None = None
    raw_artifact_retention_days: int | None = Field(default=None, ge=1)
    export_private_reasoning: bool = False
    interaction_access_token: SecretStr | None = Field(default=None, min_length=16)
    interaction_host: str = "127.0.0.1"
    interaction_port: int = Field(default=8765, ge=1, le=65_535)
    interaction_system_prompt: str = (
        "You are the selected Padawan student model. Respond directly to the user's request. "
        "The complete public conversation history is supplied explicitly on every turn."
    )

    @classmethod
    def load(cls, *, env_file: Path | str | None = None) -> Self:
        """Load settings with an opt-in external dotenv file.

        `PADAWAN_ENV_FILE` is deliberately a bootstrap process variable. It is
        never discovered by scanning sibling repositories and is not part of a
        settings manifest. Real process variables retain pydantic-settings'
        normal precedence over dotenv values.
        """

        configured = env_file if env_file is not None else os.environ.get("PADAWAN_ENV_FILE")
        if configured is None or not str(configured).strip():
            return cls()
        selected = Path(configured).expanduser()
        if not selected.exists():
            raise ValueError("PADAWAN_ENV_FILE does not exist")
        if not selected.is_file():
            raise ValueError("PADAWAN_ENV_FILE must select a regular file")
        settings = cls(_env_file=selected)
        mode = S_IMODE(selected.stat().st_mode)
        if mode & 0o077:
            message = "PADAWAN_ENV_FILE is readable or writable by group/other users"
            if settings.environment.casefold() in {"production", "prod"}:
                raise ValueError(message)
            warnings.warn(message, EnvFileSecurityWarning, stacklevel=2)
        return settings

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

    @field_validator("interaction_host")
    @classmethod
    def interaction_lab_is_loopback_only(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized == "localhost":
            return normalized
        try:
            address = ip_address(normalized)
        except ValueError as exc:
            raise ValueError("Interaction Lab host must be a loopback address") from exc
        if not address.is_loopback:
            raise ValueError("Interaction Lab host must be loopback-only in this slice")
        return str(address)

    def redacted_manifest(self) -> dict[str, object]:
        values = self.model_dump(
            exclude={
                "openai_api_key",
                "anthropic_api_key",
                "inkling_api_key",
                "compatible_api_key",
                "interaction_access_token",
                "magellan_repository_root",
                "magellan_handshake_path",
            },
            mode="json",
        )
        values["database_url"] = make_url(self.database_url).render_as_string(hide_password=True)
        values["openai_api_key_configured"] = self.openai_api_key is not None
        values["anthropic_api_key_configured"] = self.anthropic_api_key is not None
        values["inkling_api_key_configured"] = self.inkling_api_key is not None
        values["compatible_api_key_configured"] = self.compatible_api_key is not None
        values["interaction_access_token_configured"] = self.interaction_access_token is not None
        values["magellan_repository_configured"] = self.magellan_repository_root is not None
        values["magellan_handshake_configured"] = self.magellan_handshake_path is not None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
