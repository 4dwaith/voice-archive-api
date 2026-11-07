"""Summary prerequisites, downstream guards and dependency changes during generation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryGenerationRun
from voice_archive_api.services.books.schemas import BookPatch
from voice_archive_api.services.books.service import patch_book
from voice_archive_api.services.content.service import purge
from voice_archive_api.services.series import service as series
from voice_archive_api.services.summary import service as summaries


@pytest.mark.parametrize("change", ["membership", "previous_summary"])
def test_rejects_changed_background_dependencies(client, summary_series, change):
    series_id, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, current_id, "test-model")

    if change == "membership":
        with factory() as db:
            series.replace_series_books(db, series_id, [current_id, earlier_id])
    else:
        summaries.clear_book_summaries(factory, earlier_id)

    with pytest.raises(ValueError, match="changed"):
        summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory() as db:
        assert db.get(Book, current_id).summary is None


@pytest.mark.parametrize("change", ["clear", "language", "title", "purge", "text", "heading"])
def test_rejects_late_publication_after_dependencies_change(client, summary_book, change):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test-model")

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
        summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory() as db:
        assert db.get(Book, book_id).summary is None
        assert all(row.summary is None for row in db.scalars(select(BookSegment)))


@pytest.mark.parametrize("allow", [False, True])
def test_missing_previous_roots_cannot_be_bypassed(client, summary_roots, allow):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory

    with pytest.raises(ValueError, match=f"Missing earlier root summaries: {roots[0]}"):
        summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1], allow_stale_downstream=allow)

    with factory() as db:
        assert db.scalar(select(SummaryGenerationRun.id)) is None


@pytest.mark.parametrize("scope", ["book", "root"])
def test_missing_previous_books_cannot_be_bypassed(client, summary_series, scope):
    _, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    summaries.clear_book_summaries(factory, earlier_id)
    with factory() as db:
        root_id = db.scalar(
            select(BookSection.id).where(BookSection.book_id == current_id).order_by(BookSection.sequence)
        )

    with pytest.raises(ValueError, match=f"Missing earlier book summaries: {earlier_id}"):
        summaries.start_summary_run(
            factory,
            current_id,
            "test",
            root_section_id=root_id if scope == "root" else None,
            allow_stale_downstream=True,
        )


@pytest.mark.parametrize("kind", ["section", "segment"])
def test_later_root_summaries_block_generation_and_bypass_preserves_them(client, summary_roots, kind):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    with factory.begin() as db:
        row = (
            db.get(BookSection, roots[-1])
            if kind == "section"
            else db.scalar(select(BookSegment).where(BookSegment.section_id == roots[-1]))
        )
        row.summary = "Existing downstream summary"

    with pytest.raises(ValueError, match=f"root section {roots[-1]}.*--allow-stale-downstream"):
        summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0])

    generation_input = summaries.start_summary_run(
        factory, book_id, "test", root_section_id=roots[0], allow_stale_downstream=True
    )
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory() as db:
        assert db.get(SummaryGenerationRun, generation_input.run_id).allow_stale_downstream is True
        row = (
            db.get(BookSection, roots[-1])
            if kind == "section"
            else db.scalar(select(BookSegment).where(BookSegment.section_id == roots[-1]))
        )
        assert row.summary == "Existing downstream summary"

    with pytest.raises(ValueError, match="Later summaries exist"):
        summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0])


@pytest.mark.parametrize("kind", ["book", "section", "segment"])
@pytest.mark.parametrize("scope", ["book", "root"])
def test_later_books_block_both_scopes_even_with_partial_summaries(client, summary_series, kind, scope):
    _, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    with factory.begin() as db:
        table = {"book": Book, "section": BookSection, "segment": BookSegment}[kind]
        row = (
            db.get(Book, current_id)
            if kind == "book"
            else db.scalar(select(table).where(table.book_id == current_id).order_by(table.id))
        )
        row.summary = "Existing downstream summary"
        root_id = db.scalar(select(BookSection.id).where(BookSection.book_id == earlier_id))
    options = {"root_section_id": root_id} if scope == "root" else {}

    with pytest.raises(ValueError, match=f"book {current_id}.*--allow-stale-downstream"):
        summaries.start_summary_run(factory, earlier_id, "replacement", **options)

    generation_input = summaries.start_summary_run(
        factory, earlier_id, "replacement", allow_stale_downstream=True, **options
    )
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory() as db:
        row = (
            db.get(Book, current_id)
            if kind == "book"
            else db.scalar(select(table).where(table.book_id == current_id).order_by(table.id))
        )
        assert row.summary == "Existing downstream summary"


def test_later_book_published_during_generation_blocks_earlier_publication(client, summary_series):
    _, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    earlier = summaries.start_summary_run(factory, earlier_id, "replacement")
    later = summaries.start_summary_run(factory, current_id, "test")
    summaries.publish_book_summaries(factory, later, create_summary_drafts(later))

    with pytest.raises(ValueError, match="Later summaries exist"):
        summaries.publish_book_summaries(factory, earlier, create_summary_drafts(earlier))

    with factory() as db:
        assert db.get(SummaryGenerationRun, earlier.run_id).state == "running"
        assert db.get(Book, earlier_id).summary == "The book describes a return home."


def test_changed_earlier_root_summary_rejects_publication(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.get(BookSection, roots[0]).summary = "Earlier root summary"

    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1])

    with factory.begin() as db:
        db.get(BookSection, roots[0]).summary = "Changed earlier root summary"

    with pytest.raises(ValueError, match="background changed"):
        summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))


def test_previous_root_text_is_stored_as_text_and_rechecked_at_publication(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.get(BookSection, roots[0]).summary = "Earlier root summary"

    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1])

    assert generation_input.previous_root_text == "first\n\nfirst"

    with factory.begin() as db:
        db.scalar(
            select(BookSegment).where(BookSegment.section_id == roots[0])
        ).text_content = "Changed earlier passage"

    with pytest.raises(ValueError, match="background changed"):
        summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))


def test_new_later_root_blocks_publication_without_partial_writes(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0])
    with factory.begin() as db:
        db.get(BookSection, roots[-1]).summary = "Published while generation was running"
        version = db.get(Book, book_id).content_version

    with pytest.raises(ValueError, match="Later summaries exist"):
        summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory() as db:
        assert db.get(BookSection, roots[0]).summary is None
        assert db.get(Book, book_id).content_version == version


def test_concurrent_book_publications_cannot_accept_stale_background(client, summary_series):
    _, earlier_id, current_id = summary_series
    factory = client.app.state.session_factory
    earlier = summaries.start_summary_run(factory, earlier_id, "replacement")
    later = summaries.start_summary_run(factory, current_id, "test")
    ready = Barrier(2)

    def publish_summaries(generation_input):
        drafts = create_summary_drafts(generation_input)
        for draft in drafts:
            if draft.kind == "book":
                draft.summary = "Changed book background"

        ready.wait(timeout=5)
        try:
            summaries.publish_book_summaries(factory, generation_input, drafts)
            return "completed"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(publish_summaries, [earlier, later]))

    assert outcomes.count("completed") == 1
    assert any("Later summaries exist" in outcome or "background changed" in outcome for outcome in outcomes)
