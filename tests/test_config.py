from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_archive_api.core.config import PROJECT_ROOT, Settings

VOICE_ARCHIVE_ENVIRONMENT_VARIABLES = (
    "VOICE_ARCHIVE_ENVIRONMENT",
    "VOICE_ARCHIVE_DATABASE_URL",
    "VOICE_ARCHIVE_UPLOAD_ROOT",
    "VOICE_ARCHIVE_UPLOAD_MAX_BYTES",
    "VOICE_ARCHIVE_CORS_ORIGINS",
)


@pytest.fixture(autouse=True)
def clear_voice_archive_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep machine-level settings from changing configuration test results."""

    for variable_name in VOICE_ARCHIVE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)


def write_env_file(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.database_url == (
        f"sqlite:///{(PROJECT_ROOT / 'data' / 'voice_archive.sqlite3').as_posix()}"
    )
    assert settings.upload_root == PROJECT_ROOT / "uploads"
    assert settings.upload_max_bytes is None
    assert settings.cors_origins == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    )


def test_env_example_is_valid() -> None:
    settings = Settings(_env_file=PROJECT_ROOT / ".env.example")

    assert settings.environment == "development"
    assert settings.database_url == "sqlite:///./data/voice_archive.sqlite3"
    assert settings.upload_root == Path("uploads")
    assert settings.cors_origins == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://localhost:5173",
        "https://127.0.0.1:5173",
    )


def test_settings_load_from_env_file(tmp_path: Path) -> None:
    env_file = write_env_file(
        tmp_path / ".env",
        "VOICE_ARCHIVE_ENVIRONMENT=test",
        "VOICE_ARCHIVE_DATABASE_URL=sqlite:///./custom.sqlite3",
        "VOICE_ARCHIVE_UPLOAD_ROOT=./custom-uploads",
        "VOICE_ARCHIVE_UPLOAD_MAX_BYTES=4096",
        'VOICE_ARCHIVE_CORS_ORIGINS=["https://localhost:7443"]',
    )

    settings = Settings(_env_file=env_file)

    assert settings.environment == "test"
    assert settings.database_url == "sqlite:///./custom.sqlite3"
    assert settings.upload_root == Path("custom-uploads")
    assert settings.upload_max_bytes == 4096
    assert settings.cors_origins == ("https://localhost:7443",)


def test_environment_variable_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = write_env_file(
        tmp_path / ".env",
        "VOICE_ARCHIVE_ENVIRONMENT=development",
    )
    monkeypatch.setenv("VOICE_ARCHIVE_ENVIRONMENT", "production")

    settings = Settings(_env_file=env_file)

    assert settings.environment == "production"


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="environment"):
        Settings(environment="staging", _env_file=None)  # type: ignore[arg-type]


def test_invalid_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="valid SQLAlchemy database URL"):
        Settings(database_url="not a database URL", _env_file=None)


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
def test_invalid_cors_origin_is_rejected(origin: str) -> None:
    with pytest.raises(ValidationError, match=r"not an HTTP\(S\) origin"):
        Settings(cors_origins=(origin,), _env_file=None)


def test_cors_origin_trailing_slash_is_normalized() -> None:
    settings = Settings(
        cors_origins=("https://localhost:5173/",),
        _env_file=None,
    )

    assert settings.cors_origins == ("https://localhost:5173",)


def test_duplicate_cors_origins_are_rejected_after_normalization() -> None:
    with pytest.raises(ValidationError, match="duplicate origins"):
        Settings(
            cors_origins=(
                "https://localhost:5173",
                "https://localhost:5173/",
            ),
            _env_file=None,
        )


def test_empty_cors_origins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one origin"):
        Settings(cors_origins=(), _env_file=None)


@pytest.mark.parametrize("upload_max_bytes", [0, -1])
def test_non_positive_upload_limit_is_rejected(upload_max_bytes: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        Settings(upload_max_bytes=upload_max_bytes, _env_file=None)


def test_unknown_env_file_setting_is_rejected(tmp_path: Path) -> None:
    env_file = write_env_file(
        tmp_path / ".env",
        "VOICE_ARCHIVE_UNKNOWN_SETTING=unexpected",
    )

    with pytest.raises(ValidationError) as exception_info:
        Settings(_env_file=env_file)

    assert "extra_forbidden" in str(exception_info.value)
