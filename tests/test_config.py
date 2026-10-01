import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from voice_archive_api.core.config import PROJECT_ROOT, Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("VOICE_ARCHIVE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_USER", "test")
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_DB", "test")


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.environment == "development"
    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 5432
    assert settings.upload_root == PROJECT_ROOT / "uploads"
    assert settings.upload_max_bytes is None


def test_process_environment_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_HOST", "db")
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_PORT", "5433")
    monkeypatch.setenv("VOICE_ARCHIVE_CORS_ORIGINS", '["https://example.com"]')
    monkeypatch.setenv("VOICE_ARCHIVE_UPLOAD_MAX_BYTES", "4096")
    settings = Settings()
    assert settings.database_url.host == "db"
    assert settings.database_url.port == 5433
    assert settings.cors_origins == ("https://example.com",)
    assert settings.upload_max_bytes == 4096


def test_dotenv_is_not_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Another service, such as Docker Compose, Kubernetes, or AWS ECS, or even the local IDE is responsible for loading environment variables.
    (tmp_path / ".env").write_text("VOICE_ARCHIVE_ENVIRONMENT=production\n")
    monkeypatch.chdir(tmp_path)
    assert Settings.model_config.get("env_file") is None
    assert Settings().environment == "development"


def test_password_special_characters_are_preserved() -> None:
    password = "p@ss:/?#%$word"
    settings = Settings(postgres_password=password)
    url = settings.database_url
    assert make_url(url.render_as_string(hide_password=False)).password == password
    assert password not in repr(settings)
    assert password not in str(url)


@pytest.mark.parametrize("field", ["postgres_user", "postgres_password", "postgres_db"])
def test_database_credentials_are_required(field, monkeypatch) -> None:
    monkeypatch.delenv("VOICE_ARCHIVE_" + field.upper())
    with pytest.raises(ValidationError, match=field):
        Settings()


@pytest.mark.parametrize(
    "field", ["postgres_user", "postgres_password", "postgres_db", "postgres_host"]
)
def test_empty_connection_fields_are_rejected(field) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: ""})


@pytest.mark.parametrize("port", [0, 65536, "invalid"])
def test_invalid_port_is_rejected(port) -> None:
    with pytest.raises(ValidationError, match="postgres_port"):
        Settings(postgres_port=port)


def test_unrelated_process_variables_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("UNRELATED", "value")
    assert Settings().postgres_user == "test"


def test_optional_upload_limit_can_be_unset(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_ARCHIVE_UPLOAD_MAX_BYTES", "null")
    assert Settings().upload_max_bytes is None


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="environment"):
        Settings(environment="staging")


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://localhost:5173",
        "http://",
        "http://user:secret@localhost:5173",
        "http://localhost:5173/api",
        "http://localhost:5173?debug=true",
        "http://localhost:5173#fragment",
        "http://localhost:not-a-port",
    ],
)
def test_invalid_cors_origin_is_rejected(origin) -> None:
    with pytest.raises(ValidationError, match=r"not an HTTP\(S\) origin"):
        Settings(cors_origins=(origin,))


def test_cors_origin_trailing_slash_is_normalized() -> None:
    settings = Settings(cors_origins=("https://localhost:5173/",))
    assert settings.cors_origins == ("https://localhost:5173",)


def test_duplicate_cors_origins_are_rejected_after_normalization() -> None:
    with pytest.raises(ValidationError, match="duplicate origins"):
        Settings(cors_origins=("https://localhost:5173", "https://localhost:5173/"))


def test_empty_cors_origins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one origin"):
        Settings(cors_origins=())


@pytest.mark.parametrize("upload_max_bytes", [0, -1])
def test_non_positive_upload_limit_is_rejected(upload_max_bytes) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        Settings(upload_max_bytes=upload_max_bytes)
