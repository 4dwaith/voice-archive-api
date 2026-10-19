import os
import re
import stat
from pathlib import Path
from time import time
from typing import BinaryIO
from uuid import uuid4

from voice_archive_api.storage.base import BinarySource, FileTooLarge, InvalidStorageKey

CHUNK_SIZE = 1024 * 1024  # 1 MB
STALE_AFTER_SECONDS = 24 * 60 * 60
KEY_PATTERN = re.compile(r"(temporary|assets)/[0-9a-f]{32}")


class LocalStorage:
    """Storage in a service-owned directory on a single local filesystem.

    Temporary and promoted files share the same filesystem. Promotion creates
    an atomic, non-overwriting hard link, then removes the temporary name in a
    separate operation. The two operations are not one atomic transaction.
    This requires hard-link support (including NTFS and normal Linux volumes).
    The root must not be writable by untrusted local processes: path checks
    are not a defense against concurrent filesystem tampering by other users.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or KEY_PATTERN.fullmatch(key) is None:
            raise InvalidStorageKey("Invalid storage key")
        path = self.root / key
        # Reject redirected namespaces as well as individual symlinked files.
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or not path.resolve().is_relative_to(self.root)
        ):
            raise InvalidStorageKey("Storage key points outside its namespace")
        return path

    def put_temporary(self, source: BinarySource, *, max_bytes: int) -> str:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        key = f"temporary/{uuid4().hex}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Open before the try: if this path already exists, raise without
        # running cleanup, which would delete a file we didn't create.
        output = path.open("xb")

        try:
            with output:
                size = 0
                while chunk := source.read(min(CHUNK_SIZE, max_bytes - size + 1)):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge
                    output.write(chunk)

                    # Refresh activity explicitly, so that the stale cleanup
                    # count down resets every time a chunk is written.
                    os.utime(path, None)
            return key
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def promote(self, temporary_key: str) -> str:
        # Marks the file as out of temporary storage, and in long-term storage.

        source = self._path(temporary_key)
        if not temporary_key.startswith("temporary/"):
            raise InvalidStorageKey("Only temporary files can be promoted")

        key = f"assets/{uuid4().hex}"
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # No copy, no partially visible destination, and no replacement of an
        # existing asset even in the event of a key collision.
        os.link(source, destination)
        try:
            source.unlink()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return key

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def clear_stale(self) -> int:
        """Reclaim inactive temporary files, using modification time as activity."""
        temp_folder = self.root / "temporary"
        if temp_folder.is_symlink() or not temp_folder.resolve().is_relative_to(self.root):
            raise InvalidStorageKey("Temporary folder points outside storage")

        cutoff = time() - STALE_AFTER_SECONDS
        removed = 0

        try:
            entries = list(temp_folder.iterdir())
        except FileNotFoundError:
            return 0

        for entry in entries:

            if KEY_PATTERN.fullmatch(f"temporary/{entry.name}") is None:
                continue

            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                    # It's a regular file that's older than the cutoff.
                    entry.unlink()
                    removed += 1
            except FileNotFoundError:
                # Another cleanup or promotion may already have removed it.
                continue

        return removed
