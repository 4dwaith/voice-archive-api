"""Discover every book scenario; undefined steps and failed assertions fail pytest."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

import pytest
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/books")


class Book(TypedDict):
    """A saved book's JSON representation, with timestamps still serialized."""

    id: int
    title: str
    language: str
    author: str | None
    description: str | None
    created_at: str
    updated_at: str


@dataclass
class BookScenario:
    # Plain values deliberately allow invalid input to reach API validation.
    request_draft: dict = field(default_factory=dict)

    books: dict[str, Book] = field(default_factory=dict)  # Keyed by title
    selected: Book | None = None
    original: Book | None = None
    submitted: dict = field(default_factory=dict)
    library_before_save: list[Book] = field(default_factory=list)
    response: Response | None = None


@pytest.fixture
def state():
    # pytest-bdd runs each scenario as one pytest test function (and each
    # Scenario Outline example as a separate test function). Function scope is
    # pytest's default: every step in that test receives this same cached object,
    # so mutations made by one step are visible to later steps. Once the scenario
    # ends, the fixture is discarded; the next scenario gets a fresh state.
    return BookScenario()


def result(state):
    assert state.response is not None, "No action has been performed"
    return state.response


def fetch_library(client):
    response = client.get("/api/v1/books", params={"limit": 100})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == len(page["items"]), "Test library exceeds snapshot size"
    return page["items"]


def fetch_saved_book(client, state):
    assert state.selected is not None, "No book has been selected"
    response = client.get(f"/api/v1/books/{state.selected['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def seed_book(client, state, title, author=None, description=None):
    response = client.post(
        "/api/v1/books",
        json={
            "title": title,
            "language": "en",
            "author": author,
            "description": description,
        },
    )
    assert response.status_code == 201, response.text
    state.books[title] = response.json()


@given("I am adding a new book")
def new_book(state):
    state.request_draft = {}
    state.selected = None
    state.original = None


@given("I am adding a book with valid required details")
def valid_new_book(state):
    new_book(state)
    state.request_draft = {"title": "Length example", "language": "kn"}


@given(parsers.re(r'I have entered the (?P<field>title|author|description) "(?P<value>.*)"'))
def enter_detail(state, field, value):
    state.request_draft[field] = value


@given(parsers.re(r'I have selected the language code "(?P<value>.*)"'))
def enter_language(state, value):
    state.request_draft["language"] = value


@given("I have entered the description")
def enter_description(state, docstring):
    state.request_draft["description"] = docstring


@given(parsers.re(r"I have cleared the (?P<field>title|language|author|description)"))
def clear_detail(state, field):
    state.request_draft[field] = None


@given(parsers.re(r"I have not entered the (?P<field>title|language|author|description)"))
def omit_detail(state, field):
    state.request_draft.pop(field, None)


@given(parsers.re(
    r"I have left the (?P<field>title|author) "
    r"(?P<entry>unentered|cleared|empty|spaces only|Unicode whitespace only|as tabs and line breaks)"
))
def blank_detail(state, field, entry):
    if entry == "unentered":
        omit_detail(state, field)
    else:
        state.request_draft[field] = {
            "cleared": None,
            "empty": "",
            "spaces only": "    ",
            "Unicode whitespace only": " \t\n\u2003",
            "as tabs and line breaks": "\t\n",
        }[entry]


@given('I have entered the title "A Book" with surrounding Unicode spaces and internal tabs and line breaks')
def whitespace_title(state):
    state.request_draft["title"] = "\u2003A\t\nBook "


@given(parsers.parse(
    'I have entered the {field} containing {count:d} copies of "{character}"'
))
def enter_repeated_text(state, field, count, character):
    assert field in {"title", "author", "description"}
    state.request_draft[field] = character * count


@given(parsers.re(r'my library contains "(?P<title>[^"]*)"'))
def existing_book(client, state, title):
    seed_book(client, state, title)


@given(parsers.re(r'my library contains "(?P<title>[^"]*)" by "(?P<author>[^"]*)"'))
def existing_authored_book(client, state, title, author):
    seed_book(client, state, title, author)


@given(parsers.re(
    r'my library contains "(?P<title>[^"]*)" by "(?P<author>[^"]*)" '
    r'with description "(?P<description>[^"]*)"'
))
def existing_detailed_book(client, state, title, author, description):
    seed_book(client, state, title, author, description)


@given(parsers.parse('I have selected the book "{title}"'))
def select_book(state, title):
    state.selected = state.books[title].copy()


@given(parsers.parse('I am editing the book "{title}"'))
def edit_book(state, title):
    select_book(state, title)
    state.original = state.selected.copy()
    # Editing starts with no changes. Untouched values must not enter PATCH.
    state.request_draft = {}


@given(parsers.parse("{count:d} distinct books exist"))
def several_books(client, state, count):
    for index in range(count):
        seed_book(client, state, f"Book {index}")


@given("the selected book is no longer in my library")
def missing_book(client, state):
    seed_book(client, state, "Removed book")
    select_book(state, "Removed book")
    response = client.delete(f"/api/v1/books/{state.selected['id']}")
    assert response.status_code == 204, response.text


def submit(client, state, editing):
    state.library_before_save = fetch_library(client)
    state.submitted = state.request_draft.copy()
    if editing:
        assert state.original is not None, "No book is being edited"
        state.response = client.patch(
            f"/api/v1/books/{state.selected['id']}", json=state.submitted
        )
    else:
        state.response = client.post("/api/v1/books", json=state.submitted)
        if state.response.status_code == 201:
            state.selected = state.response.json()


@when("I save the book")
def create_book(client, state):
    submit(client, state, editing=False)


@when("I save my changes")
def update_book(client, state):
    submit(client, state, editing=True)


@when("I view the book")
def retrieve_book(client, state):
    state.response = client.get(f"/api/v1/books/{state.selected['id']}")


@when("I delete the book")
def delete_book(client, state):
    state.response = client.delete(f"/api/v1/books/{state.selected['id']}")


@when(parsers.parse("I try to {action} the book"))
def access_book(client, state, action):
    if action == "view":
        retrieve_book(client, state)
    elif action == "delete":
        delete_book(client, state)
    else:
        assert action == "edit"
        state.response = client.patch(
            f"/api/v1/books/{state.selected['id']}", json={"title": "Changed"}
        )


@when("I browse my library")
def browse(client, state):
    state.response = client.get("/api/v1/books")


@when(parsers.parse(
    "I browse my library with a page size of {size:d} after skipping {skip:d} books"
))
def browse_page(client, state, size, skip):
    state.response = client.get("/api/v1/books", params={"limit": size, "offset": skip})


@then("the book is added to my library")
def added(client, state):
    response = result(state)
    assert response.status_code == 201, response.text
    assert fetch_saved_book(client, state) == response.json()
    books = fetch_library(client)
    assert len(books) == len(state.library_before_save) + 1
    other_books = [book for book in books if book["id"] != state.selected["id"]]
    assert other_books == state.library_before_save


@then("my changes are saved")
def changes_saved(client, state):
    response = result(state)
    assert response.status_code == 200, response.text
    actual = fetch_saved_book(client, state)
    assert actual == response.json()
    for key in ("title", "language", "author", "description"):
        assert actual[key] == state.submitted.get(key, state.original[key])


@then("its details match what I entered")
def details_match(client, state):
    response = result(state)
    assert response.status_code in (200, 201), response.text
    actual = fetch_saved_book(client, state)
    for key, value in state.submitted.items():
        assert actual[key] == value
        assert response.json()[key] == value


@then(parsers.re(r'the book has the (?P<field>title|language|author|description) "(?P<value>.*)"'))
def book_detail(client, state, field, value):
    assert result(state).json()[field] == value
    assert fetch_saved_book(client, state)[field] == value


@then(parsers.re(r"the book has no (?P<field>author|description)"))
def absent_detail(client, state, field):
    assert result(state).json()[field] is None
    assert fetch_saved_book(client, state)[field] is None


@then("the book has its own identity and creation time")
def generated_values(state):
    book = result(state).json()
    assert isinstance(book["id"], int) and book["id"] > 0
    for key in ("created_at", "updated_at"):
        assert datetime.fromisoformat(book[key]).utcoffset().total_seconds() == 0
    assert book["created_at"] == book["updated_at"]
    assert set(book) == {
        "id", "title", "author", "description", "language", "created_at", "updated_at"
    }


@then("I am told that this book is already in my library")
def duplicate_book(state):
    response = result(state)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "duplicate_book"


@then(parsers.re(r"I am told that the (?P<field>title|language|author|description) is (?P<reason>invalid|required)"))
def invalid_detail(state, field, reason):
    response = result(state)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    # PATCH's omittable fields can report a union branch after the field name.
    issues = [
        issue for issue in body["issues"]
        if issue["location"][:2] == ["body", field]
    ]
    assert issues, body
    if reason == "required":
        assert any(issue["code"] == "missing" for issue in issues)


@then("my library remains unchanged")
def library_unchanged(client, state):
    assert fetch_library(client) == state.library_before_save


@then("the book remains unchanged")
def unchanged(client, state):
    assert state.original is not None
    assert fetch_saved_book(client, state) == state.original


@then("creation time is preserved and update time advances")
def updated_time(state):
    book = result(state).json()
    assert book["created_at"] == state.original["created_at"]
    assert datetime.fromisoformat(book["updated_at"]) > datetime.fromisoformat(
        state.original["updated_at"]
    )


@then("the book is removed from my library")
def removed(client, state):
    response = result(state)
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert state.selected["id"] not in {book["id"] for book in fetch_library(client)}


@then("I am told that the book cannot be found")
def not_found(state):
    response = result(state)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "book_not_found"


@then("I am told that the page bounds are invalid")
def invalid_page(state):
    response = result(state)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any(
        issue["location"] in (["query", "limit"], ["query", "offset"])
        for issue in body["issues"]
    )


@then(parsers.parse(
    "I see {count:d} books out of {total:d}, skipping {skip:d} with a page size of {size:d}"
))
def page_contents(state, count, total, skip, size):
    response = result(state)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["limit"], page["offset"], page["total"]) == (size, skip, total)
    assert len(page["items"]) == count
    expected = list(state.books.values())[skip : skip + size]
    assert page["items"] == expected
