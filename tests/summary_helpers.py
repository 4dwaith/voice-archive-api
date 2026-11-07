"""Shared source fixtures for summary database, service and feature coverage."""

from uuid import UUID

import pytest

from voice_archive_api.logic.summaries import SummaryDraft, build_summary_targets
from voice_archive_api.services.series import service as series
from voice_archive_api.services.series.schemas import SeriesCreate
from voice_archive_api.services.summary import service as summaries


@pytest.fixture(name="summary_book")
def create_summary_book(client):
    book_id = client.post("/api/v1/books", json={"title": "Journey", "language": "kn"}).json()["id"]
    payload = {
        "schema_version": 1,
        "sections": [{"source_key": "chapter", "kind": "chapter", "title": "Home", "sibling_sequence": 0}],
        "segments": [
            {
                "source_key": "a",
                "sequence": 0,
                "kind": "prose",
                "section_source_key": "chapter",
                "html_content": "<p>ತಾಯಿಯನ್ನು ನೋಡಲು ಮನೆಗೆ ಬಂದನು.</p>",
            }
        ],
    }
    response = client.post(f"/api/v1/books/{book_id}/content-imports", json=payload)
    assert response.status_code == 200

    return book_id, UUID(response.json()["content_version"])


def create_summary_drafts(snapshot):
    return [
        SummaryDraft(**target.model_dump(), language="en", summary=f"The {target.kind} describes a return home.")
        for target in build_summary_targets(snapshot.book_id, snapshot.fragments)
    ]


@pytest.fixture(name="summary_series")
def create_summary_series(client, summary_book):
    earlier_id, _ = summary_book
    factory = client.app.state.session_factory
    snapshot = summaries.start_summary_run(factory, earlier_id, "test-model")
    summaries.publish_book_summaries(factory, snapshot, create_summary_drafts(snapshot))
    current_id = client.post("/api/v1/books", json={"title": "Following", "language": "en"}).json()["id"]
    response = client.post(
        f"/api/v1/books/{current_id}/content-imports",
        json={
            "schema_version": 1,
            "sections": [
                {"source_key": key, "kind": "chapter", "title": key, "sibling_sequence": index}
                for index, key in enumerate(["departure", "home"])
            ],
            "segments": [
                {
                    "source_key": str(index),
                    "sequence": index,
                    "kind": "prose",
                    "section_source_key": "departure" if index < 40 else "home",
                    "html_content": f"<p>{('The traveller describes the journey at point ' + str(index) + '. ') * 30}</p>",
                }
                for index in range(41)
            ],
        },
    )
    assert response.status_code == 200
    with factory() as db:
        series_id = series.create_series(db, SeriesCreate(title="Journeys")).id
        series.replace_series_books(db, series_id, [earlier_id, current_id])

    return series_id, earlier_id, current_id
