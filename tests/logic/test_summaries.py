import json
from uuid import uuid4

import pytest

from voice_archive_api.logic.summaries import (
    BackgroundSummary,
    PreviousBookSummary,
    SourceFragment,
    SummaryDraft,
    SummaryTarget,
    build_summary_scopes,
    build_summary_targets,
    compute_summary_source_fingerprint,
    compute_summary_target_end_indexes,
    format_summary_context,
    order_source_fragments,
    split_summary_targets,
    validate_summary_coverage,
)


def test_fingerprint_ignores_background_versions_but_tracks_actual_summary_changes():
    previous = PreviousBookSummary(id=1, position=1, title="Earlier", content_version=None, summary="A journey begins.")
    metadata = {"title": "Following", "language": "en"}
    fragments = [
        SourceFragment(id=2, parent_id=None, kind="section", text="Home"),
        SourceFragment(id=3, parent_id=2, kind="passage", text="The traveller returns."),
    ]

    original = compute_summary_source_fingerprint(metadata, fragments, (1, 2), [previous])
    previous.content_version = uuid4()

    assert compute_summary_source_fingerprint(metadata, fragments, (1, 2), [previous]) == original

    previous.summary = "The traveller leaves their family."

    assert compute_summary_source_fingerprint(metadata, fragments, (1, 2), [previous]) != original


def test_root_scopes_cover_every_target_and_keep_background_separate():
    sections = [
        SourceFragment(id=10, parent_id=None, kind="section", text="Root"),
        SourceFragment(id=11, parent_id=10, kind="section", text="Nested"),
        SourceFragment(id=14, parent_id=10, kind="section", text="Empty nested"),
        SourceFragment(id=12, parent_id=None, kind="section", text="Other"),
        SourceFragment(id=13, parent_id=None, kind="section", text="Empty"),
    ]
    passages = [
        SourceFragment(id=index, parent_id=section_id, kind="passage", text=f"Source {index}")
        for index, section_id in enumerate([None, 10, 11, 10, 12], start=1)
    ]
    fragments = order_source_fragments(sections, passages)
    scopes = build_summary_scopes(7, fragments)
    previous_books = [PreviousBookSummary(id=6, position=1, title="Earlier", content_version=None, summary=None)]
    previous_roots = [BackgroundSummary(title="Root", summary="A traveller leaves home.")]

    context = json.loads(
        format_summary_context(scopes[1], {"language": "kn"}, previous_books, previous_roots, scopes[0])
    )

    assert [(f.kind, f.id) for f in fragments] == [
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
    assert [[f.id for f in scope.fragments if f.kind == "passage"] for scope in scopes] == [
        [2, 3, 4],
        [5],
        [],
        [1, 2, 3, 4, 5],
    ]
    expected = build_summary_targets(7, fragments)
    assert {(t.kind, t.id) for scope in scopes for t in scope.targets} == {(t.kind, t.id) for t in expected}
    assert sum(len(scope.targets) for scope in scopes) == len(expected)
    assert [t.id for t in scopes[0].targets] == [10, 2, 11, 3, 4, 14]
    assert [t.id for t in scopes[-1].targets] == [7, 1]
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


@pytest.mark.parametrize("text,first_size,maximum_size", [("x" * 1200, 36, 37), ("Yes", 90, 93)])
def test_batches_targets_for_a_conservative_response_size(text, first_size, maximum_size):
    targets = [SummaryTarget(kind="section", id=10), *(SummaryTarget(kind="segment", id=index) for index in range(100))]
    passages = [SourceFragment(id=index, parent_id=10, kind="passage", text=text) for index in range(100)]

    batches = split_summary_targets(targets, passages)

    assert len(batches[0]) == first_size
    assert all(len(batch) <= maximum_size for batch in batches)
    assert [target for batch in batches for target in batch] == targets


def test_target_cutoffs_follow_subtrees_and_passage_positions_with_overlapping_ids():
    fragments = [
        SourceFragment(id=1, parent_id=None, kind="section", text="Root"),
        SourceFragment(id=1, parent_id=1, kind="passage", text="Before the nested section."),
        SourceFragment(id=2, parent_id=1, kind="section", text="Nested"),
        SourceFragment(id=2, parent_id=2, kind="passage", text="Inside the nested section."),
        SourceFragment(id=3, parent_id=1, kind="passage", text="The parent resumes."),
        SourceFragment(id=3, parent_id=1, kind="section", text="Empty nested"),
        SourceFragment(id=4, parent_id=None, kind="section", text="Later root"),
        SourceFragment(id=4, parent_id=4, kind="passage", text="Later events."),
    ]
    scope = build_summary_scopes(7, fragments)[-1]

    ends = compute_summary_target_end_indexes(scope)

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


@pytest.mark.parametrize(
    "language,summary", [("kn", "An English sentence."), ("en", "ಕನ್ನಡ ಸಾರಾಂಶ."), ("en", "  "), ("en", "Text\x00")]
)
def test_rejects_unusable_or_non_english_output(language, summary):
    with pytest.raises(ValueError):
        SummaryDraft(kind="book", id=7, language=language, summary=summary)


def test_allows_original_names_in_english_prose():
    result = SummaryDraft(kind="book", id=7, language="en", summary="  The narrator describes ಕೃಷ್ಣ with uncertainty.  ")

    assert result.summary == "The narrator describes ಕೃಷ್ಣ with uncertainty."


@pytest.mark.parametrize("ids", [[], [7, 7], [8]])
def test_rejects_missing_duplicate_and_unknown_targets(ids):
    target = SummaryTarget(kind="book", id=7)
    drafts = [SummaryDraft(kind="book", id=value, language="en", summary="A source-grounded summary.") for value in ids]

    with pytest.raises(ValueError, match="exactly once"):
        validate_summary_coverage([target], drafts)
