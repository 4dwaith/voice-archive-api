"""Closed canonical section and segment kinds, shared with persistence."""

from enum import StrEnum


class SectionKind(StrEnum):
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"


class SegmentKind(StrEnum):
    HEADING = "heading"
    PROSE = "prose"
    VERSE = "verse"
