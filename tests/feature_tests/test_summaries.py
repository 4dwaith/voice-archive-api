"""Summary CLI-to-API journeys with real PostgreSQL and mocked Gemini HTTP only."""

import json
from uuid import UUID

import httpx
import pytest
from google.genai import Client, types
from pydantic import SecretStr
from pytest_bdd import given, scenarios, then, when
from sqlalchemy import select, text

from voice_archive_api.cli import summaries as cli
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.summary import SummaryRun
from voice_archive_api.logic.summaries import SUMMARY_INSTRUCTIONS, SummaryDraft

scenarios("../features/summary")


@pytest.fixture(name="summary_case")
def create_summary_case(summary_book):
    book_id, version = summary_book
    return {
        "book_id": book_id,
        "version": version,
        "accepted": None,
        "requests": [],
        "counted_configs": [],
        "generated_configs": [],
        "reject": False,
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


@given("the summary provider is available")
def mock_summary_provider(client, summary_case, monkeypatch):
    client.app.state.settings.gemini_api_key = SecretStr("provider-secret")
    factory = client.app.state.session_factory

    async def respond(request):
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
        assert "summary" not in parts[0]
        if summary_case["reject"]:
            return httpx.Response(403, json={"error": {"message": "provider-secret"}})
        result = {
            "summaries": [
                SummaryDraft(
                    kind=target["kind"], id=target["id"], language="en", summary="He returned home to see his mother."
                ).model_dump()
                for target in json.loads(parts[1])["TARGETS"]
            ]
        }
        return httpx.Response(
            200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(result)}]}}]}
        )

    def create_test_client(*, api_key, http_options):
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
        runs = db.scalars(select(SummaryRun).where(SummaryRun.book_id == summary_case["book_id"])).all()
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


@given("the book title changes before a rejected replacement")
def reject_replacement_generation(client, summary_case):
    response = client.patch(f"/api/v1/books/{summary_case['book_id']}", json={"title": "A changed journey"})
    assert response.status_code == 200
    summary_case["before"] = read_summary_state(client, summary_case["book_id"])
    summary_case["version"] = UUID(summary_case["before"][1]["content_version"])
    summary_case["reject"] = True


@then("the command reports failure and preserves the accepted summary reads")
def verify_failed_replacement(client, summary_case):
    assert summary_case["exit_code"] == 1
    assert not summary_case["output"].out
    assert "generation failed" in summary_case["output"].err
    assert "provider-secret" not in summary_case["output"].err
    assert len(summary_case["requests"]) > summary_case["request_count"]
    assert read_summary_state(client, summary_case["book_id"]) == summary_case["before"]
