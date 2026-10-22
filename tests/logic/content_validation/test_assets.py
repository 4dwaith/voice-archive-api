"""Prepared-file validation and lookup orchestration using in-memory snapshots."""

import pytest

from tests.logic.content_validation.helpers import example, prepared
from voice_archive_api.logic.content_validation.assets import validate_assets
from voice_archive_api.logic.content_validation.validation import ContentValidationError, validate_content
from voice_archive_api.logic.files import FileKind, PreparedAsset


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("missing_pdf", "pdf_required"),
        ("missing_audio", "root_audio_required"),
        ("nested_only", "root_audio_required"),
        ("short_audio", "audio_duration"),
        ("bad_duration", "root_audio_required"),
        ("bad_pages", "page_bounds"),
        ("wrong_book", "asset_owned"),
        ("owned_audio", "asset_owned"),
        ("sectionless", "root_audio_required"),
        ("duplicate_audio", "duplicate_audio"),
        ("wrong_kind", "asset_kind"),
        ("missing_file", "asset_not_found"),
    ],
)
def test_asset_failures(change, code):
    data, assets = example(), prepared()
    if change == "missing_pdf":
        data["source_pdf_file_id"] = None
    if change == "missing_audio":
        assets.pop(201)
    if change == "nested_only":
        data["sections"][0]["audio_file_id"] = None
    if change == "short_audio":
        assets[201] = PreparedAsset(201, FileKind.AUDIO_CHAPTER, None, None, {"duration_ms": 3999})
    if change == "bad_duration":
        assets[201] = PreparedAsset(201, FileKind.AUDIO_CHAPTER, None, None, {"duration_ms": True})
    if change == "bad_pages":
        assets[101] = PreparedAsset(101, FileKind.SOURCE_PDF, None, None, {"page_count": 1})
    if change == "wrong_book":
        assets[101] = PreparedAsset(101, FileKind.SOURCE_PDF, 8, None, {"page_count": 2})
    if change == "owned_audio":
        assets[201] = PreparedAsset(201, FileKind.AUDIO_CHAPTER, None, 9, {"duration_ms": 7000})
    if change == "sectionless":
        data["segments"][1]["section_source_key"] = None
    if change == "duplicate_audio":
        data["sections"][1]["audio_file_id"] = 201
    if change == "wrong_kind":
        assets[201] = assets[101]
    if change == "missing_file":
        assets.pop(202)
    with pytest.raises(ContentValidationError) as raised:
        validate_assets(validate_content(data), assets, book_id=7)
    assert code in {i.code for i in raised.value.issues}


def test_nested_owned_audio_does_not_fallback_when_root_missing():
    data = example()
    data["sections"][0]["audio_file_id"] = None
    for segment in data["segments"]:
        for key in ("narration_start_ms", "narration_end_ms", "narration_seek_ms"):
            segment.pop(key, None)
    validated = validate_content(data)
    validate_assets(validated, prepared(), book_id=7)
    assert validated.playback_audio_id("nested") is None


@pytest.mark.parametrize("failure", ["omitted", "missing", "invalid_metadata"])
def test_pdf_failure_reports_one_root_issue_without_region_cascades(failure):
    data, assets = example(), prepared()
    if failure == "omitted":
        data["source_pdf_file_id"] = None
        expected_code = "pdf_required"
    elif failure == "missing":
        assets.pop(101)
        expected_code = "asset_not_found"
    else:
        assets[101] = PreparedAsset(101, FileKind.SOURCE_PDF, None, None, {"page_count": True})
        expected_code = "pdf_metadata"
    with pytest.raises(ContentValidationError) as raised:
        validate_assets(validate_content(data), assets, book_id=7)
    assert [(issue.code, issue.path) for issue in raised.value.issues] == [(expected_code, "$.source_pdf_file_id")]


def test_referenced_file_ids_collects_distinct_pdf_and_audio():
    from voice_archive_api.logic.content_validation.assets import referenced_file_ids

    assert referenced_file_ids(validate_content(example())) == {101, 201, 202}


def test_text_only_content_has_no_file_ids():
    from voice_archive_api.logic.content_validation.assets import referenced_file_ids

    data = example()
    data["source_pdf_file_id"] = None
    data["sections"] = []
    data["segments"] = [dict(source_key="text", sequence=0, kind="prose", html_content="<p>Text only</p>")]
    assert referenced_file_ids(validate_content(data)) == set()
