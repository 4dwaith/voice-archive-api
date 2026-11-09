"""Exercise the installed PostgreSQL dictionaries, locale and trigram extents."""

import unicodedata
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from alembic import command
from voice_archive_api.db.models.windows import RetrievalWindow
from voice_archive_api.services.library.search.lexical import search_lexical_windows


@pytest.mark.parametrize(
    "language, source, query, matches",
    [
        ("en", "Travellers were running home.", "traveller runs", True),
        ("en", "The traveller returned home.", '"returned home"', True),
        ("en", "The traveller returned to his old home.", '"returned home"', False),
        ("en", "Running home.", "the and", False),
        ("en", "A cat sleeps.", "cat", True),
        ("en", "A cat sleeps.", '" & : ()', False),
        ("en", "A cat sleeps.", "Secret gloss", False),
        ("kn", "ಬಹಳ ದೂರದ ಊರಿನಲ್ಲಿ ದೇವರಾಜ ವಾಸವಾಗಿದ್ದನು.", "ದೇವರಾಜ", True),
        ("kn", "ಬಹಳ ದೂರದ ಊರಿನಲ್ಲಿ ದೇವರಾಜ ವಾಸವಾಗಿದ್ದನು.", "ದೇವರಜ", True),
        ("kn", "ದೇವರಾಜ ಮನೆಗೆ ಬಂದನು.", "ದೇವರಾಜ, ಮನೆಗೆ?", True),
        ("kn", "ದೇವರಾಜ ಮನೆಗೆ ಬಂದನು.", "Where is ದೇವರಾಜ?", False),
        ("fr", "Le voyageur rentrait chez lui.", "voyageur", True),
        ("fr", "Le voyageur rentrait chez lui.", "Secret gloss", False),
        ("kn", "ದೇವರಾಜ ಮನೆಗೆ ಬಂದನು.", "Where is Devaraja?", False),
        ("kn", "ದೇವರಾಜ ಮನೆಗೆ ಬಂದನು.", "ದ", True),
        ("kn", "ಗೋವಿಂದ ಮನೆಗೆ ಬಂದನು.", unicodedata.normalize("NFD", "ಗೋವಿಂದ"), True),
    ],
)
def test_language_specific_matches(client, summary_book, language, source, query, matches):
    book_id, version = summary_book
    window_id = uuid4()
    factory = client.app.state.session_factory
    with factory.begin() as db:
        db.add(
            RetrievalWindow(
                id=window_id,
                book_id=book_id,
                content_version=version,
                sequence=0,
                segment_ids=[123],
                path="Journey",
                primary_text=source,
                translated_text=None if language == "en" else "Secret gloss",
            )
        )

    with factory.begin() as db:
        found = search_lexical_windows(db, [book_id], language == "en", query)
        outside_scope = search_lexical_windows(db, [book_id + 1], language == "en", query)
        window = db.get(RetrievalWindow, window_id)

        assert [row.id for row in found] == ([window_id] if matches else [])
        assert outside_scope == []
        assert (window.english_vector is not None) == (language == "en")
        assert window.trigram_text == (None if language == "en" else unicodedata.normalize("NFC", source))
        assert search_lexical_windows(db, [book_id], language != "en", query) == []


@pytest.mark.parametrize(
    "is_english, sources, terms",
    [
        (True, ["Cats sleep.", "Dogs bark."], ["cats", "dogs", "cats"]),
        (False, ["ದೇವರಾಜ ಮನೆಗೆ ಬಂದನು.", "ಗೋವಿಂದ ಊರಿಗೆ ಹೋದನು."], ["ದೇವರಾಜ", "ಗೋವಿಂದ", "ದೇವರಾಜ"]),
    ],
)
def test_terms_replace_the_question_and_form_one_deduplicated_ranking(client, summary_book, is_english, sources, terms):
    book_id, version = summary_book
    identities = [uuid4(), uuid4()]
    factory = client.app.state.session_factory
    with factory.begin() as db:
        for sequence, source in enumerate(sources):
            db.add(
                RetrievalWindow(
                    id=identities[sequence],
                    book_id=book_id,
                    content_version=version,
                    sequence=sequence,
                    segment_ids=[sequence],
                    path="Journey",
                    primary_text=source,
                    translated_text=None if is_english else "English translation",
                )
            )

    with factory.begin() as db:
        matches = search_lexical_windows(db, [book_id], is_english, "Unrelated full question", terms)
        unique_matches = search_lexical_windows(db, [book_id], is_english, "Unrelated full question", terms[:2])

        assert {match.id for match in matches} == set(identities)
        assert matches == unique_matches


def test_deployed_locale_classifies_kannada_letters(client):
    with client.app.state.session_factory() as db:
        locale = db.execute(
            text("SELECT datlocprovider, datcollate FROM pg_database WHERE datname = current_database()")
        )
        assert locale.one() == ("c", "en_US.utf8")
        assert db.scalar(text("SELECT cardinality(show_trgm('ದೇವರಾಜ'))")) > 0


def test_generated_columns_backfill_and_follow_window_updates(client, summary_book, migration_config):
    book_id, version = summary_book
    factory = client.app.state.session_factory
    command.downgrade(migration_config, "0026_pgvector")
    window_id = uuid4()
    with factory.begin() as db:
        db.execute(
            text("""INSERT INTO retrieval_windows
            (id, book_id, content_version, sequence, segment_ids, path, primary_text)
            VALUES (:id, :book, :version, 0, '{123}', 'Journey', 'Running home.')"""),
            {"id": window_id, "book": book_id, "version": version},
        )

    command.upgrade(migration_config, "head")

    with factory.begin() as db:
        assert db.get(RetrievalWindow, window_id).english_vector is not None
        db.get(RetrievalWindow, window_id).primary_text = "Changed passage."

    with factory.begin() as db:
        assert search_lexical_windows(db, [book_id], True, "changed")
        db.get(RetrievalWindow, window_id).translated_text = "English translation."
        db.get(RetrievalWindow, window_id).content_version = uuid4()

    with factory.begin() as db:
        window = db.get(RetrievalWindow, window_id)
        assert window.english_vector is None
        assert window.trigram_text == "Changed passage."
        assert search_lexical_windows(db, [book_id], False, "changed") == []
        db.execute(delete(RetrievalWindow).where(RetrievalWindow.id == window_id))

    with factory() as db:
        assert db.scalars(select(RetrievalWindow)).all() == []
