from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "voice_archive.sqlite3"


class Settings(BaseSettings):
    """Runtime configuration loaded from VOICE_ARCHIVE_* variables."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="VOICE_ARCHIVE_",
        extra="forbid",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    upload_root: Path = PROJECT_ROOT / "uploads"
    upload_max_bytes: PositiveInt | None = None
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            make_url(value)
        except ArgumentError as exc:
            raise ValueError("must be a valid SQLAlchemy database URL") from exc
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
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


@lru_cache
def get_settings() -> Settings:
    return Settings()

