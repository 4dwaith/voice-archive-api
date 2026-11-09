from dataclasses import replace
from uuid import uuid4

import pytest

from voice_archive_api.logic.dense import (
    WindowMatch,
    expand_segment_candidates,
    fuse_window_rankings,
    resolve_query_texts,
    select_segment_ids,
    validate_query_vector,
)


def select_from_ranked_lists(ranked_lists, book_ids):
    ranked_windows = fuse_window_rankings(ranked_lists, book_ids)
    candidates = expand_segment_candidates(ranked_windows, book_ids)

    return select_segment_ids(candidates)


def test_duplicate_channel_entries_do_not_add_votes_or_shift_ranks():
    first = WindowMatch(uuid4(), 1, 0, [1], 0.1)
    second = WindowMatch(uuid4(), 1, 1, [2], 0.2)

    selected = select_from_ranked_lists({"primary": [first, first, second], "translated": [second]}, [1])

    assert selected == [2, 1]


def test_lexical_votes_join_individual_dense_lists():
    first = WindowMatch(uuid4(), 1, 0, [1], 0.1)
    recovered = WindowMatch(uuid4(), 1, 1, [2], -0.9)

    selected = select_from_ranked_lists(
        {"primary": [first], "translated": [recovered, first], "lexical": [recovered, recovered]}, [1]
    )

    assert selected == [2, 1]


def test_overlapping_segments_receive_only_their_strongest_window_score():
    first = WindowMatch(uuid4(), 1, 0, [3], 0.1)
    second = WindowMatch(uuid4(), 1, 1, [2], 0.2)
    third = WindowMatch(uuid4(), 1, 2, [3, 4], 0.3)

    selected = select_from_ranked_lists({"primary": [first, second, third], "translated": [second, third]}, [1])

    assert selected == [2, 3, 4]


def test_scope_relevance_precedes_book_order_and_reading_order_breaks_ties():
    early = WindowMatch(uuid4(), 1, 0, [1], 0.8)
    relevant = WindowMatch(uuid4(), 2, 1, [3, 4], 0.1)
    preceding = WindowMatch(uuid4(), 2, 0, [2], 0.1)

    selected = select_from_ranked_lists({"primary": [early, relevant, preceding]}, [1, 2])

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


def test_candidate_stages_preserve_all_segments_and_window_provenance_before_selection():
    first = WindowMatch(uuid4(), 1, 0, list(range(30)), 0.1)
    second = WindowMatch(uuid4(), 1, 1, [29, 30], 0.2)

    ranked_windows = fuse_window_rankings({"primary": [second, first, first]}, [1])
    candidates = expand_segment_candidates(ranked_windows, [1])
    overlap = next(candidate for candidate in candidates if candidate.segment_id == 29)

    assert [ranked.window.id for ranked in ranked_windows] == [first.id, second.id]
    assert len(candidates) == 31
    assert overlap.window_ids == (first.id, second.id)
    assert overlap.book_id == 1
    assert overlap.score == ranked_windows[0].score
    assert overlap.source_order == (0, 0, 29)

    selected = select_segment_ids(candidates)

    assert selected == list(range(25))
    assert select_segment_ids(candidates, limit=31) == list(range(31))
    assert len(candidates) == 31


def test_final_selection_consumes_updated_candidate_scores():
    window = WindowMatch(uuid4(), 1, 0, [1, 2], 0.1)
    candidates = expand_segment_candidates(fuse_window_rankings({"primary": [window]}, [1]), [1])
    reranked = [replace(candidate, score=float(candidate.segment_id)) for candidate in candidates]

    selected = select_segment_ids(reranked, limit=1)

    assert selected == [2]


def test_empty_candidate_pool_and_zero_selection_limit():
    ranked_windows = fuse_window_rankings({}, [1])
    candidates = expand_segment_candidates(ranked_windows, [1])
    window = WindowMatch(uuid4(), 1, 0, [1], 0.1)
    populated = expand_segment_candidates(fuse_window_rankings({"primary": [window]}, [1]), [1])

    assert ranked_windows == []
    assert candidates == []
    assert select_segment_ids(candidates) == []
    assert select_segment_ids(populated, limit=0) == []


def test_negative_selection_limit_is_rejected():
    with pytest.raises(ValueError):
        select_segment_ids([], limit=-1)
