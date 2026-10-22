"""Bounded pure schema/HTML/forest validation, separate from asset lookup."""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from voice_archive_api.logic.content_validation.schemas import MAX_PAYLOAD_BYTES, ContentInput

MAX_ISSUES = 100


@dataclass(frozen=True)
class ContentIssue:
    code: str
    path: str
    source_key: str | None
    detail: str


class ContentValidationError(ValueError):
    def __init__(self, issues: list[ContentIssue]):
        self.issues = issues[:MAX_ISSUES]
        super().__init__("; ".join(f"{i.path}: {i.detail}" for i in self.issues))


@dataclass(frozen=True)
class ValidatedContent:
    content: ContentInput
    # Includes every section, including empty nodes. Front matter has no section.
    root_by_section: dict[str, str]
    audio_by_root: dict[str, int]

    def playback_audio_id(self, section_source_key: str | None) -> int | None:
        root = self.root_by_section.get(section_source_key) if section_source_key is not None else None
        return self.audio_by_root.get(root) if root is not None else None


def validate_content(payload: bytes | dict[str, Any]) -> ValidatedContent:
    """Validate canonical input without database access or automatic repairs.

    Bytes are preferred at ingestion: the limit is checked before JSON parsing.
    A dict is convenient for producers/tests, with the same serialized byte limit.
    """
    try:
        raw = (
            payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        )
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ContentValidationError(
                [ContentIssue("payload_limit", "$", None, f"Input exceeds {MAX_PAYLOAD_BYTES} bytes")]
            )
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ContentValidationError):
            raise
        raise ContentValidationError(
            [ContentIssue("invalid_json", "$", None, "Expected finite UTF-8 JSON with unique object keys")]
        ) from exc

    try:
        content = ContentInput.model_validate(decoded)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_input=False, include_url=False)[:MAX_ISSUES]:
            loc = error["loc"]
            key = None
            if (
                len(loc) > 1
                and loc[0] in ("sections", "segments")
                and isinstance(loc[1], int)
                and isinstance(decoded, dict)
            ):
                entries = decoded.get(loc[0])
                if isinstance(entries, list) and loc[1] < len(entries) and isinstance(entries[loc[1]], dict):
                    candidate = entries[loc[1]].get("source_key")
                    key = candidate if isinstance(candidate, str) else None
            path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in loc)
            issues.append(ContentIssue(error["type"], path, key, error["msg"]))
        raise ContentValidationError(issues) from exc
    return validate_graph(content)


def invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def validate_graph(content: ContentInput) -> ValidatedContent:
    issues: list[ContentIssue] = []

    def add_issue(code: str, path: str, key: str, detail: str) -> None:
        if len(issues) < MAX_ISSUES:
            issues.append(ContentIssue(code, path, key, detail))

    sections = {s.source_key: s for s in content.sections}
    indexes = {s.source_key: i for i, s in enumerate(content.sections)}
    seen_sections: set[str] = set()
    seen_segments: set[str] = set()
    siblings: set[tuple[str | None, int]] = set()
    sequences: set[int] = set()
    children: dict[str | None, list[str]] = {None: []}

    for i, section in enumerate(content.sections):
        key, parent = section.source_key, section.parent_source_key
        path = f"$.sections[{i}]"

        if key in seen_sections:
            add_issue("duplicate_source_key", path + ".source_key", key, "Section source_key must be unique per book")
        seen_sections.add(key)

        if parent is not None and parent not in sections:
            add_issue("missing_parent", path + ".parent_source_key", key, "Parent source_key does not exist")

        order = (parent, section.sibling_sequence)
        if order in siblings:
            add_issue(
                "duplicate_sibling_sequence", path + ".sibling_sequence", key, "Sibling/root order must be unique"
            )

        siblings.add(order)
        children.setdefault(parent, []).append(key)

    for i, segment in enumerate(content.segments):
        key, owner = segment.source_key, segment.section_source_key
        path = f"$.segments[{i}]"

        if key in seen_segments:
            add_issue("duplicate_source_key", path + ".source_key", key, "Segment source_key must be unique per book")
        seen_segments.add(key)

        if owner is not None and owner not in sections:
            add_issue("missing_section", path + ".section_source_key", key, "Section source_key does not exist")

        if segment.sequence in sequences:
            add_issue("duplicate_sequence", path + ".sequence", key, "Document sequence must be unique per book")

        sequences.add(segment.sequence)

        try:
            segment.derive_html()
        except ValueError as exc:
            add_issue("invalid_html", path + ".html_content", key, str(exc))

    if issues:
        raise ContentValidationError(issues)

    # Iterative forest traversal is O(sections + segments), without an ancestry depth limit.
    roots: dict[str, str] = {}
    preorder: list[str] = []
    stack = [(key, key) for key in children[None]]
    while stack:
        key, root = stack.pop()
        roots[key] = root
        preorder.append(key)
        stack.extend((child, root) for child in children.get(key, []))

    if len(roots) != len(sections):
        key = next(key for key in sections if key not in roots)
        raise ContentValidationError(
            [
                ContentIssue(
                    "section_cycle",
                    f"$.sections[{indexes[key]}].parent_source_key",
                    key,
                    "Section ancestry must form a forest without cycles",
                )
            ]
        )

    # Rank, rather than sequence arithmetic, permits gaps in document sequence.
    spans: dict[str, list[int]] = {key: [len(content.segments), -1, 0] for key in sections}
    for rank, segment in enumerate(sorted(content.segments, key=lambda s: s.sequence)):
        if segment.section_source_key is not None:
            span = spans[segment.section_source_key]
            span[0], span[1], span[2] = min(span[0], rank), max(span[1], rank), span[2] + 1

    # Ensures that siblings are visited before their parents.
    for key in reversed(preorder):
        low, high, count = spans[key]
        if count and high - low + 1 != count:
            add_issue(
                "noncontiguous_subtree",
                f"$.sections[{indexes[key]}]",
                key,
                "Section and descendants must occupy one uninterrupted document range",
            )
        parent = sections[key].parent_source_key
        if parent is not None:
            span = spans[parent]
            span[0], span[1], span[2] = min(span[0], low), max(span[1], high), span[2] + count

    for group in children.values():
        previous_end = -1
        for key in sorted(group, key=lambda k: sections[k].sibling_sequence):
            low, high, count = spans[key]
            if count:
                if low <= previous_end:
                    add_issue(
                        "sibling_document_order",
                        f"$.sections[{indexes[key]}].sibling_sequence",
                        key,
                        "Sibling order must agree with subtree document order",
                    )
                previous_end = high

    if issues:
        raise ContentValidationError(issues)

    audio_by_root: dict[str, int] = {}

    for section in content.sections:
        audio_file_id = section.audio_file_id
        if section.parent_source_key is None and audio_file_id is not None:
            audio_by_root[section.source_key] = audio_file_id

    return ValidatedContent(content, roots, audio_by_root)
