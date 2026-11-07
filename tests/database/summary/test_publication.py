"""PostgreSQL admission, dependency checks and atomic summary publication."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryRun
from voice_archive_api.services.books.schemas import BookPatch
from voice_archive_api.services.books.service import patch_book
from voice_archive_api.services.content.service import purge
from voice_archive_api.services.series import service as series
from voice_archive_api.services.summary import service as summaries


@pytest.mark.parametrize("change", ["membership", "previous_summary"])
def test_rejects_changed_background_dependencies(client, summary_series, change):
    series_id, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, current_id, "test-model")

    if change == "membership":
        with factory() as db:
            series.replace_series_books(db, series_id, [current_id, earlier_id])
    else:
        summaries.clear_book_summaries(factory, earlier_id)

    with pytest.raises(ValueError, match="changed"):
        summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))

    with factory() as db:
        assert db.get(Book, current_id).summary is None


@pytest.mark.parametrize("change", ["clear", "language", "title", "purge", "text", "heading"])
def test_rejects_late_publication_after_dependencies_change(client, summary_book, change):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, book_id, "test-model")

    if change == "clear":
        assert summaries.clear_book_summaries(factory, book_id) == version
    elif change in {"text", "heading"}:
        with factory.begin() as db:
            if change == "text":
                db.scalar(select(BookSegment).where(BookSegment.book_id == book_id)).text_content = "Changed source"
            else:
                db.scalar(select(BookSection).where(BookSection.book_id == book_id)).title = "Changed heading"
    else:
        with factory() as db:
            if change == "purge":
                purge(db, book_id, f'"book-content-v1-{book_id}-content-{version}"')
            else:
                patch_book(db, book_id, BookPatch(**{change: "en" if change == "language" else "New title"}))

    with pytest.raises(ValueError, match="changed"):
        summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))

    with factory() as db:
        assert db.get(Book, book_id).summary is None
        assert all(row.summary is None for row in db.scalars(select(BookSegment)))


def test_clear_changes_the_version_once_and_removes_every_summary(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))

    cleared = summaries.clear_book_summaries(factory, book_id)

    assert cleared != version
    assert summaries.clear_book_summaries(factory, book_id) == cleared
    with factory() as db:
        assert db.get(SummaryRun, snapshot.run_id).state == "completed"
        assert all(
            row.summary is None for table in (Book, BookSection, BookSegment) for row in db.scalars(select(table))
        )
        assert db.scalar(select(BookSegment.english_translation)) is None

    assert isinstance(summaries.start_summary_run(factory, book_id, "test-model"), summaries.SummarySnapshot)


def test_incomplete_publication_retains_the_complete_accepted_set(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))
    replacement = summaries.start_summary_run(factory, book_id, "replacement-model")

    with pytest.raises(ValueError, match="exactly once"):
        summaries.publish_book_summaries(factory, replacement, create_summary_drafts(replacement)[:-1])

    with factory() as db:
        assert db.get(Book, book_id).content_version == version
        assert all(row.summary for row in db.scalars(select(BookSegment)))


def test_database_failure_rolls_back_partly_written_replacement(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))
    replacement = summaries.start_summary_run(factory, book_id, "replacement-model")
    drafts = [
        draft.model_copy(
            update={
                "summary": "A replacement description.",
                "english_translation": "A replacement translation." if draft.kind == "segment" else None,
            }
        )
        for draft in create_summary_drafts(replacement)
    ]

    def fail_segment_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE segments"):
            raise RuntimeError("Publication interrupted after section writes")

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", fail_segment_update)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            summaries.publish_book_summaries(factory, replacement, drafts)
    finally:
        event.remove(engine, "before_cursor_execute", fail_segment_update)

    with factory() as db:
        assert db.get(Book, book_id).content_version == version
        assert db.get(SummaryRun, snapshot.run_id).state == "completed"
        assert db.get(SummaryRun, replacement.run_id).state == "running"
        assert [row.summary for row in db.scalars(select(BookSection))] == ["The section describes a return home."]
        assert [row.summary for row in db.scalars(select(BookSegment))] == ["The segment describes a return home."]
        assert db.scalar(select(BookSegment.english_translation)) == "He came home to see his mother."


def test_clear_removes_a_translation_even_without_summaries(client, summary_book):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.scalar(select(BookSegment)).english_translation = "He came home to see his mother."

    cleared = summaries.clear_book_summaries(factory, book_id)

    assert cleared != version
    with factory() as db:
        assert db.scalar(select(BookSegment.english_translation)) is None


def test_rejects_generation_without_canonical_text(client):
    book_id = client.post("/api/v1/books", json={"title": "Empty", "language": "en"}).json()["id"]
    factory = client.app.state.session_factory
    previous = summaries.clear_book_summaries(factory, book_id)

    with pytest.raises(ValueError, match="canonical"):
        summaries.start_summary_run(factory, book_id, "test-model")

    assert summaries.clear_book_summaries(factory, book_id) == previous


def test_concurrent_starts_create_only_one_running_job(client, summary_book):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    ready = Barrier(2)

    def start_run():
        ready.wait(timeout=5)
        return summaries.start_summary_run(factory, book_id, "test-model")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: start_run(), range(2)))

    assert sum(isinstance(result, summaries.SummarySnapshot) for result in results) == 1
    assert results.count(version) == 1
    with factory() as db:
        assert len(db.scalars(select(SummaryRun)).all()) == 1


def test_new_invocation_replaces_four_hour_old_job_and_blocks_late_publication(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test-model")
    with factory.begin() as db:
        db.get(SummaryRun, original.run_id).started_at = datetime.now(UTC) - timedelta(hours=4, seconds=1)

    replacement = summaries.start_summary_run(factory, book_id, "test-model")

    assert isinstance(replacement, summaries.SummarySnapshot)
    assert replacement.run_id != original.run_id
    with pytest.raises(ValueError, match="run changed"):
        summaries.publish_book_summaries(factory, original, create_summary_drafts(original))

    summaries.publish_book_summaries(factory, replacement, create_summary_drafts(replacement))

    with factory() as db:
        failed = db.get(SummaryRun, original.run_id)
        assert failed.state == "failed"
        assert failed.failure_reason == "Run exceeded four hours"
        assert failed.finished_at is not None
        assert db.get(SummaryRun, replacement.run_id).state == "completed"


@pytest.mark.parametrize("change", ["text", "policy", "model"])
def test_changed_inputs_or_generation_policy_allow_a_new_run(client, summary_book, change):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test-model")
    summaries.publish_book_summaries(factory, original, create_summary_drafts(original))
    with factory.begin() as db:
        if change == "text":
            db.scalar(select(BookSegment).where(BookSegment.book_id == book_id)).text_content = "New source"
        elif change == "policy":
            db.get(SummaryRun, original.run_id).policy = "old-policy"

    replacement = summaries.start_summary_run(factory, book_id, "new-model" if change == "model" else "test-model")

    assert isinstance(replacement, summaries.SummarySnapshot)
    assert replacement.run_id != original.run_id
