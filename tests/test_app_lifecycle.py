"""Configuration and database resources belong to one application instance."""

from pathlib import Path
from typing import Annotated
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from voice_archive_api.core.config import Settings
from voice_archive_api.core.dependencies import AppSettings, FileStorage
from voice_archive_api.db.session import get_db
from voice_archive_api.main import create_app


def settings_for(database: str, upload_root: Path) -> Settings:
    return Settings(
        environment="test",
        postgres_user="unused",
        postgres_password="unused",
        postgres_db=database,
        postgres_host="127.0.0.1",
        postgres_port=5432,
        upload_root=upload_root,
        _env_file=None,
    )


def add_resource_probe(app: FastAPI) -> None:
    @app.get("/test-resources")
    def resources(settings: AppSettings, db: Annotated[Session, Depends(get_db)], storage: FileStorage):
        # Inspect the session's bind without opening a database connection.
        engine = db.get_bind()
        assert isinstance(engine, Engine)
        return {
            "database": engine.url.database,
            "upload_root": str(storage.root),
            "cover_max_bytes": settings.cover_max_bytes,
        }


def test_apps_use_their_own_settings(tmp_path, monkeypatch):
    first_settings = settings_for("first_app", tmp_path / "first")
    second_settings = settings_for("second_app", tmp_path / "second")
    second_settings.cover_max_bytes = 12345

    # This is being set after settings are constructed, so it shouldn't affect either app.
    monkeypatch.setenv("VOICE_ARCHIVE_POSTGRES_DB", "wrong_database")

    first_app = create_app(first_settings)
    second_app = create_app(second_settings)
    add_resource_probe(first_app)
    add_resource_probe(second_app)

    with TestClient(first_app) as first_client:
        first_engine = first_app.state.engine

        # Mocking only to assert that dispose is called
        first_dispose = Mock(wraps=first_engine.dispose)
        monkeypatch.setattr(first_engine, "dispose", first_dispose)

        with TestClient(second_app) as second_client:
            second_engine = second_app.state.engine

            # Mocking only to assert that dispose is called
            second_dispose = Mock(wraps=second_engine.dispose)
            monkeypatch.setattr(second_engine, "dispose", second_dispose)

            # They all have their own instances of the objects
            assert first_engine is not second_engine
            assert first_app.state.session_factory is not second_app.state.session_factory

            # The settings have been saved successfully in the app state
            assert first_app.state.settings is first_settings
            assert second_app.state.settings is second_settings

            assert second_client.get("/test-resources").json() == {
                "database": "second_app",  # Gets the settings value, not the process env variable value
                "upload_root": str(second_settings.upload_root.resolve()),  # Gets the settings value
                "cover_max_bytes": 12345,  # Gets the value overridden after settings creation
            }

        # The dispose methods are different and are called only on the one that has been disposed
        second_dispose.assert_called_once_with()
        first_dispose.assert_not_called()

        assert first_client.get("/test-resources").json() == {
            "database": "first_app",  # Gets the settings value, not the process env variable value
            "upload_root": str(first_settings.upload_root.resolve()),  # Gets the settings value
            "cover_max_bytes": first_settings.cover_max_bytes,  # Gets the settings value
        }

    first_dispose.assert_called_once_with()


def test_engine_is_disposed_when_lifespan_body_raises(tmp_path, monkeypatch):
    app = create_app(settings_for("unused", tmp_path / "uploads"))
    with pytest.raises(RuntimeError, match="interrupted"), TestClient(app):
        engine = app.state.engine
        dispose = Mock(wraps=engine.dispose)
        monkeypatch.setattr(engine, "dispose", dispose)
        raise RuntimeError("interrupted")
    dispose.assert_called_once_with()
