"""Persist response usage independently of generation success or publication."""

import pytest
from google.genai import types
from sqlalchemy import text

from alembic import command
from voice_archive_api.db.models.summary import SummaryGenerationRun
from voice_archive_api.services.summary import service as summaries


def test_accumulates_usage_even_after_run_failure(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test", effort="high")
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100,
        cached_content_token_count=80,
        candidates_token_count=20,
        thoughts_token_count=50,
        total_token_count=170,
    )

    summaries.record_summary_token_usage(factory, generation_input.run_id, usage)
    summaries.fail_summary_run(factory, generation_input.run_id)
    summaries.record_summary_token_usage(factory, generation_input.run_id, usage)

    with factory() as db:
        run = db.get(SummaryGenerationRun, generation_input.run_id)

        assert run.state == "failed"
        assert (run.input_tokens, run.cached_input_tokens) == (200, 160)
        assert (run.output_tokens, run.thinking_tokens, run.total_tokens) == (40, 100, 340)
        assert (run.generation_response_count, run.missing_usage_response_count) == (2, 0)


def test_flags_missing_usage_without_inventing_token_counts(client, summary_book):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test")

    summaries.record_summary_token_usage(factory, generation_input.run_id, None)
    summaries.record_summary_token_usage(
        factory, generation_input.run_id, types.GenerateContentResponseUsageMetadata(prompt_token_count=100)
    )

    with factory() as db:
        run = db.get(SummaryGenerationRun, generation_input.run_id)

        assert run.input_tokens == 100
        assert (run.cached_input_tokens, run.output_tokens, run.thinking_tokens, run.total_tokens) == (0, 0, 0, 0)
        assert (run.generation_response_count, run.missing_usage_response_count) == (2, 2)


@pytest.mark.parametrize("starting_revision", ["0018_summary_scopes", "0019_summary_token_usage"])
def test_usage_migration_backfills_missing_counts_and_preserves_recorded_usage(
    client, summary_book, migration_config, starting_revision
):
    book_id, _ = summary_book
    factory = client.app.state.session_factory
    generation_input = summaries.start_summary_run(factory, book_id, "test")

    command.downgrade(migration_config, starting_revision)
    if starting_revision == "0019_summary_token_usage":
        with factory.begin() as db:
            db.execute(
                text("""UPDATE book_summary_runs
                    SET input_tokens = NULL, cached_input_tokens = NULL, output_tokens = 23,
                        thinking_tokens = NULL, total_tokens = NULL,
                        generation_response_count = NULL, missing_usage_response_count = NULL
                    WHERE id = :run_id"""),
                {"run_id": generation_input.run_id},
            )

    command.upgrade(migration_config, "head")

    with factory() as db:
        run = db.get(SummaryGenerationRun, generation_input.run_id)

        assert run.book_id == book_id
        assert run.state == "running"
        assert run.input_tokens == 0
        assert run.cached_input_tokens == 0
        assert run.output_tokens == (23 if starting_revision == "0019_summary_token_usage" else 0)
        assert run.thinking_tokens == 0
        assert run.total_tokens == 0
        assert run.generation_response_count == 0
        assert run.missing_usage_response_count == 0
