"""Generate English summaries for a book or root section, or clear a book's summaries."""

import argparse
import asyncio
import json
import sys

from pydantic_settings.exceptions import SettingsError
from sqlalchemy.exc import SQLAlchemyError

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.services.summary.service import clear_book_summaries, generate_book_summaries


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["generate", "clear"])
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--root-section-id", type=int, help="Generate only this root section's complete subtree")
    parser.add_argument(
        "--allow-stale-downstream", action="store_true", help="Allow existing later summaries to become stale"
    )
    parser.add_argument("--retries", type=int, choices=range(3), default=1)
    args = parser.parse_args(argv)
    if not 0 < args.book_id <= 9_223_372_036_854_775_807:
        parser.error("Use a positive book ID within the PostgreSQL BIGINT range")
    if args.root_section_id is not None and not 0 < args.root_section_id <= 9_223_372_036_854_775_807:
        parser.error("Use a positive root section ID within the PostgreSQL BIGINT range")
    if args.operation == "clear" and (args.root_section_id is not None or args.allow_stale_downstream):
        parser.error("--root-section-id and --allow-stale-downstream apply only to generate")
    try:
        try:
            settings = Settings()
        except ValueError, SettingsError:
            print("Invalid configuration; check VOICE_ARCHIVE_* environment variables", file=sys.stderr)
            return 1
        engine = create_database_engine(settings.database_url)
        try:
            factory = create_session_factory(engine)
            version = (
                clear_book_summaries(factory, args.book_id)
                if args.operation == "clear"
                else asyncio.run(
                    generate_book_summaries(
                        factory,
                        settings,
                        args.book_id,
                        retries=args.retries,
                        root_section_id=args.root_section_id,
                        allow_stale_downstream=args.allow_stale_downstream,
                        report_progress=lambda message: print(message, file=sys.stderr),
                    )
                )
            )
        finally:
            engine.dispose()
        print(json.dumps({"book_id": args.book_id, "content_version": str(version) if version else None}))
        return 0
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "Database operation failed. Inspect the book before retrying; publication may have committed.",
            file=sys.stderr,
        )
        return 3


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
