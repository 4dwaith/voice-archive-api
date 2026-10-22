"""Prepared-content asset checks using persisted file metadata."""

from functools import partial

import pytest
from sqlalchemy.orm import Session

from tests.database.content_helpers import add_audio, add_section
from tests.database.files.test_file_ownership import add_book, add_file
from voice_archive_api.logic.content_validation.assets import referenced_file_ids, validate_assets
from voice_archive_api.logic.content_validation.validation import ContentValidationError, validate_content
from voice_archive_api.logic.files import FileKind
from voice_archive_api.services.files.service import get_prepared_assets


@pytest.fixture
def prepared_content_assets(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        track = add_audio(db)
        pdf = add_file(db, kind=FileKind.SOURCE_PDF)
        data = dict(
            schema_version=1,
            source_pdf_file_id=pdf.id,
            sections=[dict(source_key="r", kind="part", title="R", sibling_sequence=0, audio_file_id=track.id)],
            segments=[
                dict(
                    source_key="s",
                    section_source_key="r",
                    sequence=0,
                    kind="prose",
                    html_content="<p>T</p>",
                    narration_start_ms=0,
                    narration_end_ms=1000,
                    regions=[dict(page_index=0, x0=0, y0=0, x1=1, y1=1)],
                )
            ],
        )
        db.commit()
        yield db, book, pdf, track, data


def test_database_asset_lookup_and_no_assets(prepared_content_assets):
    db, book, _, _, data = prepared_content_assets
    validate_loaded_assets(validate_content(data), partial(get_prepared_assets, db), book_id=book.id)
    data["source_pdf_file_id"] = None
    data["sections"] = []
    data["segments"] = [dict(source_key="s", sequence=0, kind="prose", html_content="<p>Text only</p>")]
    validate_loaded_assets(validate_content(data), partial(get_prepared_assets, db), book_id=book.id)


@pytest.mark.parametrize(
    "reference, issue_path",
    [
        pytest.param(("source_pdf_file_id",), "$.source_pdf_file_id", id="pdf"),
        pytest.param(("sections", 0, "audio_file_id"), "$.sections[0].audio_file_id", id="audio"),
    ],
)
def test_database_asset_lookup_rejects_missing_file(prepared_content_assets, reference, issue_path):
    db, book, _, _, data = prepared_content_assets
    target = data
    for key in reference[:-1]:
        target = target[key]
    target[reference[-1]] = 9_223_372_036_854_775_807
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert ("asset_not_found", issue_path) in [(i.code, i.path) for i in raised.value.issues]


@pytest.mark.parametrize(
    "reference, issue_path",
    [
        pytest.param(("source_pdf_file_id",), "$.source_pdf_file_id", id="pdf"),
        pytest.param(("sections", 0, "audio_file_id"), "$.sections[0].audio_file_id", id="audio"),
    ],
)
def test_database_asset_lookup_rejects_wrong_file_kind(prepared_content_assets, reference, issue_path):
    db, book, _, _, data = prepared_content_assets
    cover = add_file(db, key="wrong-kind-cover", kind=FileKind.COVER)
    target = data
    for key in reference[:-1]:
        target = target[key]
    target[reference[-1]] = cover.id
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert ("asset_kind", issue_path) in [(i.code, i.path) for i in raised.value.issues]


def test_database_asset_lookup_rejects_pdf_owned_by_another_book(prepared_content_assets):
    db, book, pdf, _, data = prepared_content_assets
    pdf.book_id = add_book(db, "Other book").id
    db.flush()
    db.expire_all()
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert [(i.code, i.path) for i in raised.value.issues] == [("asset_owned", "$.source_pdf_file_id")]


def test_database_asset_lookup_rejects_already_owned_audio(prepared_content_assets):
    db, book, _, track, data = prepared_content_assets
    track.section_id = add_section(db, book.id).id
    db.flush()
    db.expire_all()
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert ("asset_owned", "$.sections[0].audio_file_id") in [(i.code, i.path) for i in raised.value.issues]


def test_database_asset_lookup_rejects_page_outside_prepared_pdf(prepared_content_assets):
    db, book, pdf, _, data = prepared_content_assets
    data["segments"][0]["regions"][0]["page_index"] = pdf.file_metadata["page_count"]
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert [(i.code, i.path) for i in raised.value.issues] == [("page_bounds", "$.segments[0].regions[0].page_index")]


def test_database_asset_lookup_rejects_narration_beyond_audio_duration(prepared_content_assets):
    db, book, _, track, data = prepared_content_assets
    track.file_metadata = {"duration_ms": 999}
    db.flush()
    db.expire_all()
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert [(i.code, i.path) for i in raised.value.issues] == [("audio_duration", "$.segments[0].narration_end_ms")]


def test_database_asset_lookup_does_not_use_nested_audio_as_root_audio(prepared_content_assets):
    db, book, _, track, data = prepared_content_assets
    data["sections"][0].pop("audio_file_id")
    data["sections"].append(
        dict(
            source_key="nested",
            parent_source_key="r",
            kind="section",
            title="Nested",
            sibling_sequence=0,
            audio_file_id=track.id,
        )
    )
    data["segments"][0]["section_source_key"] = "nested"
    validated = validate_content(data)
    with pytest.raises(ContentValidationError) as raised:
        validate_loaded_assets(validated, partial(get_prepared_assets, db), book_id=book.id)
    assert [(i.code, i.path) for i in raised.value.issues] == [
        ("root_audio_required", "$.segments[0].narration_start_ms")
    ]


def validate_loaded_assets(validated, lookup, *, book_id):
    validate_assets(validated, lookup(referenced_file_ids(validated)), book_id=book_id)
