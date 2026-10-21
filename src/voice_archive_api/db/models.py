"""Register complete model metadata for migrations and application sessions."""

from voice_archive_api.book_files.model import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.books.model import Book

__all__ = ["Book", "StoredFile", "PendingFileDeletion", "FileCleanupProgress"]
