from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, PositiveInt, SecretStr, field_validator, model_validator
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
    login_enabled: bool = True
    auth_secret: SecretStr | None = Field(default=None, min_length=32)
    auth_cookie_secure: bool = True
    auth_token_ttl_seconds: int = Field(default=43_200, ge=60, le=86_400)
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
    gemini_api_key: SecretStr | None = None
    gemini_batch_api_key: SecretStr | None = None
    gemini_live_model: str = Field(default="gemini-3.8-live", min_length=1)
    gemini_text_model: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    gemini_embedding_model: str = Field(default="gemini-embedding-2", min_length=1)
    gemini_embedding_dimensions: PositiveInt = 768
    embedding_input_format: str = Field(default="retrieval-document-v1", min_length=1)
    embedding_submission_grace_seconds: PositiveInt = 900
    embedding_collection_interval_seconds: PositiveInt = 180
    embedding_batch_tokens: int = Field(default=150_000, ge=32)
    embedding_max_attempts: PositiveInt = 3
    gemini_answer_input_tokens: PositiveInt = 1_000_000
    gemini_answer_timeout_seconds: PositiveInt = 90
    reranker_base_url: AnyHttpUrl | None = None
    reranker_model: str = Field(default="Qwen/Qwen3-Reranker-0.6B", min_length=1)
    reranker_timeout_seconds: PositiveInt = 60
    reranker_max_input_tokens: int = Field(default=4096, ge=512)

    @property
    def effective_gemini_batch_api_key(self) -> SecretStr | None:
        return self.gemini_batch_api_key or self.gemini_api_key

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
