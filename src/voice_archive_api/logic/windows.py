"""Pack complete canonical segments into aligned source/translation windows."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from itertools import groupby


@dataclass(frozen=True)
class WindowSegment:
    id: int
    section_id: int | None
    path: str
    text: str
    english_translation: str | None


@dataclass(frozen=True)
class WindowContent:
    section_id: int | None
    segment_ids: list[int]
    path: str
    primary_text: str
    translated_text: str | None


def format_embedding_input(path: str, text: str) -> str:
    return f"title: {path} | text: {text}"


def compute_embedding_input_hash(path: str, text: str, *, model: str, dimensions: int, input_format: str) -> str:
    return sha256(
        json.dumps(
            [model, dimensions, input_format, format_embedding_input(path, text)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def assemble_window_content(segments: list[WindowSegment], *, translate: bool) -> WindowContent:
    return WindowContent(
        section_id=segments[0].section_id,
        segment_ids=[segment.id for segment in segments],
        path=segments[0].path,
        primary_text="\n\n".join(segment.text for segment in segments),
        translated_text="\n\n".join(segment.english_translation or "" for segment in segments) if translate else None,
    )


def build_windows(
    segments: list[WindowSegment],
    primary_language: str,
    count_tokens: Callable[[str], int],
    *,
    window_tokens: int | None = None,
    overlap_tokens: int = 125,
) -> list[WindowContent]:
    budget = window_tokens if window_tokens is not None else 1500 if primary_language == "kn" else 1000
    if not 32 <= budget <= 4096 or not 0 <= overlap_tokens < budget:
        raise ValueError("Use a window budget of 32..4096 and a nonnegative overlap smaller than the budget")
    translate = primary_language != "en"
    windows: list[WindowContent] = []

    def estimate_input_tokens(path: str, text: str) -> int:
        return count_tokens(format_embedding_input(path, text)) + 16

    def can_fit_window(units: list[WindowSegment]) -> bool:
        content = assemble_window_content(units, translate=translate)
        return all(
            estimate_input_tokens(content.path, text) <= budget
            for text in (content.primary_text, content.translated_text)
            if text is not None
        )

    # Contiguous runs preserve parent returns and separate sectionless runs.
    for _, run in groupby(segments, key=lambda segment: segment.section_id):
        current: list[WindowSegment] = []
        for segment in run:
            if translate and not (segment.english_translation or "").strip():
                raise ValueError(
                    f"Segment {segment.id} needs an accepted English translation; run summarize-book first"
                )
            for kind, text in (
                ("primary", segment.text),
                ("translated", segment.english_translation if translate else None),
            ):
                if text is not None and (size := estimate_input_tokens(segment.path, text)) > budget:
                    raise ValueError(
                        f"Segment {segment.id} {kind}: estimated {size} tokens, budget {budget}; splitting is deferred"
                    )
            if current and not can_fit_window([*current, segment]):
                windows.append(assemble_window_content(current, translate=translate))
                overlap: list[WindowSegment] = []
                for trailing in reversed(current):
                    candidate = [trailing, *overlap]
                    content = assemble_window_content(candidate, translate=translate)
                    if any(
                        count_tokens(text) > overlap_tokens
                        for text in (content.primary_text, content.translated_text)
                        if text is not None
                    ) or not can_fit_window([*candidate, segment]):
                        break
                    overlap = candidate
                current = overlap
            current.append(segment)
        if current:
            windows.append(assemble_window_content(current, translate=translate))
    return windows
