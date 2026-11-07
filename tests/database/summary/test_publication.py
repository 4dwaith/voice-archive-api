"""Atomic summary writes, selected output boundaries, clearing and content versions."""

import pytest
from sqlalchemy import event, select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryGenerationRun
from voice_archive_api.services.summary import service as summaries


def test_clear_changes_the_version_once_and_removes_every_summary(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    cleared = summaries.clear_book_summaries(factory, book_id)

    assert cleared != version
    assert summaries.clear_book_summaries(factory, book_id) == cleared
    with factory() as db:
        assert db.get(SummaryGenerationRun, generation_input.run_id).state == "completed"
        assert all(
            row.summary is None for table in (Book, BookSection, BookSegment) for row in db.scalars(select(table))
        )
        assert db.scalar(select(BookSegment.english_translation)) is None


def test_incomplete_publication_retains_the_complete_accepted_set(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))
    replacement = summaries.start_summary_run(factory, book_id, "replacement-model")

    with pytest.raises(ValueError, match="exactly once"):
        summaries.publish_book_summaries(factory, replacement, create_summary_drafts(replacement)[:-1])

    with factory() as db:
        assert db.get(Book, book_id).content_version == version
        assert all(row.summary for row in db.scalars(select(BookSegment)))


def test_database_failure_rolls_back_partly_written_replacement(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test-model")
    version = summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))
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
        assert db.get(SummaryGenerationRun, generation_input.run_id).state == "completed"
        assert db.get(SummaryGenerationRun, replacement.run_id).state == "running"
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


def test_clear_without_canonical_content_is_a_no_op(client):
    book_id = client.post("/api/v1/books", json={"title": "Empty", "language": "en"}).json()["id"]
    factory = client.app.state.session_factory

    result = summaries.clear_book_summaries(factory, book_id)

    assert result is None
    with factory() as db:
        assert db.get(Book, book_id).content_version is None


def test_root_publication_updates_only_its_subtree_and_changes_the_version(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    with factory.begin() as db:
        book = db.get(Book, book_id)
        original_version = book.content_version
        book.summary = "Existing book summary"
        db.get(BookSection, roots[0]).summary = "Existing earlier root summary"
        earlier_passage = db.scalar(select(BookSegment).where(BookSegment.section_id == roots[0]))
        earlier_passage.summary = "Existing earlier passage summary"
        earlier_passage.english_translation = "Existing earlier passage translation"

    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1])

    version = summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    assert version != original_version
    with factory() as db:
        book = db.get(Book, book_id)
        assert book.content_version == version
        assert book.summary == "Existing book summary"
        assert db.get(BookSection, roots[0]).summary == "Existing earlier root summary"
        earlier_passage = db.scalar(select(BookSegment).where(BookSegment.section_id == roots[0]))
        assert earlier_passage.summary == "Existing earlier passage summary"
        assert earlier_passage.english_translation == "Existing earlier passage translation"

        sections = db.scalars(
            select(BookSection)
            .where((BookSection.id == roots[1]) | (BookSection.parent_id == roots[1]))
            .order_by(BookSection.sequence)
        ).all()
        assert len(sections) == 2
        assert all(row.summary == "The section describes a return home." for row in sections)
        child_passage = db.scalar(select(BookSegment).where(BookSegment.section_id == sections[-1].id))
        assert child_passage.summary == "The segment describes a return home."
        assert child_passage.english_translation == "He came home to see his mother."

        assert db.get(BookSection, roots[2]).summary is None
        later_passage = db.scalar(select(BookSegment).where(BookSegment.section_id == roots[2]))
        assert later_passage.summary is None
        assert later_passage.english_translation is None
        run = db.get(SummaryGenerationRun, generation_input.run_id)
        assert (run.root_section_id, run.state) == (roots[1], "completed")
