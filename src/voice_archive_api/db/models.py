"""Register complete model metadata for migrations and application sessions."""

from voice_archive_api.book_files.model import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.books.model import Book
from voice_archive_api.content.model import BookSection, BookSegment

__all__ = ["Book", "StoredFile", "PendingFileDeletion", "FileCleanupProgress", "BookSection", "BookSegment"]
