"""Clean up inactive temporary files without accessing the database."""

import logging

from voice_archive_api.file_handling.storage import Storage

logger = logging.getLogger(__name__)


def run_temporary_cleanup(storage: Storage) -> int:
    removed = storage.clear_stale()
    logger.info("Temporary-file cleanup completed: %s files deleted", removed)
    return removed
