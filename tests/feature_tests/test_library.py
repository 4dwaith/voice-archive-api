"""Book-answer journeys through the real application; only Gemini is mocked."""

import asyncio
import json

import httpx
import pytest
from google.genai import Client, types
from pydantic import SecretStr
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import delete, event, select, text, update
from starlette.concurrency import run_in_threadpool

from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.content import BookSegment
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.content_validation.validation import validate_content
from voice_archive_api.services.books.service import delete_book
from voice_archive_api.services.content.service import publish, purge
from voice_archive_api.services.windows import clear_book_windows, rebuild_book_windows

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
    settings = client.app.state.settings
    settings.gemini_embedding_dimensions = 2
    with client.app.state.session_factory.begin() as db:
        for segment in db.scalars(select(BookSegment).where(BookSegment.book_id == answer_case["book_id"])):
            segment.english_translation = segment.text_content
    rebuild_book_windows(client.app.state.session_factory, settings, answer_case["book_id"], len)
    with client.app.state.session_factory.begin() as db:
        db.execute(update(WindowEmbedding).values(vector=[1.0, 0.0]))
    answer_case["question"] = {"query_en": "Why did the traveller return home?"}
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
        if request.url.path.endswith(":batchEmbedContents"):
            if answer_case.get("clear_preparation"):
                await run_in_threadpool(clear_book_windows, client.app.state.session_factory, answer_case["book_id"])
            if answer_case["problem"] == "an embedding rejection":
                return httpx.Response(403, json={"error": {"message": "provider-secret"}})
            vector = [0.0, 0.0] if answer_case["problem"] == "an invalid embedding" else [1.0, 0.0]
            return httpx.Response(200, json={"embeddings": [{"values": vector}]})
        if request.url.path.endswith(":countTokens"):
            if answer_case.get("passage_budget"):
                payload = json.loads(request.content)["generateContentRequest"]
                passages = json.loads(payload["contents"][0]["parts"][0]["text"])["BOOK"]
                return httpx.Response(200, json={"totalTokens": len(passages) * 1000})
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
        if answer_case["change"] == "membership":
            db.execute(update(Book).where(Book.id == answer_case["book_id"]).values(series_id=None, series_index=None))
            db.commit()
        elif answer_case["change"] == "deleted":
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
    counted = next(
        json.loads(request.content)["generateContentRequest"]
        for request in answer_case["requests"]
        if request.url.path.endswith(":countTokens")
    )
    for payload in (counted, generated):
        config = types.GenerateContentConfig.model_validate(payload["generationConfig"])

        assert config.thinking_config.thinking_level == types.ThinkingLevel(answer_case["effort"].upper())


@when("I ask why the traveller returned home")
def ask_question(client, answer_case):
    question = {"book_id": answer_case["book_id"], "query": "Why did the traveller return home?"}
    if "effort" in answer_case:
        question["effort"] = answer_case["effort"]
    question.update(answer_case.get("question", {}))
    answer_case["body_reads"] = []
    answer_case["search_counts"] = []

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


@then("the answer service receives the question and the selected ordered passages")
def assert_selected_request(answer_case):
    counted_request = next(r for r in answer_case["requests"] if r.url.path.endswith(":countTokens"))
    counted = json.loads(counted_request.content)["generateContentRequest"]
    generated = json.loads(answer_case["requests"][-1].content)

    assert json.loads(counted_request.content)["contents"] == []
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
    expected = {
        "BOOK": [
            {"segment_id": answer_case["segments"][0]["id"], "path": ["Journey"], "text": "A traveller left home."},
            {
                "segment_id": answer_case["segments"][1]["id"],
                "path": ["Journey", "Travels", "Home"],
                "text": "ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು.",
            },
        ]
    }
    for passage, source in zip(expected["BOOK"], answer_case["segments"], strict=True):
        passage.update(
            book_id=answer_case["book_id"],
            content_version=answer_case["version"],
            section_id=source["section_id"],
            sequence=source["sequence"],
        )

    assert evidence == expected
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


@then("I am told the selected evidence is too large without submitting an answer")
def assert_oversize_answer(answer_case):
    assert answer_case["response"].json()["status"] == "oversize"
    assert not any(r.url.path.endswith(":generateContent") for r in answer_case["requests"])


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


@given("the journey book has prepared passage search")
def prepare_passage_search(client, answer_case):
    factory = client.app.state.session_factory
    settings = client.app.state.settings
    settings.gemini_embedding_dimensions = 2
    with factory.begin() as db:
        originals = db.scalars(
            select(BookSegment).where(BookSegment.book_id == answer_case["book_id"]).order_by(BookSegment.sequence)
        ).all()
        for segment in originals:
            segment.english_translation = segment.text_content
        for index in range(100):
            content = f"Other detail {index}: " + "x" * 45
            db.add(
                BookSegment(
                    book_id=answer_case["book_id"],
                    section_id=originals[-1].section_id,
                    source_key=f"other-{index}",
                    sequence=10 + index,
                    kind="prose",
                    html_content=f"<p>{content}</p>",
                    text_content=content,
                    english_translation=content,
                    regions=[],
                )
            )
    rebuild_book_windows(factory, settings, answer_case["book_id"], len, window_tokens=160, overlap_tokens=0)
    supporting_id = answer_case["segments"][1]["id"]
    with factory.begin() as db:
        for embedding, members in db.execute(
            select(WindowEmbedding, RetrievalWindow.segment_ids).join(RetrievalWindow)
        ):
            embedding.vector = [1.0, 0.0] if supporting_id in members else [0.0, 1.0]
        vector_oid = db.scalar(text("SELECT oid FROM pg_type WHERE typname = 'vector'"))
    answer_case["question"] = {"query_en": "Why did the traveller return home?"}
    answer_case["body_reads"] = []
    answer_case["search_counts"] = []

    def check_database_boundary(connection, cursor, statement, parameters, context, executemany):
        columns = cursor.description or []
        assert all(column[1] != vector_oid for column in columns), "Stored vectors must stay in PostgreSQL"
        names = {column[0] for column in columns}
        assert not names.intersection({"primary_text", "translated_text"}), "Search must not load window bodies"
        if "distance" in names:
            assert "<=>" in statement and "LIMIT" in statement
            answer_case["search_counts"].append(cursor.rowcount)
        if "text_content" in names:
            answer_case["body_reads"].append(cursor.rowcount)

    event.listen(client.app.state.engine, "after_cursor_execute", check_database_boundary)


@given("the journey book is declared English")
def declare_english_book(client, answer_case):
    response = client.patch(f"/api/v1/books/{answer_case['book_id']}", json={"language": "en-US"})
    assert response.status_code == 200, response.text
    with client.app.state.session_factory.begin() as db:
        db.execute(
            update(RetrievalWindow)
            .where(RetrievalWindow.book_id == answer_case["book_id"])
            .values(translated_text=None)
        )
    answer_case["english_book"] = True


@given("only lexical search can find the supporting passage")
def prepare_lexical_only_passage(client, answer_case):
    supporting_id = answer_case["segments"][1]["id"]
    original_ids = {segment["id"] for segment in answer_case["segments"]}
    with client.app.state.session_factory.begin() as db:
        source = "The traveller returned home to see his mother."
        db.execute(update(BookSegment).where(BookSegment.id == supporting_id).values(text_content=source))
        db.execute(
            update(RetrievalWindow)
            .where(RetrievalWindow.segment_ids.contains([supporting_id]))
            .values(primary_text=source)
        )
        db.execute(update(WindowEmbedding).values(vector=[1.0, 0.0]))
        originals = select(RetrievalWindow.id).where(RetrievalWindow.segment_ids.overlap(list(original_ids)))
        db.execute(update(WindowEmbedding).where(WindowEmbedding.window_id.in_(originals)).values(vector=[0.0, 1.0]))
    answer_case["question"]["lexical_terms"] = ["travellers returning home"]


@given("the question is in Kannada with English wording")
def prepare_kannada_question(answer_case):
    answer_case["question"]["query"] = "ಪ್ರಯಾಣಿಕ ಮನೆಗೆ ಹಿಂದಿರುಗಿದ್ದು ಏಕೆ?"


@given(parsers.parse("I ask across the {scope} with an unprepared member book"))
def prepare_scope(client, answer_case, scope):
    series = client.post("/api/v1/series", json={"title": "Travels"}).json()["id"]
    client.patch(f"/api/v1/books/{answer_case['book_id']}", json={"series_id": series})
    other = client.post(
        "/api/v1/books", json={"title": "Another journey", "language": "en", "series_id": series}
    ).json()["id"]
    client.post(f"/api/v1/books/{other}/content-imports", json=answer_case["payload"])
    answer_case["other_id"] = other
    answer_case["question"].update(book_id=None, series_id=series if scope == "series" else None)


@given("an unrelated prepared book is also in the library")
def prepare_unrelated_book(client, answer_case):
    other = client.post("/api/v1/books", json={"title": "Unrelated", "language": "en"}).json()["id"]
    client.post(f"/api/v1/books/{other}/content-imports", json=answer_case["payload"])
    factory, settings = client.app.state.session_factory, client.app.state.settings
    rebuild_book_windows(factory, settings, other, len)
    with factory.begin() as db:
        window_ids = select(RetrievalWindow.id).where(RetrievalWindow.book_id == other)
        db.execute(update(WindowEmbedding).where(WindowEmbedding.window_id.in_(window_ids)).values(vector=[1.0, 0.0]))


@given(parsers.parse("the prepared search is {state}"))
def change_search_preparation(client, answer_case, state):
    with client.app.state.session_factory.begin() as db:
        if state == "missing windows":
            clear_book_windows(client.app.state.session_factory, answer_case["book_id"])
        elif state == "missing embeddings":
            db.execute(delete(WindowEmbedding))
        elif state == "incomplete":
            db.execute(update(WindowEmbedding).where(WindowEmbedding.kind == "translated").values(vector=None))
        elif state == "incompatible":
            db.execute(update(WindowEmbedding).values(input_format="other-profile"))
        elif state == "zero magnitude":
            db.execute(update(WindowEmbedding).values(vector=[0.0, 0.0]))
    if state == "cleared while embedding the query":
        answer_case["clear_preparation"] = True


@then("only selected canonical passages are read and supplied")
def assert_bounded_retrieval(answer_case):
    generated = next(json.loads(r.content) for r in answer_case["requests"] if r.url.path.endswith(":generateContent"))
    passages = json.loads(generated["contents"][0]["parts"][0]["text"])["BOOK"]
    own = [p for p in passages if p["book_id"] == answer_case["book_id"]]
    embedded = [json.loads(r.content) for r in answer_case["requests"] if r.url.path.endswith(":batchEmbedContents")]
    questions = {answer_case["question"]["query_en"]}
    if not answer_case.get("english_book"):
        questions.add(answer_case["question"].get("query", "Why did the traveller return home?"))

    assert len(own) == 25
    assert own[0]["segment_id"] == answer_case["segments"][1]["id"]
    assert len({p["segment_id"] for p in passages}) == len(passages)
    assert all(
        set(p) == {"segment_id", "section_id", "sequence", "text", "path", "book_id", "content_version"}
        for p in passages
    )
    assert answer_case["body_reads"] == [len(passages)]
    assert all(count == 75 for count in answer_case["search_counts"])
    assert len(answer_case["search_counts"]) == (1 if answer_case.get("english_book") else 2)
    assert len(embedded) == len(questions)
    assert {e["requests"][0]["content"]["parts"][0]["text"] for e in embedded} == {
        f"task: search result | query: {q}" for q in questions
    }
    assert answer_case["response"].json()["status"] == answer_case["draft"]["status"]


@then("the unprepared member source is excluded")
def assert_unprepared_member_excluded(answer_case):
    generated = next(json.loads(r.content) for r in answer_case["requests"] if r.url.path.endswith(":generateContent"))
    passages = json.loads(generated["contents"][0]["parts"][0]["text"])["BOOK"]

    assert {p["book_id"] for p in passages} == {answer_case["book_id"]}
    assert all(p["book_id"] != answer_case["other_id"] for p in passages)
    assert answer_case["response"].json()["limitations"]


@then("no canonical source or answer generation is loaded")
def assert_no_answer_reads(answer_case):
    assert answer_case["body_reads"] == []
    assert not any(r.url.path.endswith(":generateContent") for r in answer_case["requests"])


@given("the English wording of the question is missing")
def remove_english_question(answer_case):
    answer_case["question"].pop("query_en")


@given("the scoped source exceeds its input budget")
def restrict_source_budget(client):
    client.app.state.settings.gemini_answer_input_tokens = 128


@given("the journey book leaves its series while the answer is prepared")
def prepare_membership_change(answer_case):
    answer_case["change"] = "membership"


@given("the answer cites a passage outside the selected evidence")
def prepare_unsupplied_reference(client, answer_case):
    with client.app.state.session_factory() as db:
        answer_case["draft"]["references"][0]["segment_id"] = db.scalar(
            select(BookSegment.id).order_by(BookSegment.sequence.desc()).limit(1)
        )


@given(parsers.parse("I select a {state} series"))
def prepare_empty_series(client, answer_case, state):
    series = client.post("/api/v1/series", json={"title": "Empty"}).json()["id"]
    if state == "removed":
        client.delete(f"/api/v1/series/{series}")
    answer_case["question"] = {"book_id": None, "series_id": series}


@then("no canonical source or provider request is loaded")
def assert_no_eager_reads(answer_case):
    assert answer_case["body_reads"] == []
    assert answer_case["requests"] == []


@then("the scoped source is reported too large")
def assert_scope_too_large(answer_case):
    assert answer_case["response"].json()["status"] == "oversize"


@given(parsers.parse("the answer model has a {budget} passage budget"))
def restrict_answer_model(client, answer_case, budget):
    answer_case["passage_budget"] = True
    client.app.state.settings.gemini_answer_input_tokens = 12200 if budget == "small" else 9000


@then("the answer uses fewer complete passages")
def assert_reduced_evidence(answer_case):
    generated = next(json.loads(r.content) for r in answer_case["requests"] if r.url.path.endswith(":generateContent"))
    passages = json.loads(generated["contents"][0]["parts"][0]["text"])["BOOK"]

    assert len(passages) == 3
    assert answer_case["body_reads"] == [25, 12, 6, 3]
    assert answer_case["response"].json()["status"] == "answered"


@given("I explicitly request whole-book answering")
def request_whole_book(answer_case, monkeypatch):
    answer_case["question"]["use_whole_book"] = True
    answer_case["question"].pop("query_en", None)

    def reject_dense_retrieval(*args, **kwargs):
        pytest.fail("Whole-book requests must bypass dense preparation and selection")

    monkeypatch.setattr("voice_archive_api.services.library.retrieval.read_source_availability", reject_dense_retrieval)
    monkeypatch.setattr("voice_archive_api.services.library.service.select_segment_ids", reject_dense_retrieval)


@given("the source read budget is too small")
def restrict_source_read_budget(client):
    client.app.state.settings.gemini_answer_input_tokens = 128


@then("the complete book is supplied without query embedding or vector search")
def assert_explicit_whole_book(answer_case):
    generated = next(json.loads(r.content) for r in answer_case["requests"] if r.url.path.endswith(":generateContent"))
    passages = json.loads(generated["contents"][0]["parts"][0]["text"])["BOOK"]

    assert len(passages) == 102
    assert [p["sequence"] for p in passages] == sorted(p["sequence"] for p in passages)
    assert {p["book_id"] for p in passages} == {answer_case["book_id"]}
    assert answer_case["body_reads"] == [102]
    assert answer_case["search_counts"] == []
    assert not any(r.url.path.endswith(":batchEmbedContents") for r in answer_case["requests"])
    assert answer_case["response"].json()["status"] == "answered"
