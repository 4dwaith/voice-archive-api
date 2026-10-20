from typing import Annotated, cast

from fastapi import Depends, Request

from voice_archive_api.core.application import ApplicationState
from voice_archive_api.core.config import Settings
from voice_archive_api.file_handling import LocalStorage, Storage
from voice_archive_api.file_handling.file_delivery import FileDelivery
from voice_archive_api.file_handling.local_file_delivery import LocalFileDelivery


def get_app_state(request: Request) -> ApplicationState:
    """Narrow the framework's generic state to the state installed by our factory."""
    return cast(ApplicationState, request.app.state)


def get_app_settings(request: Request) -> Settings:
    """Return the configuration selected when this application was created."""
    return get_app_state(request).settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_file_storage(settings: AppSettings) -> Storage:
    return LocalStorage(settings.upload_root)


FileStorage = Annotated[Storage, Depends(get_file_storage)]


def get_file_delivery(settings: AppSettings) -> FileDelivery:
    return LocalFileDelivery(LocalStorage(settings.upload_root))


Delivery = Annotated[FileDelivery, Depends(get_file_delivery)]
