"""Run temporary, prepared-file, and orphan-file cleanup."""

import argparse
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from types import FrameType

from apscheduler.schedulers.blocking import BlockingScheduler

from voice_archive_api.core.config import Settings
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.file_handling.cleanup import run_temporary_cleanup
from voice_archive_api.services.files.cleanup import CleanupResult, run_orphan_cleanup, run_promoted_cleanup

logger = logging.getLogger(__name__)


def build_scheduler(
    temporary_job: Callable[[], object] | None,
    promoted_job: Callable[[], object] | None,
    orphan_job: Callable[[], object] | None,
    *,
    now: datetime | None = None,
) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)
    first_run = now or datetime.now(UTC)
    for job_id, job in (
        ("temporary-file-cleanup", temporary_job),
        ("promoted-file-cleanup", promoted_job),
        ("orphan-file-cleanup", orphan_job),
    ):
        if job is None:
            continue
        scheduler.add_job(
            job,
            "cron",
            hour=0,
            minute=0,
            id=job_id,
            next_run_time=first_run,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=None,
        )
    return scheduler


def run_in_sequence(
    temporary_job: Callable[[], int] | None,
    promoted_job: Callable[[], CleanupResult] | None,
    orphan_job: Callable[[], CleanupResult] | None,
) -> int:
    """Attempt each selected job even if another fails; return its combined exit status."""
    failed = False
    if temporary_job is not None:
        try:
            temporary_job()
        except Exception:
            failed = True
            logger.exception("Temporary-file cleanup failed")
    if promoted_job is not None:
        try:
            if promoted_job().failures:
                failed = True
        except Exception:
            failed = True
            logger.exception("Promoted-file cleanup failed")
    if orphan_job is not None:
        try:
            if orphan_job().failures:
                failed = True
        except Exception:
            failed = True
            logger.exception("Orphan-file cleanup failed")
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run cleanup once and exit (suitable for external cron)")
    parser.add_argument(
        "--job",
        choices=("temporary", "promoted", "orphan", "all"),
        default="all",
        help="Select cleanup jobs (default: all)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    engine = create_database_engine(settings.database_url) if args.job != "temporary" else None
    try:
        storage = LocalStorage(settings.upload_root)
        factory = create_session_factory(engine) if engine is not None else None
        temporary_job = partial(run_temporary_cleanup, storage) if args.job in {"temporary", "all"} else None
        promoted_job = (
            partial(run_promoted_cleanup, factory, storage)
            if factory is not None and args.job in {"promoted", "all"}
            else None
        )
        orphan_job = (
            partial(run_orphan_cleanup, factory, storage)
            if factory is not None and args.job in {"orphan", "all"}
            else None
        )
        if args.once:
            return run_in_sequence(temporary_job, promoted_job, orphan_job)
        scheduler = build_scheduler(temporary_job, promoted_job, orphan_job)

        def stop(_signal: int | None = None, _frame: FrameType | None = None) -> None:
            if scheduler.running:
                scheduler.shutdown(wait=True)

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            scheduler.start()
        except KeyboardInterrupt:
            stop()
        finally:
            signal.signal(signal.SIGTERM, previous)
        return 0
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
