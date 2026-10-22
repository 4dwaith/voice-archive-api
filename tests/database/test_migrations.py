"""Migration guarantees shared by all application models."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from voice_archive_api.db.base import Base
from voice_archive_api.domain.books.model import Book
from voice_archive_api.domain.files.model import FileCleanupProgress, FileKind, PendingFileDeletion, StoredFile


def test_current_migration_schema_matches_models(test_engine):
    with test_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_full_downgrade_and_repeatable_upgrade_to_head(test_engine, migration_config):
    original_tables = set(inspect(test_engine).get_table_names())
    with test_engine.connect() as connection:
        original_heads = MigrationContext.configure(connection).get_current_heads()

    command.downgrade(migration_config, "base")

    # Alembic retains its bookkeeping table; all application tables must be gone.
    assert inspect(test_engine).get_table_names() == ["alembic_version"]
    with test_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ()

    # The first upgrade restores the schema; repeating it must be a safe no-op.
    for _ in range(2):
        command.upgrade(migration_config, "head")
        assert set(inspect(test_engine).get_table_names()) == original_tables
        with test_engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_heads() == original_heads


@pytest.mark.parametrize("starting_revision", ["0004_book_files", "0007_file_cleanup_progress"])
def test_unified_files_migration_preserves_overlapping_ids_metadata_and_links(
    test_engine, migration_config, starting_revision
):
    command.downgrade(migration_config, starting_revision)
    with test_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO cover_files (id, storage_key, byte_size, width, height, created_at)
            VALUES (1, 'promoted/cover', 100, 16, 24, '2026-01-15T03:15:30Z')""")
        )
        connection.execute(
            text("""INSERT INTO source_pdf_files (id, storage_key, byte_size, page_count, created_at)
            VALUES (1, 'promoted/pdf', 200, 3, '2026-01-15T03:15:30Z')""")
        )
    command.upgrade(migration_config, "0007_file_cleanup_progress")
    with test_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO books
            (title, normalized_title, normalized_author, language, cover_file_id, source_pdf_file_id, created_at, updated_at)
            VALUES ('Owner', 'owner', '', 'en', 1, 1, now(), now())""")
        )
        connection.execute(
            text("""INSERT INTO pending_file_deletions (storage_key, queued_at)
            VALUES ('promoted/pending', now())""")
        )
        connection.execute(
            text("""UPDATE file_cleanup_progress
            SET cover_cleanup_before = now(), source_pdf_cleanup_before = now()""")
        )
    # Exercise a populated downgrade too, preserving references to both kinds.
    for iteration in range(2):
        command.upgrade(migration_config, "head")
        with Session(test_engine) as db:
            records = {record.kind: record for record in db.scalars(select(StoredFile))}
            cover, pdf = records[FileKind.COVER], records[FileKind.SOURCE_PDF]
            assert cover.id != pdf.id
            assert (cover.storage_key, cover.byte_size, cover.file_metadata) == (
                "promoted/cover",
                100,
                {"width": 16, "height": 24},
            )
            assert (pdf.storage_key, pdf.byte_size, pdf.file_metadata) == (
                "promoted/pdf",
                200,
                {"page_count": 3},
            )
            assert cover.created_at == pdf.created_at
            assert cover.created_at.isoformat() == "2026-01-15T03:15:30+00:00"
            book = db.scalars(select(Book)).one()
            assert (book.cover_file_id, book.source_pdf_file_id) == (cover.id, pdf.id)
            assert db.get(PendingFileDeletion, "promoted/pending") is not None
            assert db.get(FileCleanupProgress, 1).cleanup_before is None
        if iteration == 0:
            command.downgrade(migration_config, "0007_file_cleanup_progress")
    with Session(test_engine) as db:
        record = StoredFile(
            kind=FileKind.COVER,
            storage_key="promoted/new",
            byte_size=1,
            file_metadata={"width": 16, "height": 24},
            created_at=cover.created_at,
        )
        db.add(record)
        db.commit()
        assert record.id > max(cover.id, pdf.id)
