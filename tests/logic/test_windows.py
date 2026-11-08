import pytest

from voice_archive_api.logic.windows import WindowSegment, build_windows, compute_embedding_input_hash


def create_segment(segment_id, section_id=1, text="aaaa", translation="bbbb", path="Book"):
    return WindowSegment(segment_id, section_id, path, text, translation)


def test_packs_whole_segments_with_bounded_overlap():
    segments = [create_segment(index) for index in range(3)]

    windows = build_windows(segments, "en", len, window_tokens=48, overlap_tokens=4)

    assert [window.segment_ids for window in windows] == [[0, 1], [1, 2]]
    assert all(window.primary_text == "aaaa\n\naaaa" for window in windows)
    assert all(window.translated_text is None for window in windows)


def test_keeps_section_transitions_parent_returns_and_sectionless_runs_separate():
    segments = [create_segment(index, section) for index, section in enumerate([1, 1, 2, 1, None, None, 1])]

    windows = build_windows(segments, "en", len)

    assert [window.segment_ids for window in windows] == [[0, 1], [2], [3], [4, 5], [6]]


def test_translation_expansion_limits_packing_and_prevents_overlap_without_progress():
    segments = [create_segment(index, translation="bbbbbbbb") for index in range(3)]

    windows = build_windows(segments, "kn", len, window_tokens=48, overlap_tokens=10)

    assert [window.segment_ids for window in windows] == [[0], [1], [2]]
    assert all(window.primary_text == "aaaa" and window.translated_text == "bbbbbbbb" for window in windows)


def test_omits_overlap_when_the_trailing_segment_exceeds_the_allowance():
    segments = [create_segment(index) for index in range(3)]

    windows = build_windows(segments, "en", len, window_tokens=48, overlap_tokens=3)

    assert [window.segment_ids for window in windows] == [[0, 1], [2]]


@pytest.mark.parametrize(
    "language,text,translation,kind",
    [
        ("en", "a" * 50, None, "primary"),
        ("kn", "aaaa", "b" * 50, "translated"),
    ],
)
def test_rejects_oversized_complete_segments(language, text, translation, kind):
    segment = create_segment(7, text=text, translation=translation)

    with pytest.raises(ValueError, match=rf"Segment 7 {kind}: estimated .* budget 48"):
        build_windows([segment], language, len, window_tokens=48, overlap_tokens=0)


def test_counts_readable_paths_and_formatting_in_the_budget():
    segment = create_segment(7, path="Series > Book > " + "Chapter" * 10)

    with pytest.raises(ValueError, match="Segment 7 primary"):
        build_windows([segment], "en", len, window_tokens=48, overlap_tokens=0)


def test_requires_accepted_translations_for_non_english_source():
    segment = create_segment(7, translation=None)

    with pytest.raises(ValueError, match="accepted English translation"):
        build_windows([segment], "kn", len)


def test_preserves_unicode_whitespace_and_verse_lines():
    text = "  ತಾಯಿ\nಮನೆಗೆ\n\nಬಂದನು.  "
    segment = create_segment(7, text=text, translation="  Mother\ncame home.  ")

    window = build_windows([segment], "kn", len)[0]

    assert window.primary_text == text
    assert window.translated_text == segment.english_translation


@pytest.mark.parametrize("budget,overlap", [(0, 0), (8192, 125), (48, -1), (48, 48)])
def test_rejects_invalid_sizing_options(budget, overlap):
    with pytest.raises(ValueError, match="window budget"):
        build_windows([], "en", len, window_tokens=budget, overlap_tokens=overlap)


def test_hash_covers_exact_formatted_input():
    options = {"model": "model-a", "dimensions": 768, "input_format": "format-a"}
    baseline = compute_embedding_input_hash("Book", "Text", **options)

    assert compute_embedding_input_hash("Book", "Text", **options) == baseline
    assert compute_embedding_input_hash("Other book", "Text", **options) != baseline
    assert compute_embedding_input_hash("Book", "Text ", **options) != baseline


@pytest.mark.parametrize("field,value", [("model", "model-b"), ("dimensions", 1536), ("input_format", "format-b")])
def test_hash_covers_embedding_configuration(field, value):
    options = {"model": "model-a", "dimensions": 768, "input_format": "format-a"}
    baseline = compute_embedding_input_hash("Book", "Text", **options)

    options[field] = value

    assert compute_embedding_input_hash("Book", "Text", **options) != baseline
