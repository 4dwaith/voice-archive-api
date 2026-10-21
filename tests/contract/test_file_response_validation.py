from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from voice_archive_api.book_files.schemas import FileResponse


def file_record(kind, metadata):
    return SimpleNamespace(id=1, kind=kind, byte_size=100, file_metadata=metadata, created_at=datetime.now(UTC))


@pytest.mark.parametrize(
    "kind,metadata",
    [("cover", {"width": 16, "height": 24}), ("source_pdf", {"page_count": 3})],
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
    ],
)
def test_file_response_rejects_missing_or_other_kind_metadata(kind, metadata):
    with pytest.raises(ValidationError):
        TypeAdapter(FileResponse).validate_python(file_record(kind, metadata))


@pytest.mark.parametrize("invalid", [None, 0, -1, 1.5, 1.0, "1", True, [], {}, 2_147_483_648])
@pytest.mark.parametrize("kind,field", [("cover", "width"), ("cover", "height"), ("source_pdf", "page_count")])
def test_file_response_rejects_non_positive_or_non_integer_metadata(kind, field, invalid):
    metadata = {"width": 16, "height": 24} if kind == "cover" else {"page_count": 3}
    with pytest.raises(ValidationError):
        TypeAdapter(FileResponse).validate_python(file_record(kind, metadata | {field: invalid}))
