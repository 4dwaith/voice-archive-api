"""Rebuild, dry-run or clear whole-segment windows without provider calls."""

import argparse
import json
import sys

from google.genai.local_tokenizer import LocalTokenizer
from pydantic_settings.exceptions import SettingsError
from sqlalchemy.exc import SQLAlchemyError

from voice_archive_api.core.config import Settings
from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.services.windows import clear_book_windows, rebuild_book_windows


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["rebuild", "dry-run", "clear"])
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument(
        "--window-tokens", type=int, help="Default: 1500 Kannada, 1000 otherwise; includes paths and reserve"
    )
    parser.add_argument("--overlap-tokens", type=int, default=125)
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
            factory = create_session_factory(engine)
            result: dict[str, object] = {"book_id": args.book_id}
            if args.operation == "clear":
                clear_book_windows(factory, args.book_id)
                result["cleared"] = True
            else:
                # This SDK model name selects Gemma 3 for local estimates only.
                tokenizer = LocalTokenizer("gemini-2.5-flash")

                def count_tokens(text: str) -> int:
                    count: int | None = tokenizer.count_tokens(text).total_tokens
                    if count is None:
                        raise ValueError("Local tokenizer returned no token count")
                    return count

                windows = rebuild_book_windows(
                    factory,
                    settings,
                    args.book_id,
                    count_tokens,
                    dry_run=args.operation == "dry-run",
                    window_tokens=args.window_tokens,
                    overlap_tokens=args.overlap_tokens,
                )
                result["window_count"] = len(windows)
                if args.operation == "dry-run":
                    result["windows"] = windows
        finally:
            engine.dispose()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
    except SQLAlchemyError:
        print("Database operation failed; verify database state before retrying", file=sys.stderr)
        return 3
    return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
