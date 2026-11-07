"""Summary CLI-to-API journeys with real PostgreSQL and mocked Gemini HTTP only."""

import json
from uuid import UUID

import httpx
import pytest
from google.genai import Client, types
from pydantic import SecretStr
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import select, text

from voice_archive_api.cli import summaries as cli
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSection, BookSegment
from voice_archive_api.db.models.summary import SummaryGenerationRun
from voice_archive_api.logic.summaries import SUMMARY_INSTRUCTIONS, GeneratedSummary

scenarios("../features/summary")


@pytest.fixture(name="summary_case")
def create_summary_case(summary_book):
    book_id, version = summary_book
    return {
        "book_id": book_id,
        "version": version,
        "accepted": None,
        "requests": [],
        "provider_requests": [],
        "counted_configs": [],
        "generated_configs": [],
        "reject": False,
        "omit_translation": False,
        "translate_passages": True,
    }


def read_summary_state(client, book_id):
    state = []
    for suffix in ("", "/content"):
        response = client.get(f"/api/v1/books/{book_id}{suffix}")
        assert response.status_code == 200, response.text
        state.append(response.json())

    for collection in ("sections", "segments"):
        response = client.get(
            f"/api/v1/books/{book_id}/{collection}",
            headers={"If-Content-Version": state[1]["content_version"]},
            params={"limit": 1},
        )
        assert response.status_code == 200, response.text
        state.append(response.json())

    return state


@given("a published Kannada book is ready for summarization")
def prepare_summary_book(client, summary_case):
    summary_case["before"] = read_summary_state(client, summary_case["book_id"])


@given("the book declares the English alias eng")
def prepare_english_book(client, summary_case):
    with client.app.state.session_factory.begin() as db:
        db.get(Book, summary_case["book_id"]).language = "eng"
        passage = db.scalar(select(BookSegment).where(BookSegment.book_id == summary_case["book_id"]))
        passage.text_content = "He came home to see his mother."
        passage.html_content = "<p>He came home to see his mother.</p>"

    summary_case["translate_passages"] = False


@given(parsers.parse('the book declares the unresolved language "{language}"'))
def prepare_unresolved_language(client, summary_case, language):
    response = client.patch(f"/api/v1/books/{summary_case['book_id']}", json={"language": language})
    assert response.status_code == 200
    summary_case["before"] = read_summary_state(client, summary_case["book_id"])


@given("the summary provider is available")
def mock_summary_provider(client, summary_case, monkeypatch):
    client.app.state.settings.gemini_batch_api_key = SecretStr("provider-batch-secret")
    client.app.state.settings.gemini_api_key = SecretStr("provider-interactive-secret")
    factory = client.app.state.session_factory

    async def respond(request):
        summary_case["provider_requests"].append(request.url.path)
        if request.url.path.endswith(":countTokens"):
            payload = json.loads(request.content)["generateContentRequest"]
            summary_case["counted_configs"].append(
                types.GenerateContentConfig.model_validate(payload["generationConfig"]).model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            )
            return httpx.Response(200, json={"totalTokens": 1000})
        if not request.url.path.endswith(":generateContent"):
            return httpx.Response(200, json={"inputTokenLimit": 1_048_576, "outputTokenLimit": 65_536})
        payload = json.loads(request.content)
        summary_case["generated_configs"].append(
            types.GenerateContentConfig.model_validate(payload["generationConfig"]).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        )
        parts = [part["text"] for part in payload["contents"][0]["parts"]]
        with factory() as db:
            # An independent writer must acquire the book lock during provider work.
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            book = db.get(Book, summary_case["book_id"], with_for_update=True)
            assert book.content_version == summary_case["version"]
            assert book.summary == summary_case["accepted"]
        summary_case["requests"].append(json.loads(parts[0]))
        assert payload["systemInstruction"]["parts"][0]["text"] == SUMMARY_INSTRUCTIONS
        assert "summary" not in json.loads(parts[0])["SOURCE"]
        if summary_case["reject"]:
            return httpx.Response(403, json={"error": {"message": "provider-batch-secret"}})
        result = {
            "summaries": [
                GeneratedSummary(
                    kind=target["kind"],
                    id=target["id"],
                    language="en",
                    summary="He returned home to see his mother.",
                    english_translation=(
                        "He came home to see his mother."
                        if target["kind"] == "segment"
                        and summary_case["translate_passages"]
                        and not summary_case["omit_translation"]
                        else None
                    ),
                ).model_dump()
                for target in json.loads(parts[1])["TARGETS"]
            ]
        }
        return httpx.Response(
            200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]}
        )

    def create_test_client(*, api_key, http_options):
        assert api_key == "provider-batch-secret"
        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.library.provider.genai.Client", create_test_client)
    monkeypatch.setitem(cli.__dict__, "Settings", lambda: client.app.state.settings)


@when("I generate summaries with the local command")
def generate_summaries(summary_case, capsys):
    summary_case["exit_code"] = cli.run(["generate", "--book-id", str(summary_case["book_id"])])
    summary_case["output"] = capsys.readouterr()


@then("English summaries are available for the book, sections and passages")
def verify_published_summaries(client, summary_case):
    assert summary_case["exit_code"] == 0
    version = UUID(json.loads(summary_case["output"].out)["content_version"])
    assert version != summary_case["version"]
    state = read_summary_state(client, summary_case["book_id"])
    assert state[0]["summary"] == state[1]["summary"] == "He returned home to see his mother."
    for collection in state[2:]:
        assert collection["content_version"] == str(version)
        assert [row["summary"] for row in collection["items"]] == ["He returned home to see his mother."]
    with client.app.state.session_factory() as db:
        translation = "He came home to see his mother." if summary_case["translate_passages"] else None
        assert db.scalar(select(BookSegment.english_translation)) == translation


@then("the provider receives resolved English and provenance keeps the declared alias")
def verify_resolved_english_language(client, summary_case):
    assert all(request["SOURCE"]["language"] == "en" for request in summary_case["requests"])
    with client.app.state.session_factory() as db:
        assert db.get(Book, summary_case["book_id"]).language == "eng"
        assert db.scalar(select(SummaryGenerationRun.source_language)) == "eng"


@then("the command requests a primary language before any provider work or run is created")
def verify_unresolved_language_is_rejected(client, summary_case):
    assert summary_case["exit_code"] == 1
    assert "single primary book language" in summary_case["output"].err
    assert not summary_case["output"].out
    assert summary_case["provider_requests"] == []
    assert summary_case["requests"] == summary_case["counted_configs"] == summary_case["generated_configs"] == []
    assert read_summary_state(client, summary_case["book_id"]) == summary_case["before"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(SummaryGenerationRun.id)) is None


@then("the previous content version is rejected by paginated reads")
def verify_previous_version_is_rejected(client, summary_case):
    for path in ("sections", "segments"):
        response = client.get(
            f"/api/v1/books/{summary_case['book_id']}/{path}",
            headers={"If-Content-Version": str(summary_case["version"])},
            params={"limit": 1},
        )
        assert response.status_code == 412


@then("generation uses the complete canonical source outside publication locks")
def verify_generation_context(summary_case):
    assert len(summary_case["requests"]) == len(summary_case["output"].err.splitlines()) == 2
    assert summary_case["counted_configs"] == summary_case["generated_configs"]
    assert [config["maxOutputTokens"] for config in summary_case["generated_configs"]] == [65_536, 65_536]
    assert all(
        request["SOURCE"]["fragments"][1]["text"] == "ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು." for request in summary_case["requests"]
    )


@given("summaries have already been generated")
def prepare_accepted_summaries(client, summary_case, capsys):
    generate_summaries(summary_case, capsys)
    verify_published_summaries(client, summary_case)
    summary_case["version"] = UUID(json.loads(summary_case["output"].out)["content_version"])
    summary_case["accepted"] = "He returned home to see his mother."
    summary_case["before"] = read_summary_state(client, summary_case["book_id"])
    summary_case["request_count"] = len(summary_case["requests"])


@then("the command skips generation and preserves the published version")
def verify_unchanged_run_is_skipped(client, summary_case):
    assert summary_case["exit_code"] == 0
    assert "skipped" in summary_case["output"].err
    assert UUID(json.loads(summary_case["output"].out)["content_version"]) == summary_case["version"]
    assert len(summary_case["requests"]) == summary_case["request_count"]
    assert read_summary_state(client, summary_case["book_id"]) == summary_case["before"]
    with client.app.state.session_factory() as db:
        runs = db.scalars(
            select(SummaryGenerationRun).where(SummaryGenerationRun.book_id == summary_case["book_id"])
        ).all()
        assert len(runs) == 1
        assert runs[0].state == "completed"
        assert runs[0].finished_at is not None


@when("I clear summaries with the local command")
def clear_summaries(summary_case, capsys):
    summary_case["exit_code"] = cli.run(["clear", "--book-id", str(summary_case["book_id"])])
    summary_case["output"] = capsys.readouterr()


@then("all summary reads are empty under a new content version")
def verify_cleared_summaries(client, summary_case):
    assert summary_case["exit_code"] == 0
    version = UUID(json.loads(summary_case["output"].out)["content_version"])
    assert version != summary_case["version"]
    state = read_summary_state(client, summary_case["book_id"])
    assert state[0]["summary"] is state[1]["summary"] is None
    for collection in state[2:]:
        assert collection["content_version"] == str(version)
        assert all(row["summary"] is None for row in collection["items"])
    assert len(summary_case["requests"]) == summary_case["request_count"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(BookSegment.english_translation)) is None


@given("the book title changes before a rejected replacement")
def reject_replacement_generation(client, summary_case):
    response = client.patch(f"/api/v1/books/{summary_case['book_id']}", json={"title": "A changed journey"})
    assert response.status_code == 200
    summary_case["before"] = read_summary_state(client, summary_case["book_id"])
    summary_case["version"] = UUID(summary_case["before"][1]["content_version"])
    summary_case["reject"] = True


@given("the book title changes before a replacement without its passage translation")
def omit_replacement_translation(client, summary_case):
    reject_replacement_generation(client, summary_case)
    summary_case["reject"] = False
    summary_case["omit_translation"] = True


@then("the command reports failure and preserves the accepted summary reads")
def verify_failed_replacement(client, summary_case):
    assert summary_case["exit_code"] == 1
    assert not summary_case["output"].out
    assert "generation failed" in summary_case["output"].err
    assert "provider-secret" not in summary_case["output"].err
    assert len(summary_case["requests"]) > summary_case["request_count"]
    assert read_summary_state(client, summary_case["book_id"]) == summary_case["before"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(BookSegment.english_translation)) == "He came home to see his mother."


@given("the book has three root sections")
def prepare_three_roots(client, summary_case, summary_roots):
    book_id, roots = summary_roots
    summary_case["roots"] = roots
    with client.app.state.session_factory() as db:
        summary_case["version"] = db.get(Book, book_id).content_version


@when("I generate each root section in order with the local command")
def generate_roots_in_order(summary_case, capsys):
    summary_case["versions"] = [summary_case["version"]]
    for root_id in summary_case["roots"]:
        assert cli.run(["generate", "--book-id", str(summary_case["book_id"]), "--root-section-id", str(root_id)]) == 0
        output = capsys.readouterr()
        summary_case["version"] = UUID(json.loads(output.out)["content_version"])
        summary_case["versions"].append(summary_case["version"])


@then("each root is published under its own version with earlier context")
def verify_sequential_roots(client, summary_case):
    requests = summary_case["requests"]

    assert len(set(summary_case["versions"])) == 4
    assert len(requests) == 3
    for index, request in enumerate(requests):
        assert request["SOURCE"]["fragments"][0]["id"] == summary_case["roots"][index]
        assert len(request["BACKGROUND"]["previous_roots"]) == index
    assert requests[1]["BACKGROUND"]["previous_root_text"] == "first\n\nfirst"
    assert requests[2]["BACKGROUND"]["previous_root_text"] == "second\n\nChild\n\nsecond"
    with client.app.state.session_factory() as db:
        assert db.get(Book, summary_case["book_id"]).summary is None
        assert all(row.summary == "He returned home to see his mother." for row in db.scalars(select(BookSection)))
        assert all(
            row.english_translation == "He came home to see his mother." for row in db.scalars(select(BookSegment))
        )
    assert (
        read_summary_state(client, summary_case["book_id"])[2]["items"][0]["summary"]
        == "He returned home to see his mother."
    )


@given("the last root already has a summary")
def prepare_later_summary(client, summary_case):
    with client.app.state.session_factory.begin() as db:
        db.get(BookSection, summary_case["roots"][-1]).summary = "Existing later summary"


def generate_selected_root(summary_case, capsys, index, *, allow=False):
    arguments = [
        "generate",
        "--book-id",
        str(summary_case["book_id"]),
        "--root-section-id",
        str(summary_case["roots"][index]),
    ]
    if allow:
        arguments.append("--allow-stale-downstream")
    summary_case["exit_code"] = cli.run(arguments)
    summary_case["output"] = capsys.readouterr()


@when("I generate the second root section with the local command")
def generate_second_root(summary_case, capsys):
    generate_selected_root(summary_case, capsys, 1)


@when("I generate the first root section with the local command")
def generate_first_root(summary_case, capsys):
    generate_selected_root(summary_case, capsys, 0)


@when("I generate the first root section allowing stale downstream summaries")
def generate_first_root_with_override(summary_case, capsys):
    generate_selected_root(summary_case, capsys, 0, allow=True)


@then("the command reports missing earlier roots without contacting the provider")
def verify_missing_earlier_roots(client, summary_case):
    assert summary_case["exit_code"] == 1
    assert "Missing earlier root summaries" in summary_case["output"].err
    assert summary_case["provider_requests"] == []
    with client.app.state.session_factory() as db:
        assert db.scalar(select(SummaryGenerationRun.id)) is None


@then("the command reports later summaries without contacting the provider")
def verify_later_summaries_block_generation(client, summary_case):
    assert summary_case["exit_code"] == 1
    assert "--allow-stale-downstream" in summary_case["output"].err
    assert summary_case["provider_requests"] == []
    with client.app.state.session_factory() as db:
        assert db.scalar(select(SummaryGenerationRun.id)) is None


@then("the first root is published and the later summary is preserved")
def verify_override_preserves_downstream(client, summary_case):
    assert summary_case["exit_code"] == 0
    assert "Allowing potentially stale downstream summaries" in summary_case["output"].err
    assert UUID(json.loads(summary_case["output"].out)["content_version"]) != summary_case["version"]
    with client.app.state.session_factory() as db:
        assert db.get(BookSection, summary_case["roots"][0]).summary == "He returned home to see his mother."
        assert db.get(BookSection, summary_case["roots"][-1]).summary == "Existing later summary"
