from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration loaded from VOICE_ARCHIVE_* variables."""

    model_config = SettingsConfigDict(
        env_prefix="VOICE_ARCHIVE_",
        env_parse_none_str="null",
        extra="ignore",
    )

    # Field definitions and fallbacks (the actual values are supplied by the process environment
    # (Compose or the development runner) when the settings instance is created).
    environment: Literal["development", "test", "production"] = "development"
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    postgres_db: str = Field(min_length=1)
    postgres_host: str = Field(default="127.0.0.1", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    upload_root: Path = PROJECT_ROOT / "uploads"
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    )

    upload_max_bytes: PositiveInt | None = None
    cover_max_bytes: PositiveInt = 25 * 1024 * 1024
    pdf_max_bytes: PositiveInt = 1_500_000_000
    audio_max_bytes: PositiveInt = 1_500_000_000

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                _ = parsed.port  # Access validates the port text and numeric range.
            except ValueError as exc:
                raise ValueError(f"{origin!r} is not an HTTP(S) origin") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"{origin!r} is not an HTTP(S) origin")
            normalized.append(origin.rstrip("/"))
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate origins")
        return tuple(normalized)

    @model_validator(mode="after")
    def require_cors_origins(self) -> Self:
        if not self.cors_origins:
            raise ValueError("cors_origins must contain at least one origin")
        return self
