from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from voice_archive_api import maintenance
from voice_archive_api.services.files.cleanup import CleanupResult


def test_cleanup_schedule_runs_on_startup_and_at_midnight_utc():
    now = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
    temporary_job, promoted_job, orphan_job = Mock(), Mock(), Mock()
    scheduler = maintenance.build_scheduler(temporary_job, promoted_job, orphan_job, now=now)
    assert len(scheduler.get_jobs()) == 3
    for job_id, function in (
        ("temporary-file-cleanup", temporary_job),
        ("promoted-file-cleanup", promoted_job),
    ):
        job = scheduler.get_job(job_id)
        assert job.func is function
        assert job.next_run_time == now
        assert job.trigger.get_next_fire_time(None, now) == datetime(2026, 10, 22, tzinfo=UTC)
        assert job.max_instances == 1
        assert job.coalesce


@pytest.mark.parametrize(
    "temporary_error,promoted_error,failures,exit_code",
    [
        (None, None, 0, 0),
        (OSError("temporary directory busy"), None, 0, 1),
        (None, RuntimeError("database unavailable"), 0, 1),
        (None, None, 1, 1),
        (OSError("temporary directory busy"), RuntimeError("database unavailable"), 0, 1),
    ],
)
def test_one_off_cleanup_attempts_all_jobs_and_disposes_engine(
    monkeypatch,
    temporary_error,
    promoted_error,
    failures,
    exit_code,
):
    engine = Mock()
    temporary_cleanup = Mock(return_value=0, side_effect=temporary_error)
    promoted_cleanup = Mock(return_value=CleanupResult(failures=failures), side_effect=promoted_error)
    orphan_cleanup = Mock(return_value=CleanupResult())
    monkeypatch.setattr("sys.argv", ["maintenance", "--once"])
    monkeypatch.setitem(maintenance.__dict__, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setitem(maintenance.__dict__, "run_temporary_cleanup", temporary_cleanup)
    monkeypatch.setitem(maintenance.__dict__, "run_promoted_cleanup", promoted_cleanup)
    monkeypatch.setitem(maintenance.__dict__, "run_orphan_cleanup", orphan_cleanup)
    assert maintenance.main() == exit_code
    temporary_cleanup.assert_called_once()
    promoted_cleanup.assert_called_once()
    orphan_cleanup.assert_called_once()
    engine.dispose.assert_called_once_with()


def test_scheduler_interrupt_shuts_down_and_disposes_engine(monkeypatch):
    engine = Mock()
    scheduler = Mock(running=True)
    scheduler.start.side_effect = KeyboardInterrupt
    monkeypatch.setattr("sys.argv", ["maintenance"])
    monkeypatch.setitem(maintenance.__dict__, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setitem(maintenance.__dict__, "build_scheduler", Mock(return_value=scheduler))
    assert maintenance.main() == 0
    scheduler.shutdown.assert_called_once_with(wait=True)
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("job", ["temporary", "promoted", "orphan"])
def test_cli_selects_only_the_requested_job(monkeypatch, job):
    engine = Mock()
    create_engine = Mock(return_value=engine)
    temporary_cleanup = Mock(return_value=0)
    promoted_cleanup = Mock(return_value=CleanupResult())
    orphan_cleanup = Mock(return_value=CleanupResult())
    monkeypatch.setattr("sys.argv", ["maintenance", "--once", "--job", job])
    monkeypatch.setitem(maintenance.__dict__, "create_database_engine", create_engine)
    monkeypatch.setitem(maintenance.__dict__, "run_temporary_cleanup", temporary_cleanup)
    monkeypatch.setitem(maintenance.__dict__, "run_promoted_cleanup", promoted_cleanup)
    monkeypatch.setitem(maintenance.__dict__, "run_orphan_cleanup", orphan_cleanup)
    assert maintenance.main() == 0
    if job == "temporary":
        temporary_cleanup.assert_called_once()
        promoted_cleanup.assert_not_called()
        create_engine.assert_not_called()
        orphan_cleanup.assert_not_called()
    elif job == "promoted":
        promoted_cleanup.assert_called_once()
        temporary_cleanup.assert_not_called()
        engine.dispose.assert_called_once_with()
        orphan_cleanup.assert_not_called()
    else:
        orphan_cleanup.assert_called_once()
        temporary_cleanup.assert_not_called()
        promoted_cleanup.assert_not_called()
        engine.dispose.assert_called_once_with()


def test_three_jobs_have_independent_schedules():
    jobs = [Mock(), Mock(), Mock()]
    scheduler = maintenance.build_scheduler(*jobs)
    assert {job.func for job in scheduler.get_jobs()} == set(jobs)
    assert len(scheduler.get_jobs()) == 3


@pytest.mark.parametrize(
    "job,failure",
    [
        ("temporary", "exception"),
        ("promoted", "exception"),
        ("orphan", "exception"),
        ("promoted", "result"),
        ("orphan", "result"),
    ],
)
def test_job_failure_sets_exit_status_without_skipping_other_jobs(job, failure):
    jobs = {
        "temporary": Mock(return_value=0),
        "promoted": Mock(return_value=CleanupResult()),
        "orphan": Mock(return_value=CleanupResult()),
    }
    if failure == "exception":
        jobs[job].side_effect = OSError("cleanup unavailable")
    else:
        jobs[job].return_value = CleanupResult(failures=1)
    assert maintenance.run_in_sequence(jobs["temporary"], jobs["promoted"], jobs["orphan"]) == 1
    for cleanup_job in jobs.values():
        cleanup_job.assert_called_once_with()
