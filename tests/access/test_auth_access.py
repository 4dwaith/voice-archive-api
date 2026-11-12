from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy.orm import Session

from tests.database.content_helpers import add_audio, add_section
from voice_archive_api.api.v1.auth import COOKIE_NAME
from voice_archive_api.core.passwords import create_password_hash
from voice_archive_api.db.models.book import Book
from voice_archive_api.db.models.file import StoredFile
from voice_archive_api.db.models.user import User
from voice_archive_api.services.books.schemas import BookCreate
from voice_archive_api.services.books.service import create_book
from voice_archive_api.services.library.search import retrieval
from voice_archive_api.services.series.schemas import SeriesCreate
from voice_archive_api.services.series.service import create_series


@pytest.fixture
def secure_client(application, test_engine, file_storage):
    application.state.settings.auth_secret = SecretStr("isolated-auth-test-secret-with-32-characters")
    application.state.settings.auth_cookie_secure = False
    with Session(test_engine) as db:
        users = [
            User(email=f"{name}@example.com", password_hash=create_password_hash("test-password"), is_enabled=True)
            for name in ("a", "b")
        ]
        db.add_all(users)
        db.flush()
        ids = {
            "users": {name: user.id for name, user in zip(("a", "b"), users, strict=True)},
            "books": {},
            "series": {},
            "files": {},
        }
        db.commit()
        for name, owner in (("a", users[0].id), ("b", users[1].id), ("public", None)):
            series = create_series(db, SeriesCreate(title=name))
            book = create_book(db, BookCreate(title=name, language="en", series_id=series.id))
            section = add_section(db, book.id)
            key = file_storage.promote(file_storage.put_temporary(BytesIO(b"prepared audio bytes"), max_bytes=1000))
            audio = add_audio(db, section_id=section.id, uploaded_by_user_id=users[0].id, storage_key=key)
            series.owner_user_id = book.owner_user_id = owner
            db.commit()
            ids["books"][name], ids["series"][name], ids["files"][name] = book.id, series.id, audio.id
    with TestClient(application, headers={"Origin": "http://localhost:5173"}) as client:
        yield client, ids


def sign_in(client, name="a"):
    response = client.post("/api/v1/auth/login", json={"email": f"{name}@EXAMPLE.COM", "password": "test-password"})

    assert response.status_code == 200, response.text
    return response


def test_login_current_user_and_logout(secure_client):
    client, ids = secure_client
    assert client.get("/api/v1/auth/me").json() is None

    response = sign_in(client)

    assert response.json() == {"id": ids["users"]["a"], "email": "a@example.com", "display_name": None}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api" in cookie
    assert client.get("/api/v1/auth/me").json() == response.json()

    assert client.post("/api/v1/auth/logout").status_code == 204

    assert client.get("/api/v1/auth/me").json() is None


@pytest.mark.parametrize("actor,expected", [(None, ["public"]), ("a", ["a", "public"]), ("b", ["b", "public"])])
def test_catalogs_filter_items_and_totals(secure_client, actor, expected):
    client, ids = secure_client
    if actor:
        sign_in(client, actor)

    for resource, title_key in (("books", "title"), ("series", "title")):
        result = client.get(f"/api/v1/{resource}?limit=1").json()

        assert result["total"] == len(expected)
        assert result["items"][0][title_key] in expected
        assert {item["id"] for item in client.get(f"/api/v1/{resource}").json()["items"]} == {
            ids[resource][name] for name in expected
        }


@pytest.mark.parametrize(
    "resource,suffix",
    [
        ("books", ""),
        ("books", "/content"),
        ("books", "/sections"),
        ("books", "/segments"),
        ("series", ""),
        ("files", ""),
    ],
)
@pytest.mark.parametrize("actor", [None, "b"])
def test_private_resource_ids_are_hidden(secure_client, resource, suffix, actor):
    client, ids = secure_client
    if actor:
        sign_in(client, actor)

    response = client.get(
        f"/api/v1/{resource}/{ids[resource]['a']}{suffix}", headers={"If-Content-Version": str(uuid4())}
    )

    assert response.status_code == 404


@pytest.mark.parametrize("actor,expected", [(None, 401), ("a", 404)])
def test_public_books_are_read_only(secure_client, actor, expected):
    client, ids = secure_client
    if actor:
        sign_in(client, actor)

    response = client.patch(f"/api/v1/books/{ids['books']['public']}", json={"title": "Changed"})

    assert response.status_code == expected


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_foreign_book_writes_match_missing_book_errors(secure_client, method):
    client, ids = secure_client
    sign_in(client, "b")

    foreign = client.request(method, f"/api/v1/books/{ids['books']['a']}", json={})
    missing = client.request(method, "/api/v1/books/9223372036854775807", json={})

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_owner_creates_and_edits_own_books_but_cannot_link_foreign_series(secure_client, test_engine):
    client, ids = secure_client
    sign_in(client)

    response = client.post("/api/v1/books", json={"title": "New", "language": "en", "series_id": ids["series"]["a"]})

    assert response.status_code == 201
    book_id = response.json()["id"]
    with Session(test_engine) as db:
        assert db.get(Book, book_id).owner_user_id == ids["users"]["a"]

    assert client.patch(f"/api/v1/books/{book_id}", json={"title": "Updated"}).status_code == 200
    assert client.patch(f"/api/v1/books/{book_id}", json={"series_id": ids["series"]["b"]}).status_code == 404
    assert client.put(f"/api/v1/series/{ids['series']['a']}/books", json=[ids["books"]["b"]]).status_code == 404


def test_linked_media_supports_range_and_head(secure_client):
    client, ids = secure_client
    sign_in(client)

    response = client.get(f"/api/v1/files/{ids['files']['a']}", headers={"Range": "bytes=0-7"})

    assert response.status_code == 206
    assert response.content == b"prepared"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.head(f"/api/v1/files/{ids['files']['a']}").status_code == 200


def test_uploader_owns_unlinked_files_and_linked_access_follows_book(secure_client, test_engine):
    client, ids = secure_client
    sign_in(client)
    image = BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")

    uploaded = client.post("/api/v1/files/cover", files={"file": ("cover.png", image.getvalue(), "image/png")})

    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    with Session(test_engine) as db:
        assert db.get(StoredFile, file_id).uploaded_by_user_id == ids["users"]["a"]

    sign_in(client, "b")

    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    assert client.delete(f"/api/v1/files/{file_id}").status_code == 404
    assert (
        client.post(
            "/api/v1/books", json={"title": "Cannot claim", "language": "en", "cover_file_id": file_id}
        ).status_code
        == 404
    )

    sign_in(client)
    linked = client.post("/api/v1/books", json={"title": "Linked", "language": "en", "cover_file_id": file_id})

    assert linked.status_code == 201, linked.text
    with Session(test_engine) as db:
        db.get(Book, linked.json()["id"]).owner_user_id = ids["users"]["b"]
        db.commit()

    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    sign_in(client, "b")
    assert client.get(f"/api/v1/files/{file_id}").status_code == 200

    with Session(test_engine) as db:
        db.get(StoredFile, file_id).book_id = None
        db.commit()

    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    sign_in(client)
    assert client.delete(f"/api/v1/files/{file_id}").status_code == 204


@pytest.mark.parametrize("failure", ["expired", "signature", "disabled", "missing-user"])
def test_invalid_sessions_never_fall_back_to_guest(secure_client, application, test_engine, failure):
    client, ids = secure_client
    now = datetime.now(UTC)
    claims = {
        "sub": str(ids["users"]["a"]),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "iss": "voice-archive",
        "aud": "voice-archive",
    }
    secret = application.state.settings.auth_secret.get_secret_value()
    if failure == "expired":
        claims["iat"], claims["exp"] = now - timedelta(hours=2), now - timedelta(hours=1)
    elif failure == "signature":
        secret = "a-different-test-secret-of-at-least-32-characters"
    elif failure == "missing-user":
        claims["sub"] = "9223372036854775807"
    else:
        with Session(test_engine) as db:
            db.get(User, ids["users"]["a"]).is_enabled = False
            db.commit()
    client.cookies.set(COOKIE_NAME, jwt.encode(claims, secret, algorithm="HS256"), path="/api")

    assert client.get("/api/v1/books").status_code == 401


def test_login_throttles_attempts_without_exposing_credentials(secure_client):
    client, _ = secure_client
    for _ in range(10):
        response = client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "incorrect"})

        assert response.status_code == 401
        assert "incorrect" not in response.text

    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "test-password"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_ai_requires_login_and_hides_foreign_scope_before_provider_calls(secure_client, monkeypatch):
    client, ids = secure_client
    live = AsyncMock()
    embed = AsyncMock()
    monkeypatch.setattr("voice_archive_api.api.v1.live.create_live_token", live)
    monkeypatch.setattr("voice_archive_api.services.library.provider.embed_query", embed)

    assert client.post("/api/v1/live/token").status_code == 401
    assert client.post("/api/v1/library/ask", json={"query": "Question"}).status_code == 401

    sign_in(client)

    assert (
        client.post("/api/v1/library/ask", json={"query": "Question", "book_id": ids["books"]["b"]}).status_code == 404
    )
    assert (
        client.post("/api/v1/library/ask", json={"query": "Question", "series_id": ids["series"]["b"]}).status_code
        == 404
    )
    live.assert_not_called()
    embed.assert_not_called()


def test_library_wide_retrieval_uses_scoped_sessions(secure_client, monkeypatch):
    client, ids = secure_client
    sign_in(client)
    selected = []
    read_scope = retrieval.read_scope

    def capture_scope(db, request):
        result = read_scope(db, request)
        selected.append({book.id for book in result.books})
        return result

    monkeypatch.setattr(retrieval, "read_scope", capture_scope)

    assert client.post("/api/v1/library/ask", json={"query": "Question"}).status_code == 200

    assert selected == [{ids["books"]["a"], ids["books"]["public"]}]


def test_content_import_and_public_reads_follow_book_ownership(secure_client, test_engine):
    client, _ = secure_client
    sign_in(client)
    book_id = client.post("/api/v1/books", json={"title": "Imported", "language": "en"}).json()["id"]
    payload = {
        "schema_version": 1,
        "sections": [{"source_key": "root", "kind": "chapter", "title": "Chapter", "sibling_sequence": 0}],
        "segments": [
            {
                "source_key": "text",
                "section_source_key": "root",
                "sequence": 0,
                "kind": "prose",
                "html_content": "<p>Readable content</p>",
            }
        ],
    }

    result = client.post(f"/api/v1/books/{book_id}/content-imports", json=payload)

    assert result.status_code == 200, result.text
    version = result.json()["content_version"]
    assert client.get(f"/api/v1/books/{book_id}/segments", headers={"If-Content-Version": version}).status_code == 200
    etag = client.get(f"/api/v1/books/{book_id}/content").headers["etag"]

    sign_in(client, "b")

    assert client.post(f"/api/v1/books/{book_id}/content-imports", json=payload).status_code == 404
    assert client.delete(f"/api/v1/books/{book_id}/content", headers={"If-Match": etag}).status_code == 404

    with Session(test_engine) as db:
        db.get(Book, book_id).owner_user_id = None
        db.commit()
    client.post("/api/v1/auth/logout")

    assert client.get(f"/api/v1/books/{book_id}/content").status_code == 200
    response = client.get(f"/api/v1/books/{book_id}/segments", headers={"If-Content-Version": version})

    assert response.status_code == 200
    assert response.json()["items"][0]["text_content"] == "Readable content"


def test_sign_in_without_server_secret_is_unavailable(secure_client, application):
    client, _ = secure_client
    application.state.settings.auth_secret = None

    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "test-password"})

    assert response.status_code == 503
    assert COOKIE_NAME not in client.cookies
