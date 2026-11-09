import pytest

from voice_archive_api.logic.search.dense import resolve_query_texts, validate_query_vector


@pytest.mark.parametrize(
    "language, expected", [("en", {"primary": "Why?"}), ("kn", {"primary": "ಏಕೆ?", "translated": "Why?"})]
)
def test_query_language_follows_the_representation(language, expected):
    assert resolve_query_texts(language, "ಏಕೆ?", "Why?") == expected


@pytest.mark.parametrize("vector", [[0.0, 0.0], [1.0], [float("nan"), 1.0], [float("inf"), 1.0]])
def test_invalid_query_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        validate_query_vector(vector, 2)
