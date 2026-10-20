from typing import Any

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import State

from voice_archive_api.core.config import Settings


class ApplicationState(State):
    """Settings exist at construction; database resources are assigned at startup."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings


class Application(FastAPI):
    state: ApplicationState

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = ApplicationState(settings)
