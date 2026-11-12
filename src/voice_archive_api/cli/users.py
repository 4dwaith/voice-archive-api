"""Trusted local account provisioning and public-demo designation."""

import argparse
import logging
import sys
from datetime import UTC, datetime
from getpass import getpass

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import configure_logging, log_exception
from voice_archive_api.core.passwords import create_password_hash
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.series import Series
from voice_archive_api.db.models.user import User
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.logic.users import normalize_email_address
from voice_archive_api.services.series.service import lock_series_books

logger = logging.getLogger(__name__)


def publish_demo_content(db: Session, *, book_id: int | None, series_id: int | None) -> None:
    now = datetime.now(UTC)
    if series_id is not None:
        series = db.get(Series, series_id, with_for_update=True)
        if series is None:
            raise ValueError("Series not found")
        books = lock_series_books(db, series_id)
        series.owner_user_id = None
        series.updated_at = now
    else:
        book = db.get(Book, book_id, with_for_update=True)
        if book is None:
            raise ValueError("Book not found")
        if book.series_id is not None:
            raise ValueError("Publish the entire series using --series-id")
        books = [book]
    for book in books:
        book.owner_user_id = None
        book.updated_at = now


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "reset-password"):
        command = commands.add_parser(name)
        command.add_argument("email")
        if name == "create":
            command.add_argument("--display-name")
    publish = commands.add_parser("publish-demo")
    target = publish.add_mutually_exclusive_group(required=True)
    target.add_argument("--book-id", type=int)
    target.add_argument("--series-id", type=int)
    args = parser.parse_args(argv)
    email = None
    password_hash = None
    configure_logging()
    settings = None
    try:
        if args.command != "publish-demo":
            email = normalize_email_address(args.email)
            password = getpass("Password: ")
            if password != getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
            password_hash = create_password_hash(password)
        elif not 0 < (args.book_id or args.series_id or 0) <= 9_223_372_036_854_775_807:
            raise ValueError("Use a positive book or series ID within the PostgreSQL BIGINT range")
        settings = Settings()
        engine = create_database_engine(settings.database_url)
        try:
            with create_session_factory(engine)() as db:
                if args.command == "publish-demo":
                    publish_demo_content(db, book_id=args.book_id, series_id=args.series_id)
                else:
                    user = db.scalar(select(User).where(func.lower(User.email) == email).with_for_update())
                    if args.command == "create":
                        if user is not None:
                            raise ValueError("Email already exists")
                        db.add(
                            User(
                                email=email,
                                display_name=args.display_name,
                                password_hash=password_hash,
                                is_enabled=True,
                            )
                        )
                    else:
                        if user is None:
                            raise ValueError("User not found")
                        user.password_hash = password_hash
                        user.is_enabled = True
                db.commit()
        finally:
            engine.dispose()
        print(f"Completed {args.command}.")
        return 0
    except ValidationError as exc:
        log_exception(logger, "Account administration configuration failed", exc)
        print("Invalid configuration; check VOICE_ARCHIVE_* settings", file=sys.stderr)
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) or "Account administration cancelled", file=sys.stderr)
    except SQLAlchemyError as exc:
        log_exception(
            logger,
            f"Account administration failed: operation={args.command} "
            f"book={getattr(args, 'book_id', None)} series={getattr(args, 'series_id', None)}",
            exc,
            settings=settings,
        )
        print(
            "Database operation failed; inspect current state before retrying. No automatic retry was attempted.",
            file=sys.stderr,
        )
    return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
