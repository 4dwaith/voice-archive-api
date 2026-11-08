import json

import pytest

from voice_archive_api.logic.summaries import (
    BookContentFragment,
    BookSummary,
    GeneratedSummaryWithTranslation,
    SectionSummary,
    SummaryTarget,
    build_book_summary_input,
    build_root_section_summary_inputs,
    build_summary_targets,
    compute_summary_input_fingerprint,
    compute_summary_target_end_indexes,
    format_summary_background_text,
    format_summary_context,
    order_source_fragments,
    split_summary_targets,
    validate_summary_coverage,
    validate_summary_translations,
)


@pytest.mark.parametrize("change", ["id", "title", "summary", "order"])
def test_fingerprint_tracks_background_identity_content_and_order(change):
    earlier_book_summaries = [
        BookSummary(id=1, title="Earlier", summary="A journey begins."),
        BookSummary(id=2, title="Following", summary="The traveller leaves home."),
    ]
    book_metadata = {"title": "Following", "language": "en"}
    source_fragments = [
        BookContentFragment(id=2, parent_id=None, kind="section", text="Home"),
        BookContentFragment(id=3, parent_id=2, kind="passage", text="The traveller returns."),
    ]

    original = compute_summary_input_fingerprint(book_metadata, source_fragments, (1, 3), earlier_book_summaries)
    if change == "order":
        earlier_book_summaries.reverse()
    else:
        earlier_book_summaries[0] = earlier_book_summaries[0].model_copy(
            update={change: 9 if change == "id" else "Changed background"}
        )

    assert (
        compute_summary_input_fingerprint(book_metadata, source_fragments, (1, 3), earlier_book_summaries) != original
    )


def test_root_and_book_inputs_cover_every_target_and_keep_background_separate():
    sections = [
        BookContentFragment(id=10, parent_id=None, kind="section", text="Root"),
        BookContentFragment(id=11, parent_id=10, kind="section", text="Nested"),
        BookContentFragment(id=14, parent_id=10, kind="section", text="Empty nested"),
        BookContentFragment(id=12, parent_id=None, kind="section", text="Other"),
        BookContentFragment(id=13, parent_id=None, kind="section", text="Empty"),
    ]
    passages = [
        BookContentFragment(id=index, parent_id=section_id, kind="passage", text=f"Source {index}")
        for index, section_id in enumerate([None, 10, 11, 10, 12], start=1)
    ]
    source_fragments = order_source_fragments(sections, passages)
    root_section_inputs = build_root_section_summary_inputs(source_fragments)
    book_summary_input = build_book_summary_input(7, source_fragments)
    summary_inputs = [*root_section_inputs, book_summary_input]
    earlier_book_summaries = [BookSummary(id=6, title="Earlier", summary=None)]
    earlier_root_summaries = [SectionSummary(id=10, title="Root", summary="A traveller leaves home.")]
    previous_root_text = format_summary_background_text(root_section_inputs[0])

    context = json.loads(
        format_summary_context(
            root_section_inputs[1],
            {"language": "kn"},
            earlier_book_summaries,
            earlier_root_summaries,
            previous_root_text,
        )
    )

    assert [(f.kind, f.id) for f in source_fragments] == [
        ("passage", 1),
        ("section", 10),
        ("passage", 2),
        ("section", 11),
        ("passage", 3),
        ("passage", 4),
        ("section", 14),
        ("section", 12),
        ("passage", 5),
        ("section", 13),
    ]
    assert [root_input.root_section_id for root_input in root_section_inputs] == [10, 12, 13]
    assert [
        [f.id for f in summary_input.source_fragments if f.kind == "passage"] for summary_input in summary_inputs
    ] == [
        [2, 3, 4],
        [5],
        [],
        [1, 2, 3, 4, 5],
    ]
    expected = build_summary_targets(7, source_fragments)
    assert {(t.kind, t.id) for summary_input in summary_inputs for t in summary_input.targets} == {
        (t.kind, t.id) for t in expected
    }
    assert sum(len(summary_input.targets) for summary_input in summary_inputs) == len(expected)
    assert [t.id for t in root_section_inputs[0].targets] == [10, 2, 11, 3, 4, 14]
    assert [t.id for t in book_summary_input.targets] == [7, 1]
    assert context["SOURCE"]["language"] == "kn"
    assert context["SOURCE"]["fragments"] == [
        {"id": 12, "parent_id": None, "kind": "section", "text": "Other"},
        {"id": 5, "parent_id": 12, "kind": "passage", "text": "Source 5"},
    ]
    assert context["BACKGROUND"] == {
        "previous_books": [{"title": "Earlier", "summary": None}],
        "previous_roots": [{"title": "Root", "summary": "A traveller leaves home."}],
        "previous_root_text": "Root\n\nSource 2\n\nNested\n\nSource 3\n\nRoot\n\nSource 4\n\nEmpty nested",
    }


@pytest.mark.parametrize("text,first_size,maximum_size", [("x" * 1200, 74, 75), ("Yes", 101, 101)])
def test_batches_targets_for_a_conservative_response_size(text, first_size, maximum_size):
    targets = [SummaryTarget(kind="section", id=10), *(SummaryTarget(kind="segment", id=index) for index in range(100))]
    passages = [BookContentFragment(id=index, parent_id=10, kind="passage", text=text) for index in range(100)]

    batches = split_summary_targets(targets, passages)

    assert len(batches[0]) == first_size
    assert all(len(batch) <= maximum_size for batch in batches)
    assert [target for batch in batches for target in batch] == targets


def test_translation_output_allowance_grows_with_the_full_passage():
    targets = [SummaryTarget(kind="segment", id=index) for index in range(3)]
    passages = [BookContentFragment(id=index, parent_id=None, kind="passage", text="ಕ" * 8000) for index in range(3)]

    translated_batches = split_summary_targets(targets, passages, translate_passages=True)
    english_batches = split_summary_targets(targets, passages, translate_passages=False)

    assert translated_batches == [targets[:2], targets[2:]]
    assert english_batches == [targets]


@pytest.mark.parametrize(
    "translate_passages,kind,translation",
    [
        (True, "segment", "A complete translation."),
        (False, "segment", None),
        (True, "section", None),
        (True, "book", None),
    ],
)
def test_accepts_translation_only_for_non_english_passages(translate_passages, kind, translation):
    draft = GeneratedSummaryWithTranslation(
        kind=kind, id=1, summary="A concise summary.", english_translation=translation
    )

    validate_summary_translations([draft], translate_passages=translate_passages)


@pytest.mark.parametrize(
    "translate_passages,kind,translation",
    [
        (True, "segment", None),
        (False, "segment", "Unnecessary translation."),
        (True, "section", "Unnecessary translation."),
        (True, "book", "Unnecessary translation."),
    ],
)
def test_rejects_missing_or_unrequested_translations(translate_passages, kind, translation):
    draft = GeneratedSummaryWithTranslation(
        kind=kind, id=1, summary="A concise summary.", english_translation=translation
    )

    with pytest.raises(ValueError, match="translations"):
        validate_summary_translations([draft], translate_passages=translate_passages)


@pytest.mark.parametrize("translation", ["  ", "Text\x00"])
def test_rejects_unusable_translation_text(translation):
    with pytest.raises(ValueError, match="translation"):
        GeneratedSummaryWithTranslation(
            kind="segment", id=1, summary="A concise summary.", english_translation=translation
        )


@pytest.mark.parametrize("translation", ["A complete translated passage. " * 400, "123 —"])
def test_translation_preserves_full_passage_text(translation):
    draft = GeneratedSummaryWithTranslation(
        kind="segment", id=1, summary="A concise summary.", english_translation=translation
    )

    assert draft.english_translation == translation.strip()
    validate_summary_translations([draft], translate_passages=True)


def test_target_cutoffs_follow_subtrees_and_passage_positions_with_overlapping_ids():
    source_fragments = [
        BookContentFragment(id=1, parent_id=None, kind="section", text="Root"),
        BookContentFragment(id=1, parent_id=1, kind="passage", text="Before the nested section."),
        BookContentFragment(id=2, parent_id=1, kind="section", text="Nested"),
        BookContentFragment(id=2, parent_id=2, kind="passage", text="Inside the nested section."),
        BookContentFragment(id=3, parent_id=1, kind="passage", text="The parent resumes."),
        BookContentFragment(id=3, parent_id=1, kind="section", text="Empty nested"),
        BookContentFragment(id=4, parent_id=None, kind="section", text="Later root"),
        BookContentFragment(id=4, parent_id=4, kind="passage", text="Later events."),
    ]
    book_summary_input = build_book_summary_input(7, source_fragments)

    ends = compute_summary_target_end_indexes(book_summary_input)

    assert ends == {
        ("book", 7): 7,
        ("section", 1): 5,
        ("segment", 1): 1,
        ("section", 2): 3,
        ("segment", 2): 3,
        ("segment", 3): 4,
        ("section", 3): 5,
        ("section", 4): 7,
        ("segment", 4): 7,
    }


@pytest.mark.parametrize("summary", ["  ", "Text\x00", "x" * 8001])
def test_rejects_unusable_summary_text(summary):
    with pytest.raises(ValueError):
        GeneratedSummaryWithTranslation(kind="book", id=7, summary=summary)


def test_model_output_schema_does_not_request_language():
    schema = GeneratedSummaryWithTranslation.model_json_schema()

    assert "language" not in schema["properties"]
    assert "language" not in schema["required"]


@pytest.mark.parametrize("summary", ["The narrator describes ಕೃಷ್ಣ with uncertainty.", "ಕೃಷ್ಣ met ಅರ್ಜುನ."])
def test_allows_original_names_in_english_prose(summary):
    result = GeneratedSummaryWithTranslation(kind="book", id=7, summary=f"  {summary}  ")

    assert result.summary == summary


@pytest.mark.parametrize(
    "ids,details",
    [([], "Missing: [('book', 7)]"), ([7, 7], "duplicates: [('book', 7)]"), ([8], "unexpected: [('book', 8)]")],
)
def test_rejects_missing_duplicate_and_unknown_targets(ids, details):
    target = SummaryTarget(kind="book", id=7)
    drafts = [
        GeneratedSummaryWithTranslation(kind="book", id=value, summary="A source-grounded summary.") for value in ids
    ]

    with pytest.raises(ValueError, match="exactly once") as error:
        validate_summary_coverage([target], drafts)

    assert details in str(error.value)
