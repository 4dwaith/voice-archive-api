"""Import prepared content through the content service using configured PostgreSQL."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from voice_archive_api.application.content_validation.schemas import MAX_PAYLOAD_BYTES
from voice_archive_api.application.content_validation.validation import ContentValidationError, validate_content
from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.domain.content import service


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", required=True, type=int)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.book_id <= 9_223_372_036_854_775_807:
        parser.error("Use a positive book ID within the PostgreSQL BIGINT range")
    try:
        with args.json.open("rb") as source:
            payload = source.read(MAX_PAYLOAD_BYTES + 1)
    except OSError as exc:
        print(f"Cannot read input: {exc}", file=sys.stderr)
        return 1

    try:
        validated = validate_content(payload)
        try:
            settings = Settings()
        except ValidationError:
            print("Invalid configuration; check the VOICE_ARCHIVE_* environment variables", file=sys.stderr)
            return 1

        engine = create_database_engine(settings.database_url)

        try:
            with create_session_factory(engine)() as db:
                result = service.publish(db, args.book_id, validated, dry_run=args.dry_run)
        finally:
            engine.dispose()

        print(result.model_dump_json())
        return 0
    except ContentValidationError as exc:
        print(
            json.dumps(
                {
                    "error": {"code": "content_validation_error", "message": "Content validation failed"},
                    "issues": [asdict(issue) for issue in exc.issues],
                }
            ),
            file=sys.stderr,
        )
        return 1
    except ApplicationError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        # Exception text can contain connection credentials. A lost connection at
        # commit can leave publication uncertain, so never retry automatically.
        print("Database operation failed; no automatic retry was attempted.", file=sys.stderr)
        if args.dry_run:
            return 1
        print("Inspect the book's current content before retrying; publication may have committed.", file=sys.stderr)
        return 3


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
