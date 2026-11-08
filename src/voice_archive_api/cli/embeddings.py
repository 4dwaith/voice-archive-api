"""Queue a book for sequential embedding batches, submitting the first group if idle."""

import argparse
import json
import logging
import sys

from pydantic_settings.exceptions import SettingsError
from sqlalchemy.exc import SQLAlchemyError

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.logic.batches import BookBatchJobState
from voice_archive_api.services.embeddings.service import submit_book_embedding_batches

logger = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", type=int, required=True)
    args = parser.parse_args(argv)
    if not 0 < args.book_id <= 9_223_372_036_854_775_807:
        parser.error("Use a positive book ID within the PostgreSQL BIGINT range")
    try:
        try:
            settings = Settings()
        except (ValueError, SettingsError) as exc:
            log_exception(logger, "Embedding CLI configuration failed", exc)
            print("Invalid configuration; check VOICE_ARCHIVE_* environment variables", file=sys.stderr)
            return 1
        engine = create_database_engine(settings.database_url)
        try:
            result = submit_book_embedding_batches(create_session_factory(engine), settings, args.book_id)
        finally:
            engine.dispose()
        print(json.dumps(result))
        return int(result["state"] == BookBatchJobState.FAILED)
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except SQLAlchemyError as exc:
        log_exception(logger, f"Embedding CLI database operation failed: book={args.book_id}", exc, settings=settings)
        print("Database operation failed; verify batch records before retrying", file=sys.stderr)
        return 3
    return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
