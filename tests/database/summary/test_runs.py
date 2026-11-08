"""Summary run admission, unchanged-input skipping and expired-run recovery."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from tests.summary_helpers import create_summary_drafts
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryBatchCheckpoint, SummaryGenerationRun
from voice_archive_api.logic.summaries import SummaryBatchResult, SummaryTarget
from voice_archive_api.services.summary import service as summaries


def test_rejects_generation_without_canonical_text(client):
    book_id = client.post("/api/v1/books", json={"title": "Empty", "language": "en"}).json()["id"]
    factory = client.app.state.session_factory

    with pytest.raises(ValueError, match="canonical"):
        summaries.start_summary_run(factory, book_id, "test-model")


@pytest.mark.parametrize("selection", ["missing", "foreign", "nested"])
def test_rejects_invalid_root_selection(client, summary_roots, selection):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    other_id = client.post("/api/v1/books", json={"title": "Other", "language": "en"}).json()["id"]
    response = client.post(
        f"/api/v1/books/{other_id}/content-imports",
        json={
            "schema_version": 1,
            "sections": [],
            "segments": [{"source_key": "text", "sequence": 0, "kind": "prose", "html_content": "<p>Other text</p>"}],
        },
    )
    assert response.status_code == 200
    with factory() as db:
        child_id = db.scalar(select(BookSection.id).where(BookSection.parent_id.is_not(None)))

    with pytest.raises(ValueError, match="Root section must belong"):
        summaries.start_summary_run(
            factory,
            other_id if selection == "foreign" else book_id,
            "test",
            root_section_id={"missing": 999999, "foreign": roots[0], "nested": child_id}[selection],
        )


def test_concurrent_starts_create_only_one_running_job(client, summary_book):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    ready = Barrier(2)

    def start_run():
        ready.wait(timeout=5)
        return summaries.start_summary_run(factory, book_id, "test-model")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: start_run(), range(2)))

    assert sum(isinstance(result, summaries.SummaryGenerationInput) for result in results) == 1
    assert results.count(version) == 1
    with factory() as db:
        assert len(db.scalars(select(SummaryGenerationRun)).all()) == 1


def test_new_invocation_replaces_four_hour_old_job_and_blocks_late_publication(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test-model")

    with factory.begin() as db:
        db.get(SummaryGenerationRun, original.run_id).started_at = datetime.now(UTC) - timedelta(hours=4, seconds=1)

    replacement = summaries.start_summary_run(factory, book_id, "test-model")

    assert isinstance(replacement, summaries.SummaryGenerationInput)
    assert replacement.run_id != original.run_id

    with pytest.raises(ValueError, match="run changed"):
        summaries.publish_book_summaries(factory, original, create_summary_drafts(original))

    summaries.publish_book_summaries(factory, replacement, create_summary_drafts(replacement))

    with factory() as db:
        failed = db.get(SummaryGenerationRun, original.run_id)
        assert failed.state == "failed"
        assert failed.failure_reason == "Run exceeded four hours"
        assert failed.finished_at is not None
        assert db.get(SummaryGenerationRun, replacement.run_id).state == "completed"


@pytest.mark.parametrize("change", ["text", "policy", "model", "effort"])
def test_changed_inputs_or_generation_policy_allow_a_new_run(client, summary_book, change):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test-model")
    summaries.publish_book_summaries(factory, original, create_summary_drafts(original))

    with factory.begin() as db:
        if change == "text":
            db.scalar(select(BookSegment).where(BookSegment.book_id == book_id)).text_content = "New source"
        elif change == "policy":
            db.get(SummaryGenerationRun, original.run_id).policy = "old-policy"

    replacement = summaries.start_summary_run(
        factory,
        book_id,
        "new-model" if change == "model" else "test-model",
        effort="high" if change == "effort" else "low",
    )

    assert isinstance(replacement, summaries.SummaryGenerationInput)
    assert replacement.run_id != original.run_id


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_unchanged_root_skips_generation(client, summary_roots, effort):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0], effort=effort)
    version = summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    result = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0], effort=effort)

    assert result == version
    with factory() as db:
        assert len(db.scalars(select(SummaryGenerationRun)).all()) == 1
        assert db.get(SummaryGenerationRun, generation_input.run_id).policy == (
            summaries.POLICY if effort == "low" else f"{summaries.POLICY};effort={effort}"
        )


@pytest.mark.parametrize("next_root_index", [None, 1], ids=["book", "another-root"])
def test_completed_root_does_not_skip_other_scopes(client, summary_roots, next_root_index):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[0])
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))
    next_root_id = roots[next_root_index] if next_root_index is not None else None

    result = summaries.start_summary_run(factory, book_id, "test", root_section_id=next_root_id)

    assert isinstance(result, summaries.SummaryGenerationInput)
    assert result.root_section_id == next_root_id
    assert result.run_id != generation_input.run_id


def test_completed_book_does_not_skip_root_generation(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test")
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    result = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[-1])

    assert isinstance(result, summaries.SummaryGenerationInput)
    assert result.root_section_id == roots[-1]


def test_root_replacement_requires_new_book_generation(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    book_input = summaries.start_summary_run(factory, book_id, "test")
    summaries.publish_book_summaries(factory, book_input, create_summary_drafts(book_input))
    root_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[-1])
    summaries.publish_book_summaries(factory, root_input, create_summary_drafts(root_input))

    result = summaries.start_summary_run(factory, book_id, "test")

    assert isinstance(result, summaries.SummaryGenerationInput)
    assert result.root_section_id is None
    assert result.run_id != book_input.run_id


def test_cleared_summaries_allow_regeneration(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test")
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))
    summaries.clear_book_summaries(factory, book_id)

    result = summaries.start_summary_run(factory, book_id, "test")

    assert isinstance(result, summaries.SummaryGenerationInput)
    assert result.run_id != generation_input.run_id


def test_changed_earlier_root_summary_allows_regeneration(client, summary_roots):
    book_id, roots = summary_roots
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.get(BookSection, roots[0]).summary = "Earlier root summary"

    generation_input = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1])
    summaries.publish_book_summaries(factory, generation_input, create_summary_drafts(generation_input))

    with factory.begin() as db:
        db.get(BookSection, roots[0]).summary = "Changed earlier root summary"

    result = summaries.start_summary_run(factory, book_id, "test", root_section_id=roots[1])

    assert isinstance(result, summaries.SummaryGenerationInput)
    assert result.run_id != generation_input.run_id
    assert result.input_fingerprint != generation_input.input_fingerprint


@pytest.mark.parametrize("change", ["unchanged", "source", "model", "policy", "effort", "scope", "override"])
def test_checkpoint_reuse_requires_matching_run_inputs(client, summary_book, monkeypatch, change):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    original = summaries.start_summary_run(factory, book_id, "test")
    batch = SummaryBatchResult(summaries=create_summary_drafts(original))
    fingerprint = "a" * 64
    summaries.save_summary_batch_checkpoint(
        factory, original.run_id, fingerprint, SummaryTarget(kind="book", id=book_id), 1, batch
    )
    summaries.fail_summary_run(factory, original.run_id)

    if change == "source":
        with factory.begin() as db:
            db.scalar(select(BookSegment).where(BookSegment.book_id == book_id)).text_content = "Changed source"
    if change == "policy":
        monkeypatch.setattr(summaries, "POLICY", "changed-policy")
    with factory() as db:
        root_id = db.scalar(select(BookSection.id).where(BookSection.book_id == book_id))

    replacement = summaries.start_summary_run(
        factory,
        book_id,
        "new-model" if change == "model" else "test",
        effort="high" if change == "effort" else "low",
        root_section_id=root_id if change == "scope" else None,
        allow_stale_downstream=change == "override",
    )
    checkpoints = summaries.read_summary_batch_checkpoints(factory, replacement.run_id)

    assert checkpoints == ({fingerprint: batch} if change == "unchanged" else {})


def test_clear_discards_checkpoints_and_rejects_late_batch_saves(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test")
    batch = SummaryBatchResult(summaries=create_summary_drafts(generation_input))
    target = SummaryTarget(kind="book", id=book_id)
    summaries.save_summary_batch_checkpoint(factory, generation_input.run_id, "a" * 64, target, 1, batch)

    summaries.clear_book_summaries(factory, book_id)

    with factory() as db:
        assert db.scalar(select(SummaryBatchCheckpoint)) is None

    with pytest.raises(ValueError, match="run changed"):
        summaries.save_summary_batch_checkpoint(factory, generation_input.run_id, "b" * 64, target, 2, batch)

    replacement = summaries.start_summary_run(factory, book_id, "test")

    assert summaries.read_summary_batch_checkpoints(factory, replacement.run_id) == {}
