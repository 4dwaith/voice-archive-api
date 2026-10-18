"""Shared metadata rules; lengths count Unicode code points, not bytes."""

import unicodedata

from langcodes import tag_is_valid


def normalize_identity(value: str | None) -> str:
    """Build a duplicate-comparison key without changing the displayed text."""
    # An absent value and an empty value have the same identity.
    text = value or ""

    # Give canonically equivalent Unicode sequences the same representation
    # (for example, a composed accented letter and its decomposed equivalent).
    normalized = unicodedata.normalize("NFC", text)

    # Remove surrounding whitespace and collapse internal runs, including
    # tabs and line breaks, to a single space.
    collapsed = " ".join(normalized.split())

    # Use Unicode-aware case-insensitive matching; unlike lower(), casefold()
    # also handles equivalences such as German sharp S (ß) and "ss".
    casefolded = collapsed.casefold()

    # Case folding can change normalization, so restore NFC for the final key.
    return unicodedata.normalize("NFC", casefolded)


def validate_text(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("Text must contain valid Unicode") from exc
    return value


def validate_title(value: str) -> str:
    if not normalize_identity(value):
        raise ValueError("Title must contain non-whitespace text")
    return value


def validate_author(value: str) -> str | None:
    return value if value.strip() else None


def validate_language(value: str) -> str:
    try:
        valid = tag_is_valid(value)
    except RecursionError:
        valid = False

    if not valid:
        raise ValueError("Language must be a recognized language code, such as en or kn")

    return value
