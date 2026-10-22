from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.database.content_helpers import add_audio, add_section, add_segment
from tests.database.files.test_file_ownership import add_book, add_file
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.file import PendingFileDeletion, StoredFile
from voice_archive_api.logic.content_validation.kinds import SectionKind, SegmentKind
from voice_archive_api.services.books.service import delete_book


def test_content_roundtrip_and_auto_generated_pages(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        book.content_version, book.content_schema_version, book.content_imported_at = uuid4(), 1, datetime.now(UTC)
        root = add_section(db, book.id)
        nested = add_section(db, book.id, source_key="nested", parent_id=root.id)
        regions = [dict(page_index=p, x0=0.1, y0=0.2, x1=0.8, y1=0.9) for p in (3, 1, 3, 0)]
        row = add_segment(
            db,
            book.id,
            section_id=nested.id,
            regions=regions,
            narration_start_ms=10,
            narration_end_ms=50,
            narration_seek_ms=0,
            alignment_status="uncertain",
            alignment_metadata={"note": "passive"},
        )
        db.refresh(row)
        db.refresh(root)
        assert root.kind is SectionKind.CHAPTER
        assert row.kind is SegmentKind.PROSE
        assert (row.html_content, row.text_content) == ("<p>Text</p>", "Text")
        assert (row.narration_start_ms, row.narration_end_ms, row.narration_seek_ms) == (10, 50, 0)
        assert row.page_indexes == [0, 1, 3]
        assert row.regions == regions
        assert list(db.scalars(select(BookSegment).where(BookSegment.page_indexes.contains([1])))) == [row]

        # Geometry is deliberately an application check: SQL only needs page_index for this projection.
        db.execute(update(BookSegment).where(BookSegment.id == row.id).values(regions=[dict(page_index=7)]))
        db.refresh(row)
        assert row.page_indexes == [7]
        db.commit()


def test_page_projection_helper_and_index_schema(test_engine):
    with test_engine.connect() as db:
        # Text-only segments have no regions, so the helper must return an empty page array.
        assert db.scalar(text("SELECT public.region_pages('[]'::jsonb)")) == []

        # pg_indexes exposes index definitions; verify the migration installed a GIN index
        # to support array-membership lookups such as "segments containing page 7".
        index = db.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_segments_page_indexes'"))
        assert "USING gin" in index

        # pg_proc stores function metadata; regprocedure resolves this exact function signature.
        # provolatile = 'i' means declared IMMUTABLE, required by the stored generated column.
        assert (
            db.scalar(text("SELECT provolatile FROM pg_proc WHERE oid='public.region_pages(jsonb)'::regprocedure"))
            == "i"
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [None],
        [1],
        [{}],
        [{"page_index": None}],
        [{"page_index": True}],
        [{"page_index": "1"}],
        [{"page_index": -1}],
        [{"page_index": 0.5}],
        [{"page_index": 1.0}],
        [{"page_index": 2147483648}],
        [{"page_index": 10**100}],
    ],
)
def test_region_projection_rejects_malformed_direct_writes(test_engine, value):
    with Session(test_engine) as db:
        book = add_book(db)
        with pytest.raises(IntegrityError):
            add_segment(db, book.id, regions=value)


def test_generated_column_cannot_be_supplied(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        with pytest.raises(Exception, match="generated"):
            add_segment(db, book.id, page_indexes=[77])


@pytest.mark.parametrize(
    "changes",
    [
        dict(content_version=uuid4()),
        dict(content_schema_version=1),
        dict(content_imported_at=datetime.now(UTC)),
        dict(content_version=uuid4(), content_schema_version=0, content_imported_at=datetime.now(UTC)),
        dict(content_version=uuid4(), content_schema_version=1),
    ],
)
def test_content_metadata_all_or_nothing(test_engine, changes):
    # All of content version, schema version, and imported at should change together to valid values, or none of them will
    with Session(test_engine) as db:
        book = add_book(db)
        for name, value in changes.items():
            setattr(book, name, value)
        with pytest.raises(IntegrityError):
            db.flush()


@pytest.mark.parametrize("changes", [dict(kind="bad"), dict(sibling_sequence=-1), dict(source_key=""), dict(title="")])
def test_section_local_constraints(test_engine, changes):
    with Session(test_engine) as db, pytest.raises(IntegrityError):
        add_section(db, add_book(db).id, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        dict(kind="caption"),
        dict(sequence=-1),
        dict(source_key=""),
        dict(text_content=""),
        dict(narration_start_ms=0),
        dict(narration_end_ms=1),
        dict(narration_seek_ms=0),
        dict(narration_start_ms=0, narration_end_ms=0),
        dict(narration_start_ms=-1, narration_end_ms=1),
        dict(narration_start_ms=1, narration_end_ms=2, narration_seek_ms=2),
        dict(narration_start_ms=1, narration_end_ms=2, narration_seek_ms=-1),
    ],
)
def test_segment_local_constraints(test_engine, changes):
    with Session(test_engine) as db, pytest.raises(IntegrityError):
        add_segment(db, add_book(db).id, **changes)


@pytest.mark.parametrize("target", ["parent", "segment"])
def test_foreign_keys_on_same_book(test_engine, target):
    with Session(test_engine) as db:
        book1, book2 = add_book(db, "One"), add_book(db, "Two")
        root = add_section(db, book1.id)
        with pytest.raises(IntegrityError):
            if target == "parent":
                add_section(db, book2.id, parent_id=root.id)
            else:
                add_segment(db, book2.id, section_id=root.id)


@pytest.mark.parametrize("target", ["root_order", "child_order", "section_key", "segment_key", "sequence", "self"])
def test_uniqueness_and_self_parent(test_engine, target):
    with Session(test_engine) as db:
        book = add_book(db)
        root = add_section(db, book.id)
        add_segment(db, book.id)
        if target == "child_order":
            add_section(db, book.id, source_key="first", parent_id=root.id)
        with pytest.raises(IntegrityError):
            if target == "root_order":
                add_section(db, book.id, source_key="other")
            if target == "child_order":
                add_section(db, book.id, source_key="other", parent_id=root.id)
            if target == "section_key":
                add_section(db, book.id, sibling_sequence=1)
            if target == "segment_key":
                add_segment(db, book.id, sequence=1)
            if target == "sequence":
                add_segment(db, book.id, source_key="other")
            if target == "self":
                root.parent_id = root.id
                db.flush()


def test_whole_book_delete_rollback_preserves_graph_audio_and_queue(test_engine, monkeypatch):
    with Session(test_engine) as db:
        book = add_book(db)
        root = add_section(db, book.id)
        row = add_segment(db, book.id, section_id=root.id)
        record = add_audio(db, section_id=root.id)
        book_id, root_id, segment_id, file_id, key = book.id, root.id, row.id, record.id, record.storage_key
        db.commit()

        def failed_commit():
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(db, "commit", failed_commit)
        with pytest.raises(RuntimeError, match="simulated"):
            delete_book(db, book_id)
        assert db.get(BookSection, root_id) is not None
        assert db.get(BookSegment, segment_id) is not None
        assert db.get(StoredFile, file_id).section_id == root_id
        assert db.get(PendingFileDeletion, key) is None


def test_book_deletion_removes_content_and_queues_owned_files(test_engine):
    with Session(test_engine) as db:
        book = add_book(db)
        root = add_section(db, book.id)
        nested = add_section(db, book.id, source_key="nested", parent_id=root.id)
        add_segment(db, book.id, section_id=nested.id)
        record = add_audio(db, section_id=nested.id)
        file_id = record.id
        cover = add_file(db, book_id=book.id)
        cover_id = cover.id
        db.commit()
        assert delete_book(db, book.id)
        assert db.get(StoredFile, file_id) is None
        assert db.get(StoredFile, cover_id) is None
        assert db.scalar(select(BookSection.id)) is None
        assert db.scalar(select(BookSegment.id)) is None
        assert db.get(PendingFileDeletion, record.storage_key) is not None
        assert db.get(PendingFileDeletion, cover.storage_key) is not None
