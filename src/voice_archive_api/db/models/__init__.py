"""Register complete model metadata for migrations and application sessions."""

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.file import FileCleanupProgress, PendingFileDeletion, StoredFile
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.models.summary import SummaryBatchCheckpoint, SummaryGenerationRun
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding

__all__ = [
    "Series",
    "Book",
    "StoredFile",
    "PendingFileDeletion",
    "FileCleanupProgress",
    "BookSection",
    "BookSegment",
    "SummaryGenerationRun",
    "SummaryBatchCheckpoint",
    "RetrievalWindow",
    "WindowEmbedding",
    "EmbeddingBatchJob",
    "EmbeddingBatchRequest",
]
