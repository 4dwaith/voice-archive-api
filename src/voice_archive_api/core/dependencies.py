from typing import Annotated, cast

from fastapi import Depends, Request

from voice_archive_api.core.application import ApplicationState
from voice_archive_api.core.config import Settings


def get_app_state(request: Request) -> ApplicationState:
    """Narrow the framework's generic state to the state installed by our factory."""
    return cast(ApplicationState, request.app.state)


def get_app_settings(request: Request) -> Settings:
    """Return the configuration selected when this application was created."""
    return get_app_state(request).settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
