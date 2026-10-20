"""Local delivery adapts validated storage keys to Starlette's file responses."""

import stat

from fastapi.responses import FileResponse

from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.file_handling.file_delivery import FileDelivery


class LocalFileDelivery(FileDelivery):
    def __init__(self, storage: LocalStorage):
        self.storage = storage

    def response(self, key: str, *, media_type: str, filename: str) -> FileResponse:
        path = self.storage.local_path(key)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError("Stored file is not a regular file")
        # Resolve missing files before response execution, while route handlers
        # can still translate the failure to the corresponding file-not-found error.
        return FileResponse(
            path,
            stat_result=info,
            media_type=media_type,
            filename=filename,
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )
