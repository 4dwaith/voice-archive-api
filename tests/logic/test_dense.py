from uuid import uuid4

import pytest

from voice_archive_api.logic.dense import WindowMatch, resolve_query_texts, select_segment_ids, validate_query_vector


def test_duplicate_channel_entries_do_not_add_votes_or_shift_ranks():
    first = WindowMatch(uuid4(), 1, 0, [1], 0.1)
    second = WindowMatch(uuid4(), 1, 1, [2], 0.2)

    selected = select_segment_ids({"primary": [first, first, second], "translated": [second]}, [1])

    assert selected == [2, 1]


def test_overlapping_segments_receive_only_their_strongest_window_score():
    first = WindowMatch(uuid4(), 1, 0, [3], 0.1)
    second = WindowMatch(uuid4(), 1, 1, [2], 0.2)
    third = WindowMatch(uuid4(), 1, 2, [3, 4], 0.3)

    selected = select_segment_ids({"primary": [first, second, third], "translated": [second, third]}, [1])

    assert selected == [2, 3, 4]


def test_scope_relevance_precedes_book_order_and_reading_order_breaks_ties():
    early = WindowMatch(uuid4(), 1, 0, [1], 0.8)
    relevant = WindowMatch(uuid4(), 2, 1, [3, 4], 0.1)
    preceding = WindowMatch(uuid4(), 2, 0, [2], 0.1)

    selected = select_segment_ids({"primary": [early, relevant, preceding]}, [1, 2])

    assert selected == [2, 3, 4, 1]


@pytest.mark.parametrize(
    "language, expected", [("en", {"primary": "Why?"}), ("kn", {"primary": "ಏಕೆ?", "translated": "Why?"})]
)
def test_query_language_follows_the_representation(language, expected):
    assert resolve_query_texts(language, "ಏಕೆ?", "Why?") == expected


@pytest.mark.parametrize("vector", [[0.0, 0.0], [1.0], [float("nan"), 1.0], [float("inf"), 1.0]])
def test_invalid_query_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        validate_query_vector(vector, 2)
