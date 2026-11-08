"""Run file cleanup and collect embedding batch results."""

import argparse
import logging
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from types import FrameType

from apscheduler.schedulers.blocking import BlockingScheduler

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.session import create_database_engine, create_session_factory
from voice_archive_api.file_handling import LocalStorage
from voice_archive_api.file_handling.cleanup import run_temporary_cleanup
from voice_archive_api.services.embeddings.service import run_embedding_maintenance
from voice_archive_api.services.files.cleanup import CleanupResult, run_orphan_cleanup, run_promoted_cleanup

logger = logging.getLogger(__name__)


def build_scheduler(
    temporary_job: Callable[[], object] | None,
    promoted_job: Callable[[], object] | None,
    orphan_job: Callable[[], object] | None,
    *,
    now: datetime | None = None,
    embedding_job: Callable[[], int] | None = None,
    embedding_interval_seconds: int = 180,
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
    if embedding_job is not None:
        scheduler.add_job(
            embedding_job,
            "interval",
            seconds=embedding_interval_seconds,
            id="embedding-batch-collection",
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
    embedding_job: Callable[[], int] | None = None,
) -> int:
    """Attempt each selected job even if another fails; return its combined exit status."""
    failed = False
    if temporary_job is not None:
        try:
            temporary_job()
        except Exception as exc:
            failed = True
            log_exception(logger, "Temporary-file cleanup failed", exc)
    if promoted_job is not None:
        try:
            if promoted_job().failures:
                failed = True
        except Exception as exc:
            failed = True
            log_exception(logger, "Promoted-file cleanup failed", exc)
    if orphan_job is not None:
        try:
            if orphan_job().failures:
                failed = True
        except Exception as exc:
            failed = True
            log_exception(logger, "Orphan-file cleanup failed", exc)
    if embedding_job is not None:
        try:
            if embedding_job():
                failed = True
        except Exception as exc:
            failed = True
            log_exception(logger, "Embedding batch collection failed", exc)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run selected jobs once and exit")
    parser.add_argument(
        "--job",
        choices=("temporary", "promoted", "orphan", "embeddings", "all"),
        default="all",
        help="Select maintenance jobs (default: all)",
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
        embedding_job = (
            partial(run_embedding_maintenance, factory, settings)
            if factory is not None and args.job in {"embeddings", "all"}
            else None
        )
        if args.once:
            return run_in_sequence(temporary_job, promoted_job, orphan_job, embedding_job)
        scheduler = build_scheduler(
            temporary_job,
            promoted_job,
            orphan_job,
            embedding_job=partial(run_in_sequence, None, None, None, embedding_job) if embedding_job else None,
            embedding_interval_seconds=settings.embedding_collection_interval_seconds,
        )

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
