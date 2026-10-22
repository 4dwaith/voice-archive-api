from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from voice_archive_api.services.files.schemas import FileResponse


def file_record(kind, metadata):
    return SimpleNamespace(id=1, kind=kind, byte_size=100, file_metadata=metadata, created_at=datetime.now(UTC))


@pytest.mark.parametrize(
    "kind,metadata",
    [
        ("cover", {"width": 16, "height": 24}),
        ("source_pdf", {"page_count": 3}),
        ("audio_chapter", {"duration_ms": 1000, "sample_rate": 44100, "channels": 2}),
    ],
)
def test_file_response_selects_metadata_shape_from_kind(kind, metadata):
    response = TypeAdapter(FileResponse).validate_python(file_record(kind, metadata | {"custom": "value"}))
    assert response.model_dump(mode="json")["metadata"] == metadata | {"custom": "value"}


@pytest.mark.parametrize(
    "kind,metadata",
    [
        ("cover", {}),
        ("cover", {"page_count": 3}),
        ("cover", {"width": 16}),
        ("source_pdf", {}),
        ("source_pdf", {"width": 16, "height": 24}),
        ("audio_chapter", {}),
        ("audio_chapter", {"width": 16, "height": 24}),
        ("audio_chapter", {"duration_ms": 1000, "sample_rate": 44100}),
        ("audio_chapter", {"duration_ms": 1000, "channels": 2}),
        ("audio_chapter", {"sample_rate": 44100, "channels": 2}),
    ],
)
def test_file_response_rejects_missing_or_other_kind_metadata(kind, metadata):
    with pytest.raises(ValidationError):
        TypeAdapter(FileResponse).validate_python(file_record(kind, metadata))


def valid_metadata(kind):
    return {
        "cover": {"width": 16, "height": 24},
        "source_pdf": {"page_count": 3},
        "audio_chapter": {"duration_ms": 1000, "sample_rate": 44100, "channels": 2},
    }[kind]


@pytest.mark.parametrize("invalid", [None, 0, -1, 1.5, 1.0, "1", True, [], {}])
@pytest.mark.parametrize(
    "kind,field",
    [
        ("cover", "width"),
        ("cover", "height"),
        ("source_pdf", "page_count"),
        ("audio_chapter", "duration_ms"),
        ("audio_chapter", "sample_rate"),
        ("audio_chapter", "channels"),
    ],
)
def test_file_response_rejects_non_positive_or_non_integer_metadata(kind, field, invalid):
    with pytest.raises(ValidationError):
        TypeAdapter(FileResponse).validate_python(file_record(kind, valid_metadata(kind) | {field: invalid}))


@pytest.mark.parametrize(
    "kind,field,maximum",
    [
        ("cover", "width", 2_147_483_647),
        ("cover", "height", 2_147_483_647),
        ("source_pdf", "page_count", 2_147_483_647),
        ("audio_chapter", "duration_ms", 9_223_372_036_854_775_807),
        ("audio_chapter", "sample_rate", 2_147_483_647),
        ("audio_chapter", "channels", 2_147_483_647),
    ],
)
def test_file_response_metadata_enforces_field_specific_upper_bounds(kind, field, maximum):
    adapter = TypeAdapter(FileResponse)
    response = adapter.validate_python(file_record(kind, valid_metadata(kind) | {field: maximum}))
    assert response.model_dump(mode="json")["metadata"][field] == maximum
    with pytest.raises(ValidationError):
        adapter.validate_python(file_record(kind, valid_metadata(kind) | {field: maximum + 1}))
