from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import command
from tests.database.content_helpers import add_audio, add_section
from tests.database.files.test_file_ownership import add_file
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.models.user import User
from voice_archive_api.services.books.schemas import BookCreate
from voice_archive_api.services.books.service import create_book


def test_uploader_backfill_uses_linked_book_owner_and_unassigned_for_unlinked(test_engine, migration_config):
    with Session(test_engine) as db:
        user = User(email="reader@example.com", is_enabled=False)
        db.add(user)
        db.commit()
        book = create_book(db, BookCreate(title="Private", language="en"))
        section = add_section(db, book.id)
        cover = add_file(db, book_id=book.id)
        audio = add_audio(db, section_id=section.id)
        unlinked = add_audio(db)
        book.owner_user_id = user.id
        db.commit()
        user_id, cover_id, audio_id, unlinked_id = user.id, cover.id, audio.id, unlinked.id
        original = {
            record.id: (record.storage_key, record.book_id, record.section_id, record.created_at)
            for record in (cover, audio, unlinked)
        }
    command.downgrade(migration_config, "0028_users_and_ownership")

    command.upgrade(migration_config, "head")

    with Session(test_engine) as db:
        records = {record.id: record for record in db.scalars(select(StoredFile))}

        assert records[cover_id].uploaded_by_user_id == records[audio_id].uploaded_by_user_id == user_id
        assert records[unlinked_id].uploaded_by_user_id == 1
        assert {
            identity: (record.storage_key, record.book_id, record.section_id, record.created_at)
            for identity, record in records.items()
        } == original
