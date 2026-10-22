"""Content publication, manifest, and purge with transactional persistence."""

from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from itertools import batched
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from voice_archive_api.application.content_validation.assets import validate_referenced_assets
from voice_archive_api.application.content_validation.schemas import ContentInput, SectionInput
from voice_archive_api.application.content_validation.validation import (
    ContentIssue,
    ContentValidationError,
    ValidatedContent,
)
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.domain.book_cleanup import delete_book_content_and_files
from voice_archive_api.domain.books.service import book_write, claim_file, get_book
from voice_archive_api.domain.content.model import BookSection, BookSegment
from voice_archive_api.domain.content.schemas import ContentManifest, ImportResult
from voice_archive_api.domain.files import service as files
from voice_archive_api.domain.files.model import FileKind

BATCH_SIZE = 500


class ContentExists(ApplicationError):
    code = "content_exists"
    message = "Book content already exists; inspect it before explicitly purging"


class ContentPreconditionRequired(ApplicationError):
    code = "content_precondition_required"
    message = "Supply a specific strong If-Match ETag from GET book content"


class ContentPreconditionFailed(ApplicationError):
    code = "content_precondition_failed"
    message = "Book content state no longer matches If-Match; inspect its current state"


def publish(db: Session, book_id: int, validated: ValidatedContent, *, dry_run: bool) -> ImportResult:
    with book_write(db):
        book = get_book(db, book_id, for_update=True)
        if book.content_version is not None:
            raise ContentExists
        content = validated.content
        if book.source_pdf_file_id is not None and book.source_pdf_file_id != content.source_pdf_file_id:
            raise ContentValidationError(
                [
                    ContentIssue(
                        "pdf_mismatch",
                        "$.source_pdf_file_id",
                        None,
                        "Canonical PDF ID must match the PDF already attached to this book",
                    )
                ]
            )
        validate_referenced_assets(
            validated,
            partial(files.get_prepared_assets, db, for_update=True),
            book_id=book_id,
        )
        result = ImportResult(
            dry_run=dry_run,
            section_count=len(content.sections),
            segment_count=len(content.segments),
            content_version=None,
        )
        if dry_run:
            # Release locks without making any persistent changes or reserving uploads.
            db.rollback()
            return result

        section_ids = allocate_section_ids(db, [s.source_key for s in content.sections])

        insert_book_content(book_id, section_ids, content, db)

        for section in content.sections:
            if section.audio_file_id is not None:
                files.claim_section_audio(db, section.audio_file_id, section_id=section_ids[section.source_key])

        if content.source_pdf_file_id is not None and book.source_pdf_file_id is None:
            claim_file(db, content.source_pdf_file_id, book_id=book_id, kind=FileKind.SOURCE_PDF)

        book.content_version = uuid4()
        book.content_schema_version = content.schema_version
        book.content_imported_at = datetime.now(UTC)
        db.commit()

        result.content_version = book.content_version

        return result


def allocate_section_ids(db: Session, source_keys: list[str]) -> dict[str, int]:
    ids = db.scalars(
        text("SELECT nextval(pg_get_serial_sequence('sections', 'id')) FROM generate_series(1, :count)"),
        {"count": len(source_keys)},
    )
    return dict(zip(source_keys, ids, strict=True))


def insert_book_content(book_id: int, section_ids: dict[str, int], content: ContentInput, db: Session) -> None:
    # Allocate IDs once and order parents before children, independent of input order.
    children: dict[str | None, list[SectionInput]] = defaultdict(list)
    for section in content.sections:
        children[section.parent_source_key].append(section)
    stack = list(children[None])

    def section_rows() -> Iterator[dict[str, Any]]:
        while stack:
            section = stack.pop()
            yield dict(
                id=section_ids[section.source_key],
                book_id=book_id,
                source_key=section.source_key,
                parent_id=section_ids[section.parent_source_key] if section.parent_source_key is not None else None,
                kind=section.kind,
                title=section.title,
                sibling_sequence=section.sibling_sequence,
            )
            stack.extend(children[section.source_key])

    def segment_rows() -> Iterator[dict[str, Any]]:
        for segment in content.segments:
            row = segment.model_dump(exclude={"section_source_key"})
            row.update(
                book_id=book_id,
                section_id=section_ids[segment.section_source_key] if segment.section_source_key is not None else None,
                text_content=segment.text_content,
            )
            yield row

    for batch in batched(section_rows(), BATCH_SIZE, strict=False):
        db.execute(insert(BookSection), list(batch))

    for batch1 in batched(segment_rows(), BATCH_SIZE, strict=False):
        db.execute(insert(BookSegment), list(batch1))


def get_manifest_and_etag(db: Session, book_id: int) -> tuple[ContentManifest, str]:
    book = get_book(db, book_id)
    state = (
        f"content-{book.content_version}"
        if book.content_version
        else f"pdf-{book.source_pdf_file_id}"
        if book.source_pdf_file_id
        else "empty"
    )

    etag = f'"book-content-v1-{book.id}-{state}"'

    manifest = ContentManifest(
        state="published" if book.content_version else "pdf_only" if book.source_pdf_file_id else "empty",
        content_version=book.content_version,
        schema_version=book.content_schema_version,
        imported_at=book.content_imported_at,
        source_pdf_file_id=book.source_pdf_file_id,
    )

    return manifest, etag


def purge(db: Session, book_id: int, if_match: str | None) -> None:
    with book_write(db):
        book = get_book(db, book_id, for_update=True)
        if if_match is None:
            raise ContentPreconditionRequired
        # Only the current specific strong tag can authorize this destructive operation.
        # Wildcards, weak tags and lists cannot match this contract.
        state = (
            f"content-{book.content_version}"
            if book.content_version
            else f"pdf-{book.source_pdf_file_id}"
            if book.source_pdf_file_id
            else "empty"
        )
        if if_match.strip() != f'"book-content-v1-{book.id}-{state}"':
            raise ContentPreconditionFailed
        delete_book_content_and_files(db, book_id)
        book.content_version = None
        book.content_schema_version = None
        book.content_imported_at = None
        db.commit()
