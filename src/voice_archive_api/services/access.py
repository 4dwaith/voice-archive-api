"""Ownership checks for requests and explicitly trusted local jobs."""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, SQLColumnExpression, and_, false, or_, select, true
from sqlalchemy.orm import Session

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection
from voice_archive_api.db.models.file import StoredFile


@dataclass(frozen=True)
class AccessContext:
    user_id: int | None
    is_write: bool = False
    is_trusted: bool = False


def get_access_context(db: Session) -> AccessContext:
    # Missing authentication metadata grants guest access, never trusted access.
    access: AccessContext | None = db.info.get("access")
    if access is None:
        return AccessContext(user_id=None)
    return access


def is_public(access: AccessContext) -> bool:
    """Reads may include public resources; writes require private ownership."""
    return not access.is_write


def create_owner_filter(db: Session, owner: SQLColumnExpression[int | None]) -> ColumnElement[bool]:
    """Build a SQL condition, without executing a query.

    For example, with owner=Book.owner_user_id and user 3, reads match
    ``owner_user_id IS NULL OR owner_user_id = 3``; writes match only
    ``owner_user_id = 3``. A guest has no own rows, even though public rows
    also use NULL: comparing the column with a missing user ID would be wrong.
    """
    access = get_access_context(db)
    if access.is_trusted:
        return true()
    is_owned_by_user: ColumnElement[bool] = false()
    if access.user_id is not None:
        is_owned_by_user = owner == access.user_id
    if is_public(access):
        return or_(owner.is_(None), is_owned_by_user)
    return is_owned_by_user


def can_access_resource(db: Session, owner_user_id: int | None) -> bool:
    """Check an already loaded owner ID using the same rule as the SQL filter."""
    access = get_access_context(db)
    if access.is_trusted:
        return True
    if is_public(access) and owner_user_id is None:
        return True
    if access.user_id is None:
        return False
    return access.user_id == owner_user_id


def create_file_access_filter(db: Session) -> ColumnElement[bool]:
    access = get_access_context(db)
    if access.is_trusted:
        return true()
    # Path 1: a directly linked file follows its book's ownership.
    has_accessible_book = (
        select(Book.id).where(Book.id == StoredFile.book_id, create_owner_filter(db, Book.owner_user_id)).exists()
    )

    # Path 2: section audio follows the section's book ownership.
    has_accessible_section_book = (
        select(BookSection.id)
        .join(Book, Book.id == BookSection.book_id)
        .where(BookSection.id == StoredFile.section_id, create_owner_filter(db, Book.owner_user_id))
        .exists()
    )

    # Path 3: only unlinked files use uploader identity to grant access.
    # Requiring both links to be absent prevents uploader access to foreign books.
    is_uploaded_by_user: ColumnElement[bool] = false()
    if access.user_id is not None:
        is_uploaded_by_user = StoredFile.uploaded_by_user_id == access.user_id
    is_accessible_unlinked_upload = and_(
        StoredFile.book_id.is_(None),
        StoredFile.section_id.is_(None),
        is_uploaded_by_user,
    )
    return or_(has_accessible_book, has_accessible_section_book, is_accessible_unlinked_upload)
