"""Book-answer journeys through the real application; only Gemini is mocked."""

import asyncio
import json

import httpx
import pytest
from google.genai import Client, types
from pydantic import SecretStr
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import delete, text
from starlette.concurrency import run_in_threadpool

from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.logic.content_validation.validation import validate_content
from voice_archive_api.services.books.service import delete_book
from voice_archive_api.services.content.service import publish, purge

scenarios("../features/library")


@pytest.fixture
def answer_case():
    return {"requests": [], "limit": 1_048_576, "tokens": 1000, "problem": None}


@given("my library contains a published book about a journey")
def create_journey_book(client, answer_case):
    response = client.post("/api/v1/books", json={"title": "Journey", "language": "kn"})
    assert response.status_code == 201, response.text
    answer_case["book_id"] = response.json()["id"]
    payload = {
        "schema_version": 1,
        "sections": [
            {"source_key": "part", "kind": "part", "title": "Travels", "sibling_sequence": 0},
            {
                "source_key": "home",
                "parent_source_key": "part",
                "kind": "chapter",
                "title": "Home",
                "sibling_sequence": 0,
            },
        ],
        "segments": [
            {
                "source_key": "return",
                "sequence": 9,
                "kind": "prose",
                "section_source_key": "home",
                "html_content": "<p>ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು.</p>",
            },
            {"source_key": "before", "sequence": 5, "kind": "prose", "html_content": "<p>A traveller left home.</p>"},
        ],
    }
    response = client.post(f"/api/v1/books/{answer_case['book_id']}/content-imports", json=payload)
    assert response.status_code == 200, response.text
    version = response.json()["content_version"]
    response = client.get(f"/api/v1/books/{answer_case['book_id']}/segments", headers={"If-Content-Version": version})
    assert response.status_code == 200, response.text
    answer_case.update(payload=payload, version=version, segments=response.json()["items"])
    answer_case["draft"] = {
        "status": "answered",
        "answer": "He returned to see his mother.",
        "limitations": [],
        "references": [
            {
                "segment_id": answer_case["segments"][1]["id"],
                "relevance": "Explains his reason for returning.",
            }
        ],
    }


@given("the answer service is available")
def mock_answer_provider(client, answer_case, monkeypatch):
    client.app.state.settings.gemini_api_key = SecretStr("provider-secret")

    async def respond(request):
        answer_case["requests"].append(request)
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": answer_case["tokens"]})
        if not request.url.path.endswith(":generateContent"):
            return httpx.Response(200, json={"inputTokenLimit": answer_case["limit"]})
        problem = answer_case["problem"]
        if problem == "a network failure":
            raise httpx.ConnectError("provider-secret")
        if problem == "a timeout":
            await asyncio.Event().wait()
        if problem == "a provider rejection":
            return httpx.Response(403, json={"error": {"message": "provider-secret"}})
        if answer_case.get("change"):
            await run_in_threadpool(change_source, client, answer_case)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": {"a blocked response": "SAFETY", "a truncated response": "MAX_TOKENS"}.get(
                            problem, "STOP"
                        ),
                        "content": {
                            "parts": [
                                {
                                    "text": "invalid"
                                    if problem == "malformed output"
                                    else json.dumps(answer_case["draft"])
                                }
                            ]
                        },
                    }
                ]
            },
        )

    def create_test_client(*, api_key, http_options):
        assert http_options.retry_options.attempts == 1

        http_options.async_client_args = {"transport": httpx.MockTransport(respond)}
        return Client(api_key=api_key, http_options=http_options)

    monkeypatch.setattr("voice_archive_api.services.library.provider.genai.Client", create_test_client)


def change_source(client, answer_case):
    # A second real PostgreSQL session must acquire the writer lock while the
    # Gemini request is pending. A leaked reader lock fails promptly, not hangs.
    with client.app.state.session_factory() as db:
        db.execute(text("SET lock_timeout = '500ms'"))
        if answer_case["change"] == "deleted":
            assert delete_book(db, answer_case["book_id"])
        else:
            purge(
                db,
                answer_case["book_id"],
                f'"book-content-v1-{answer_case["book_id"]}-content-{answer_case["version"]}"',
            )
            if answer_case["change"] == "replaced":
                publish(
                    db,
                    answer_case["book_id"],
                    validate_content(json.dumps(answer_case["payload"]).encode()),
                    dry_run=False,
                )
    answer_case["changed"] = True


@given("the answer service finds no evidence for the question")
def prepare_absent_answer(answer_case):
    answer_case["draft"] = {
        "status": "insufficient_evidence",
        "answer": "The book does not say.",
        "limitations": ["No supporting passage."],
        "references": [],
    }


@given(parsers.parse("the answer service encounters {problem}"))
def prepare_provider_failure(client, answer_case, problem):
    answer_case["problem"] = problem
    references = answer_case["draft"]["references"]
    if problem == "a timeout":
        client.app.state.settings.gemini_answer_timeout_seconds = 0.01
    elif problem == "an unknown passage":
        references[0]["segment_id"] = 999999
    elif problem == "a mismatched passage reference":
        references[0]["segment_id"] = answer_case["segments"][1]["sequence"]
    elif problem == "a passage from another book":
        other = client.post("/api/v1/books", json={"title": "Other", "language": "en"}).json()["id"]
        imported = client.post(f"/api/v1/books/{other}/content-imports", json=answer_case["payload"])
        version = imported.json()["content_version"]
        segments = client.get(f"/api/v1/books/{other}/segments", headers={"If-Content-Version": version}).json()[
            "items"
        ]
        references[0]["segment_id"] = segments[0]["id"]
    elif problem == "repeated passages":
        references.append(dict(references[0]))
    elif problem == "an answer without support":
        references.clear()
    elif problem == "an empty answer":
        answer_case["draft"]["answer"] = " "
    elif problem == "an unexplained passage":
        references[0]["relevance"] = " "
    elif problem == "missing model limits":
        answer_case["limit"] = None
    elif problem == "missing token count":
        answer_case["tokens"] = None


@given(parsers.parse("the complete request exceeds the {limit} input limit"))
def prepare_oversize(client, answer_case, limit):
    if limit == "provider":
        answer_case["limit"] = 999
    else:
        client.app.state.settings.gemini_answer_input_tokens = 999


@given(parsers.parse("the selected book is {state}"))
def remove_source(client, answer_case, state):
    if state == "published without text":
        with client.app.state.session_factory.begin() as db:
            db.execute(delete(BookSegment).where(BookSegment.book_id == answer_case["book_id"]))
    else:
        answer_case["change"] = "deleted" if state == "removed" else "purged"
        change_source(client, answer_case)
        answer_case.pop("change")


@given(parsers.parse("the book is {change} while the answer is being prepared"))
def prepare_source_change(answer_case, change):
    answer_case["change"] = change


@given("the answer service has no credential")
def clear_credential(client):
    client.app.state.settings.gemini_api_key = None
    client.app.state.settings.gemini_batch_api_key = SecretStr("batch-only-secret")


@given(parsers.parse("I want {effort} reasoning effort"))
def set_reasoning_effort(answer_case, effort):
    answer_case["effort"] = effort


@then("the answer service uses the requested reasoning effort")
def assert_reasoning_effort(answer_case):
    generated = json.loads(answer_case["requests"][-1].content)
    counted = json.loads(answer_case["requests"][1].content)["generateContentRequest"]
    for payload in (counted, generated):
        config = types.GenerateContentConfig.model_validate(payload["generationConfig"])

        assert config.thinking_config.thinking_level == types.ThinkingLevel(answer_case["effort"].upper())


@when("I ask why the traveller returned home")
def ask_question(client, answer_case):
    question = {"book_id": answer_case["book_id"], "query": "Why did the traveller return home?"}
    if "effort" in answer_case:
        question["effort"] = answer_case["effort"]

    answer_case["response"] = client.post("/api/v1/library/ask", json=question)


@then("the answer explains the return with its supporting passage")
def assert_grounded_answer(answer_case):
    response = answer_case["response"]
    result = response.json()

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert result["status"] == "answered"
    assert result["answer"] == answer_case["draft"]["answer"]
    assert result["limitations"] == []

    reference = result["references"][0]

    assert reference == {
        "book_id": answer_case["book_id"],
        "content_version": answer_case["version"],
        "segment_id": answer_case["segments"][1]["id"],
        "section_id": answer_case["segments"][1]["section_id"],
        "sequence": 9,
        "path": ["Journey", "Travels", "Home"],
        "text": "ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು.",
        "relevance": answer_case["draft"]["references"][0]["relevance"],
    }


@then("the answer service receives the question and the complete ordered book")
def assert_complete_request(answer_case):
    requests = answer_case["requests"]
    counted = json.loads(requests[1].content)["generateContentRequest"]
    generated = json.loads(requests[2].content)

    assert json.loads(requests[1].content)["contents"] == []
    assert counted.pop("model") == "models/gemini-3.8-flash"

    # Protobuf accepts both naming styles; the SDK emits thinking_level here.
    for payload in (counted, generated):
        payload["generationConfig"] = types.GenerateContentConfig.model_validate(
            payload["generationConfig"]
        ).model_dump(mode="json", by_alias=True, exclude_none=True)

    assert counted == generated
    assert generated["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}

    evidence = json.loads(generated["contents"][0]["parts"][0]["text"])
    question = json.loads(generated["contents"][0]["parts"][1]["text"])

    assert question == {"QUESTION": "Why did the traveller return home?"}
    assert evidence == {
        "BOOK": [
            {"segment_id": answer_case["segments"][0]["id"], "path": ["Journey"], "text": "A traveller left home."},
            {
                "segment_id": answer_case["segments"][1]["id"],
                "path": ["Journey", "Travels", "Home"],
                "text": "ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು.",
            },
        ]
    }
    assert generated["generationConfig"]["responseJsonSchema"]["required"] == [
        "status",
        "answer",
        "limitations",
        "references",
    ]


@then("I am told that the book does not establish an answer")
def assert_absent_answer(answer_case):
    assert answer_case["response"].json() == answer_case["draft"]


@then("I am told that an answer is temporarily unavailable")
def assert_unavailable_answer(answer_case):
    result = answer_case["response"].json()
    assert result["status"] == "unavailable"
    assert result["answer"] == ""
    assert result["references"] == []
    assert result["limitations"]
    assert "provider-secret" not in answer_case["response"].text


@then("I am told the complete book is too large without submitting a partial book")
def assert_oversize_answer(answer_case):
    assert answer_case["response"].json()["status"] == "oversize"
    assert len(answer_case["requests"]) == 2


@then(parsers.parse("I am told the selected source is {availability}"))
def assert_missing_source(answer_case, availability):
    response = answer_case["response"]
    if availability == "missing":
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "book_not_found"
    else:
        assert response.json()["status"] == "unavailable"
    assert answer_case["requests"] == []


@then("the source change completes and I am told to ask again")
def assert_changed_source(answer_case):
    assert answer_case["changed"]
    result = answer_case["response"].json()
    assert result["status"] == "stale"
    assert result["answer"] == ""
    assert result["references"] == []
