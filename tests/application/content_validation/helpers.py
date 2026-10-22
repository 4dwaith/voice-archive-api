"""Shared canonical example and prepared-file snapshots for application tests."""

import json
from importlib.resources import files

from voice_archive_api.book_files.model import FileKind, PreparedAsset

DOCUMENTS = files("voice_archive_api.application.content_validation").joinpath("docs")
EXAMPLE = DOCUMENTS.joinpath("example.v1.json")


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def prepared():
    return {
        101: PreparedAsset(101, FileKind.SOURCE_PDF, 7, None, {"page_count": 2}),
        201: PreparedAsset(201, FileKind.AUDIO_CHAPTER, None, None, {"duration_ms": 7000}),
        202: PreparedAsset(202, FileKind.AUDIO_CHAPTER, None, None, {"duration_ms": 10}),
    }


def mutate(data, location, value):
    target = data
    for component in location[:-1]:
        target = target[component]
    target[location[-1]] = value
