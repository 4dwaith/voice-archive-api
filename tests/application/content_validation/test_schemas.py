"""Canonical fields, derived JSON Schema, and passive metadata."""

import json

import pytest

from tests.application.content_validation.helpers import DOCUMENTS, example, mutate
from voice_archive_api.application.content_validation.schemas import ContentInput
from voice_archive_api.application.content_validation.validation import ContentValidationError, validate_content


@pytest.mark.parametrize(
    ("location", "value"),
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("schema_version",), "1"),
        (("source_pdf_file_id",), True),
        (("source_pdf_file_id",), 0),
        (("segments",), []),
        (("sections", 0, "kind"), "appendix"),
        (("sections", 0, "title"), " "),
        (("sections", 0, "source_key"), ""),
        (("sections", 0, "sibling_sequence"), -1),
        (("sections", 0, "sibling_sequence"), False),
        (("segments", 0, "kind"), "copyright"),
        (("segments", 0, "language"), "invalid language!"),
        (("segments", 0, "text_content"), "independent"),
        (("segments", 0, "audio_file_id"), 201),
        (("segments", 0, "page_indexes"), [0]),
        (("segments", 0, "html_content"), "<p></p>"),
        (("segments", 0, "sequence"), -1),
        (("segments", 0, "sequence"), 1.5),
        (("segments", 2, "narration_start_ms"), None),
        (("segments", 2, "narration_end_ms"), None),
        (("segments", 2, "narration_start_ms"), 4000),
        (("segments", 2, "narration_start_ms"), True),
        (("segments", 2, "narration_seek_ms"), 1001),
        (("segments", 2, "narration_seek_ms"), -1),
        (("segments", 0, "narration_seek_ms"), 0),
        (("segments", 0, "html_content"), "<p>\x00</p>"),
        (("segments", 2, "regions", 0, "x0"), 0.9),
        (("segments", 2, "regions", 0, "x1"), 1.1),
        (("segments", 2, "regions", 0, "y0"), -0.1),
        (("segments", 2, "regions", 0, "y1"), 0.8),
        (("segments", 2, "regions", 0, "x0"), "0.1"),
        (("segments", 2, "regions", 0, "x0"), True),
        (("segments", 2, "regions", 0, "page_index"), -1),
        (("segments", 2, "regions", 0, "page_index"), 0.5),
        (("segments", 2, "alignment_metadata"), {"x": "\x00"}),
        (("segments", 2, "alignment_metadata"), {"\x00": "x"}),
    ],
)
def test_schema_errors_have_paths_and_source_keys(location, value):
    data = example()
    mutate(data, location, value)
    with pytest.raises(ContentValidationError) as raised:
        validate_content(data)
    assert raised.value.issues[0].path.startswith("$.")
    if location[0] in ("sections", "segments") and len(location) > 1:
        assert raised.value.issues[0].source_key is not None


def test_generated_json_schema_is_current():
    committed = json.loads(DOCUMENTS.joinpath("schema.v1.json").read_text(encoding="utf-8"))
    assert committed == ContentInput.model_json_schema()


def test_alignment_metadata_uses_json_validation_without_custom_budgets():
    data = example()
    metadata = {"leaf": "value"}
    for _ in range(17):
        metadata = {"child": metadata}
    metadata["long_string"] = "x" * 16385
    metadata["many_values"] = [0] * 1025
    data["segments"][0]["alignment_metadata"] = metadata
    assert validate_content(data).content.segments[0].alignment_metadata == metadata


@pytest.mark.parametrize("value", [r"\u0000", r"\\u0000", {r"\u0000": ["🌕", "é", True, None, 0.8]}])
def test_alignment_metadata_preserves_literal_escapes_and_unicode(value):
    data = example()
    data["segments"][0]["alignment_metadata"] = {"diagnostic": value}
    assert validate_content(data).content.segments[0].alignment_metadata == {"diagnostic": value}


@pytest.mark.parametrize("value", ["\\" + "\x00", {"nested": ["\x00"]}, {"\x00": "value"}])
def test_alignment_metadata_rejects_nested_nul_with_escaped_backslashes(value):
    data = example()
    data["segments"][0]["alignment_metadata"] = {"diagnostic": value}
    with pytest.raises(ContentValidationError, match="NUL"):
        validate_content(data)
