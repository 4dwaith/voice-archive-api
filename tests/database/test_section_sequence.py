"""Existing hierarchies are backfilled in preorder without changing dataset identity."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.orm import Session

from alembic import command
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection


def test_section_sequence_backfill_preserves_nested_order_and_content_version(test_engine, migration_config):
    command.downgrade(migration_config, "0012_content_domain")
    version = uuid4()
    historical_books = Table("books", MetaData(), autoload_with=test_engine)
    now = datetime.now(UTC)
    with Session(test_engine) as db:
        book_id, other_id = [
            db.scalar(
                historical_books.insert()
                .values(
                    title=title,
                    normalized_title=title.lower(),
                    normalized_author="",
                    language="en",
                    created_at=now,
                    updated_at=now,
                    content_version=version,
                    content_schema_version=1,
                    content_imported_at=now,
                )
                .returning(historical_books.c.id)
            )
            for title in ["Owner", "Other"]
        ]
        # Sibling values have gaps; IDs and insertion order do not define reading order.
        for row_id, owner_book_id, key, parent, sibling in [
            (10, book_id, "later", None, 20),
            (20, book_id, "first", None, 5),
            (30, book_id, "right", 20, 30),
            (40, book_id, "left", 20, 10),
            (50, book_id, "deep", 40, 7),
            (60, other_id, "other", None, 0),
        ]:
            db.execute(
                text("""INSERT INTO sections(id,book_id,source_key,parent_id,kind,title,sibling_sequence)
                VALUES (:id,:book,:key,:parent,'section',:key,:sibling)"""),
                dict(id=row_id, book=owner_book_id, key=key, parent=parent, sibling=sibling),
            )
        db.commit()

    def assert_backfilled_content():
        with Session(test_engine) as db:
            rows = list(
                db.scalars(select(BookSection).where(BookSection.book_id == book_id).order_by(BookSection.sequence))
            )
            assert [row.source_key for row in rows] == ["first", "left", "deep", "right", "later"]
            assert [row.sequence for row in rows] == list(range(5))
            assert db.scalar(select(BookSection.sequence).where(BookSection.book_id == other_id)) == 0
            assert db.get(Book, book_id).content_version == version

    command.upgrade(migration_config, "head")
    assert_backfilled_content()

    command.downgrade(migration_config, "0012_content_domain")
    command.upgrade(migration_config, "head")
    assert_backfilled_content()
