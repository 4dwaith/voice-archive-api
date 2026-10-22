"""Preparation results and strategy contracts for file services."""

from dataclasses import dataclass
from typing import Protocol

from voice_archive_api.file_handling import BinarySource, Storage


@dataclass(frozen=True)
class PreparedFile:
    storage_key: str
    byte_size: int
    metadata: dict[str, int | str]


class Preparer(Protocol):
    def __call__(self, source: BinarySource, storage: Storage, *, max_bytes: int) -> PreparedFile: ...


@dataclass(frozen=True)
class FileStrategy:
    prepare: Preparer | None
    limit_setting_key: str | None
    media_type: str
    filename: str
