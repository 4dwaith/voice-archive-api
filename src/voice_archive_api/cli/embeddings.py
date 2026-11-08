"""Submit missing window embeddings to Google's asynchronous Batch API."""

import argparse
import json
import sys

from pydantic_settings.exceptions import SettingsError
from sqlalchemy.exc import SQLAlchemyError

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.services.embedding_batches import submit_book_embedding_batches


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", type=int, required=True)
    args = parser.parse_args(argv)
    if not 0 < args.book_id <= 9_223_372_036_854_775_807:
        parser.error("Use a positive book ID within the PostgreSQL BIGINT range")
    try:
        try:
            settings = Settings()
        except ValueError, SettingsError:
            print("Invalid configuration; check VOICE_ARCHIVE_* environment variables", file=sys.stderr)
            return 1
        engine = create_database_engine(settings.database_url)
        try:
            batches = submit_book_embedding_batches(create_session_factory(engine), settings, args.book_id)
        finally:
            engine.dispose()
        print(json.dumps({"book_id": args.book_id, "batches": batches}))
        return 0
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except SQLAlchemyError:
        print("Database operation failed; verify batch records before retrying", file=sys.stderr)
        return 3
    return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
