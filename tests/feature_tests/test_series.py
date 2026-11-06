"""Domain-level catalog journeys backed by the production HTTP application."""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.http.test_content import setup_book

scenarios("../features/series")


def read_catalog(client):
    books = client.get("/api/v1/books")
    series = client.get("/api/v1/series")
    assert books.status_code == series.status_code == 200
    return {"books": books.json(), "series": series.json()}


def read_book(client, catalog, title):
    response = client.get(f"/api/v1/books/{catalog['books'][title]}")
    assert response.status_code == 200, response.text
    assert response.json()["title"] == title
    return response.json()


def create_book(client, catalog, title, series_id=None):
    response = client.post("/api/v1/books", json={"title": title, "language": "en", "series_id": series_id})
    assert response.status_code == 201, response.text
    catalog["books"][title] = response.json()["id"]


@pytest.fixture
def catalog():
    return {"books": {}, "series": {}, "draft": {}}


@given(parsers.parse('series "{title}" contains "{books}" in that order'))
def create_series_with_books(client, catalog, title, books):
    response = client.post("/api/v1/series", json={"title": title})
    assert response.status_code == 201, response.text
    catalog["series"][title] = response.json()["id"]
    for book_title in books.split(", "):
        create_book(client, catalog, book_title, catalog["series"][title])


@given(parsers.parse('my library contains the standalone book "{title}"'))
def create_standalone_book(client, catalog, title):
    create_book(client, catalog, title)


@given(parsers.parse('book "{title}" is no longer in my library'))
def remove_missing_book(client, catalog, title):
    create_book(client, catalog, title)

    response = client.delete(f"/api/v1/books/{catalog['books'][title]}")

    assert response.status_code == 204, response.text


@given(parsers.parse('series "{title}" is no longer in my catalog'))
def remove_missing_series(client, catalog, title):
    response = client.post("/api/v1/series", json={"title": title})
    assert response.status_code == 201, response.text
    catalog["series"][title] = response.json()["id"]

    response = client.delete(f"/api/v1/series/{catalog['series'][title]}")

    assert response.status_code == 204, response.text


@given(parsers.parse('I am editing book "{title}"'))
def select_book_for_editing(catalog, title):
    catalog["selected_book"] = title
    catalog["draft"] = {}


@given(parsers.parse('I am adding book "{title}"'))
def prepare_new_book(catalog, title):
    catalog["draft"] = {"title": title, "language": "en"}


@given(parsers.parse('I have selected series "{title}" for the book'))
def select_book_series(catalog, title):
    catalog["draft"]["series_id"] = catalog["series"][title]


@given("I have cleared the book's series")
def clear_book_series(catalog):
    catalog["draft"]["series_id"] = None


@given(parsers.parse("I have entered series position {position:d}"))
def enter_series_position(catalog, position):
    catalog["draft"]["series_index"] = position


@given("I have cleared the book's series position")
def clear_series_position(catalog):
    catalog["draft"]["series_index"] = None


@given(parsers.parse('I have changed the book\'s description to "{description}"'))
def enter_book_description(catalog, description):
    catalog["draft"]["description"] = description


@when("I save the book's changes")
def save_book_changes(client, catalog):
    catalog["before"] = read_catalog(client)
    book_id = catalog["books"][catalog["selected_book"]]
    catalog["response"] = client.patch(f"/api/v1/books/{book_id}", json=catalog["draft"])


@when("I save the new book")
def save_new_book(client, catalog):
    catalog["before"] = read_catalog(client)
    catalog["response"] = client.post("/api/v1/books", json=catalog["draft"])


@when(parsers.parse('I replace the books in series "{title}" with "{books}"'))
def replace_series_books(client, catalog, title, books):
    catalog["before"] = read_catalog(client)
    ids = [catalog["books"][book_title] for book_title in books.split(", ")]
    catalog["response"] = client.put(f"/api/v1/series/{catalog['series'][title]}/books", json=ids)


@when(parsers.parse('I remove all books from series "{title}"'))
def clear_series_membership(client, catalog, title):
    catalog["before"] = read_catalog(client)
    catalog["response"] = client.put(f"/api/v1/series/{catalog['series'][title]}/books", json=[])


@when("I delete the book")
def delete_selected_book(client, catalog):
    catalog["before"] = read_catalog(client)
    book_id = catalog["books"][catalog["selected_book"]]
    catalog["response"] = client.delete(f"/api/v1/books/{book_id}")


@when(parsers.parse('I delete series "{title}"'))
def delete_series(client, catalog, title):
    catalog["selected_series"] = catalog["series"][title]
    catalog["response"] = client.delete(f"/api/v1/series/{catalog['selected_series']}")


@when(parsers.parse('I rename series "{title}" to "{replacement}"'))
def rename_series(client, catalog, title, replacement):
    catalog["selected_series"] = catalog["series"][title]
    catalog["response"] = client.patch(f"/api/v1/series/{catalog['selected_series']}", json={"title": replacement})


@when(parsers.parse("I browse series with a page size of {size:d} after skipping {skip:d} series"))
def browse_series(client, catalog, size, skip):
    catalog["page"] = (size, skip)
    catalog["response"] = client.get("/api/v1/series", params={"limit": size, "offset": skip})


@given(parsers.parse("I am adding a series with its title {entry}"))
def prepare_invalid_series_title(catalog, entry):
    catalog["draft"] = {"title": {"cleared": None, "empty": "", "spaces only": "   "}[entry]}


@when("I save the new series")
def save_new_series(client, catalog):
    catalog["before"] = read_catalog(client)
    catalog["response"] = client.post("/api/v1/series", json=catalog["draft"])


@then("my book changes are saved")
@then("my series changes are saved")
def assert_changes_saved(catalog):
    assert catalog["response"].status_code == 200, catalog["response"].text


@then("I am told that series positions must remain consecutive")
@then("I am told that a series cannot contain missing, repeated or another series' books")
def assert_invalid_series_change(catalog):
    response = catalog["response"]

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "series_order_conflict"


@then("I am told that the series cannot be found")
def assert_missing_series(catalog):
    response = catalog["response"]

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "series_not_found"


@then("I am told that the series title is invalid")
def assert_invalid_series_title(catalog):
    response = catalog["response"]

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert any(issue["location"] == ["body", "title"] for issue in response.json()["issues"])


@then(parsers.parse('series "{title}" contains "{books}" in that order'))
def assert_member_order(client, catalog, title, books):
    titles = books.split(", ")
    response = client.get(f"/api/v1/series/{catalog['series'][title]}")

    assert response.status_code == 200, response.text
    assert response.json()["book_ids"] == [catalog["books"][book_title] for book_title in titles]
    for position, book_title in enumerate(titles, start=1):
        book = read_book(client, catalog, book_title)
        assert (book["series_id"], book["series_index"]) == (response.json()["id"], position)


@then(parsers.parse('series "{title}" has no books'))
def assert_empty_series(client, catalog, title):
    response = client.get(f"/api/v1/series/{catalog['series'][title]}")

    assert response.status_code == 200, response.text
    assert response.json()["book_ids"] == []


@then(parsers.parse('"{title}" is a standalone book'))
def assert_no_membership(client, catalog, title):
    book = read_book(client, catalog, title)

    assert (book["series_id"], book["series_index"], book["series_title"]) == (None, None, None)


@then(parsers.parse('the book\'s description is "{description}"'))
def assert_book_description(client, catalog, description):
    assert read_book(client, catalog, catalog["selected_book"])["description"] == description


@then("my catalog remains unchanged")
def assert_catalog_unchanged(client, catalog):
    assert read_catalog(client) == catalog["before"]


@then("the book is removed from my library")
def assert_book_removed(client, catalog):
    book_id = catalog["books"][catalog["selected_book"]]

    assert catalog["response"].status_code == 204, catalog["response"].text
    assert client.get(f"/api/v1/books/{book_id}").status_code == 404
    assert book_id not in {book["id"] for book in read_catalog(client)["books"]["items"]}


@then("the series is removed from my catalog")
def assert_series_removed(client, catalog):
    assert catalog["response"].status_code == 204, catalog["response"].text
    assert client.get(f"/api/v1/series/{catalog['selected_series']}").status_code == 404


@then(parsers.parse('the series is named "{title}"'))
def assert_series_name(client, catalog, title):
    response = client.get(f"/api/v1/series/{catalog['selected_series']}")

    assert catalog["response"].status_code == 200, catalog["response"].text
    assert response.status_code == 200, response.text
    assert response.json()["title"] == catalog["response"].json()["title"] == title


@then(parsers.parse('its member books show the series name "{title}"'))
def assert_member_metadata(client, catalog, title):
    books = read_catalog(client)["books"]["items"]
    members = [book for book in books if book["series_id"] == catalog["selected_series"]]

    assert len(members) == 3
    for book in members:
        assert book["series_title"] == title
        assert client.get(f"/api/v1/books/{book['id']}").json()["series_title"] == title


@then(parsers.parse('I see series "{title}" out of {total:d} series'))
def assert_series_page(catalog, title, total):
    response = catalog["response"]

    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["title"] for item in page["items"]] == [title]
    assert (page["limit"], page["offset"]) == catalog["page"]
    assert page["total"] == total


@given(parsers.parse('series "{title}" also contains an imported book with media'))
def import_series_book(client, catalog, title):
    book_id, payload, files, cover = setup_book(client)
    assert client.post(f"/api/v1/books/{book_id}/content-imports", json=payload).status_code == 200
    catalog["imported"] = book_id
    catalog["manifest"] = client.get(f"/api/v1/books/{book_id}/content").json()
    catalog["media"] = {file_id: client.get(f"/api/v1/files/{file_id}").content for file_id in (*files, cover)}

    response = client.patch(f"/api/v1/books/{book_id}", json={"series_id": catalog["series"][title]})

    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/books/{book_id}/content").json() == catalog["manifest"]


@then("the imported book and its media are preserved")
def assert_import_preserved(client, catalog):
    book_id = catalog["imported"]

    assert client.get(f"/api/v1/books/{book_id}").json()["series_index"] is None
    assert client.get(f"/api/v1/books/{book_id}/content").json() == catalog["manifest"]
    for file_id, content in catalog["media"].items():
        response = client.get(f"/api/v1/files/{file_id}")
        assert response.status_code == 200, response.text
        assert response.content == content
