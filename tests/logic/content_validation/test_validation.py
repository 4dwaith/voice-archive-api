"""Canonical envelope, hierarchy, document ordering, and complete example validation."""

import json

import pytest

from tests.logic.content_validation.helpers import EXAMPLE, example, mutate, prepared
from voice_archive_api.logic.content_validation.assets import validate_assets
from voice_archive_api.logic.content_validation.kinds import SectionKind, SegmentKind
from voice_archive_api.logic.content_validation.schemas import ContentInput
from voice_archive_api.logic.content_validation.validation import ContentValidationError, validate_content


def test_complete_example_and_backward_root_audio():
    validated = validate_content(EXAMPLE.read_bytes())
    validate_assets(validated, prepared(), book_id=7)
    content = validated.content
    assert content.sections[0].kind is SectionKind.CHAPTER
    assert content.segments[3].kind is SegmentKind.VERSE
    assert content.model_dump(mode="json")["sections"][0]["kind"] == "chapter"
    assert content.segments[2].text_content == "A bold and italic phrase & literal <markup>."
    assert content.segments[3].text_content == "One line\nAnother\xa0line\n🌕 é"
    assert validated.playback_audio_id("nested") == 201
    assert validated.playback_audio_id("empty") == 201
    assert validated.playback_audio_id("chapter-2") is None
    assert validated.playback_audio_id(None) is None
    assert "text_content" not in content.segments[2].model_dump()
    assert "page_indexes" not in ContentInput.model_json_schema()["$defs"]["SegmentInput"]["properties"]


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b'{"schema_version":1,"schema_version":1}',
        b"[]",
        b"\xff",
        b'{"v":NaN}',
        {"v": float("inf")},
        {"v": object()},
        {"v": "\ud800"},
    ],
)
def test_invalid_envelopes(payload):
    with pytest.raises(ContentValidationError):
        validate_content(payload)


def test_byte_and_diagnostic_limits(monkeypatch):
    import voice_archive_api.logic.content_validation.validation as module

    monkeypatch.setattr(module, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(ContentValidationError) as raised:
        validate_content(EXAMPLE.read_bytes())
    assert raised.value.issues[0].code == "payload_limit"
    monkeypatch.setattr(module, "MAX_PAYLOAD_BYTES", 32_000_000)
    data = example()
    data["segments"] = [dict(source_key=str(i), sequence=i, kind="prose", html_content="<p/>") for i in range(200)]
    with pytest.raises(ContentValidationError) as raised:
        validate_content(data)
    assert len(raised.value.issues) == 100


@pytest.mark.parametrize(
    ("location", "value", "code"),
    [
        (("sections", 1, "source_key"), "chapter-1", "duplicate_source_key"),
        (("sections", 1, "parent_source_key"), "missing", "missing_parent"),
        (("sections", 3, "sibling_sequence"), 0, "duplicate_sibling_sequence"),
        (("segments", 1, "source_key"), "front", "duplicate_source_key"),
        (("segments", 1, "section_source_key"), "missing", "missing_section"),
        (("segments", 1, "sequence"), 0, "duplicate_sequence"),
        (("sections", 0, "parent_source_key"), "nested", "section_cycle"),
        (("sections", 2, "parent_source_key"), "empty", "section_cycle"),
        (("segments", 2, "sequence"), 50, "noncontiguous_subtree"),
    ],
)
def test_graph_validation(location, value, code):
    data = example()
    mutate(data, location, value)
    if code == "section_cycle":
        data["sections"][0]["sibling_sequence"] = 99
    with pytest.raises(ContentValidationError) as raised:
        validate_content(data)
    assert code in {i.code for i in raised.value.issues}


def test_sibling_order_and_sequence_gaps():
    data = example()
    data["sections"][0]["sibling_sequence"] = 5
    with pytest.raises(ContentValidationError) as raised:
        validate_content(data)
    assert raised.value.issues[0].code == "sibling_document_order"


def test_arbitrary_depth_forest_without_recursion_and_unordered_input():
    data = example()
    data["sections"] = [
        dict(
            source_key=str(i),
            parent_source_key=str(i - 1) if i else None,
            kind="section",
            title="S",
            sibling_sequence=0,
        )
        for i in range(4000)
    ]
    data["sections"].reverse()
    data["segments"] = [
        dict(source_key="s", section_source_key="3999", sequence=999, kind="prose", html_content="<p>Deep</p>")
    ]
    validated = validate_content(data)
    assert validated.root_by_section["3999"] == "0"


def test_json_must_be_utf8():
    with pytest.raises(ContentValidationError) as raised:
        validate_content(json.dumps(example()).encode("utf-16"))
    assert raised.value.issues[0].code == "invalid_json"
