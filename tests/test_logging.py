import logging

import pytest
from psycopg import OperationalError as DriverOperationalError
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception

logger = logging.getLogger("voice_archive_api.logging_test")


def test_diagnostics_keep_causes_and_stack_locations_and_redact_configured_secrets(caplog, monkeypatch):
    settings = Settings(
        postgres_user="unused",
        postgres_password="db-secret",
        postgres_db="unused",
        gemini_api_key=SecretStr("api-secret"),
        gemini_batch_api_key=SecretStr("batch-secret"),
        _env_file=None,
    )
    monkeypatch.setenv("VOICE_ARCHIVE_GEMINI_API_KEY", "environment-secret")

    try:
        try:
            raise ValueError("Provider rejected api-secret batch-secret environment-secret db-secret")
        except ValueError as cause:
            raise RuntimeError("Operation failed") from cause
    except RuntimeError as exc:
        log_exception(logger, "Failed: book=46", exc, settings=settings)

    assert len(caplog.records) == 1
    assert "RuntimeError" in caplog.text
    assert "ValueError" in caplog.text
    assert "Caused by:" in caplog.text
    assert "in test_diagnostics_keep_causes" in caplog.text
    assert caplog.text.count("[redacted]") == 4
    assert all(
        secret not in caplog.text for secret in ("api-secret", "batch-secret", "environment-secret", "db-secret")
    )


def test_database_diagnostics_keep_driver_reason_without_sql_or_parameters(caplog):
    original = DriverOperationalError("connection refused")

    try:
        raise OperationalError("INSERT INTO private_book_content", {"text": "private-passage"}, original) from original
    except OperationalError as exc:
        log_exception(logger, "Database publication failed", exc)

    assert "OperationalError" in caplog.text
    assert "connection refused" in caplog.text
    assert "private_book_content" not in caplog.text
    assert "private-passage" not in caplog.text


def test_configuration_diagnostics_identify_fields_without_dumping_input_values(caplog):
    with pytest.raises(ValidationError) as failure:
        Settings(
            postgres_user="unused",
            postgres_password="private-password",
            postgres_db="unused",
            postgres_port="private-invalid-input",
            _env_file=None,
        )

    log_exception(logger, "Invalid configuration", failure.value)

    assert "postgres_port: int_parsing" in caplog.text
    assert "private-password" not in caplog.text
    assert "private-invalid-input" not in caplog.text
