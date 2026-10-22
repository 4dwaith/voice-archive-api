"""Asset checks consume exact prepared-file metadata, without inspecting media bytes."""

from collections.abc import Mapping

from voice_archive_api.logic.content_validation.validation import (
    MAX_ISSUES,
    ContentIssue,
    ContentValidationError,
    ValidatedContent,
)
from voice_archive_api.logic.files import FileKind, PreparedAsset


def validate_assets(validated: ValidatedContent, assets: Mapping[int, PreparedAsset], *, book_id: int) -> None:
    """Check a prospective create-only import. Caller must lock/recheck claims at commit."""
    content = validated.content
    issues: list[ContentIssue] = []

    def issue(code: str, path: str, key: str | None, detail: str) -> None:
        if len(issues) < MAX_ISSUES:
            issues.append(ContentIssue(code, path, key, detail))

    def get_file(file_id: int, kind: FileKind, path: str, key: str | None) -> PreparedAsset | None:
        record = assets.get(file_id)
        if record is None:
            issue("asset_not_found", path, key, "Use an existing prepared file ID")
        elif record.kind != kind:
            issue("asset_kind", path, key, f"Expected prepared {kind} file")
        elif record.section_id is not None or (
            record.book_id is not None and (kind != FileKind.SOURCE_PDF or record.book_id != book_id)
        ):
            issue("asset_owned", path, key, "File must be unlinked, or the PDF already owned by this book")
        else:
            return record
        return None

    pdf = (
        get_file(content.source_pdf_file_id, FileKind.SOURCE_PDF, "$.source_pdf_file_id", None)
        if content.source_pdf_file_id is not None
        else None
    )
    page_count: int | None = None
    if pdf is not None:
        prepared_page_count = pdf.metadata.get("page_count")
        if type(prepared_page_count) is not int or prepared_page_count <= 0:
            issue("pdf_metadata", "$.source_pdf_file_id", None, "Prepared PDF must have a positive integer page_count")
        else:
            page_count = prepared_page_count
    elif content.source_pdf_file_id is None and any(segment.regions for segment in content.segments):
        issue("pdf_required", "$.source_pdf_file_id", None, "Provide a prepared source PDF when segments have regions")

    audio: dict[str, PreparedAsset] = {}
    used_audio: set[int] = set()

    for i, section in enumerate(content.sections):
        if section.audio_file_id is None:
            continue

        path = f"$.sections[{i}].audio_file_id"
        if section.audio_file_id in used_audio:
            issue("duplicate_audio", path, section.source_key, "A file can belong to only one section")

        used_audio.add(section.audio_file_id)
        record = get_file(section.audio_file_id, FileKind.AUDIO_CHAPTER, path, section.source_key)
        if record is not None:
            audio[section.source_key] = record
            duration = record.metadata.get("duration_ms")
            if type(duration) is not int or duration <= 0:
                issue(
                    "audio_metadata",
                    path,
                    section.source_key,
                    "Prepared audio must have a positive integer duration_ms",
                )

    for i, segment in enumerate(content.segments):
        path = f"$.segments[{i}]"
        if page_count is not None:
            for j, region in enumerate(segment.regions):
                if region.page_index >= page_count:
                    issue(
                        "page_bounds",
                        f"{path}.regions[{j}].page_index",
                        segment.source_key,
                        f"page_index must be below the prepared PDF's page_count ({page_count})",
                    )

        if segment.narration_start_ms is None:
            continue

        root = validated.root_by_section.get(segment.section_source_key) if segment.section_source_key else None
        track = audio.get(root) if root is not None else None
        duration = track.metadata.get("duration_ms") if track else None
        if type(duration) is not int or duration <= 0:
            issue(
                "root_audio_required",
                path + ".narration_start_ms",
                segment.source_key,
                "Timed text requires root-section audio with a positive integer duration_ms; nested audio is ignored",
            )
        elif segment.narration_end_ms is not None and segment.narration_end_ms > duration:
            issue(
                "audio_duration",
                path + ".narration_end_ms",
                segment.source_key,
                f"narration_end_ms exceeds root audio duration_ms ({duration})",
            )
    if issues:
        raise ContentValidationError(issues)


def referenced_file_ids(validated: ValidatedContent) -> set[int]:
    """Return the prepared IDs a service must load before asset validation."""
    ids = {s.audio_file_id for s in validated.content.sections if s.audio_file_id is not None}
    if validated.content.source_pdf_file_id is not None:
        ids.add(validated.content.source_pdf_file_id)
    return ids
