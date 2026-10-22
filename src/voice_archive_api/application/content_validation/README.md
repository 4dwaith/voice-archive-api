# Content validation application module

This module owns canonical v1 schema, HTML/text, hierarchy, and prepared-asset
validation. It depends on the existing book/file/content models; it provides no
HTTP endpoints or publication transaction. Tests live under
`tests/application/content_validation/`: `test_html.py`, `test_schemas.py`,
`test_validation.py` (envelope/hierarchy/order), `test_assets.py` (in-memory asset
rules and lookup wiring), and `test_database_assets.py` (file-service integration).
The guide and contract artifacts are colocated here so implementation and
documentation can be reviewed together.

## Canonical book content v1

Phase 08 supplies the database model and validation library. Phase 09 supplies
MP3 preparation, atomic import/dry-run, manifest/purge endpoints, and the CLI.
There is no content mutation endpoint in this phase.

Start with [example.v1.json](docs/example.v1.json) and [schema.v1.json](docs/schema.v1.json).
The synthetic example includes front matter, nested and empty sections, headings,
prose and verse, nested emphasis, all six block classes, Unicode, multiple boxes
and pages, and backward audio positions. Its file IDs are placeholders for
prepared backend uploads: PDF 101 has two pages; root audio 201 has duration
7000 ms; nested audio 202 has duration 10 ms. Nested audio can be owned but never
overrides root playback. Replace those IDs with actual prepared files at import.

## Producer contract

The book is supplied separately by the future API path/CLI option. The envelope
contains integer `schema_version: 1`, nullable `source_pdf_file_id`, `sections`,
and a nonempty `segments` list. Unknown fields and unknown kinds are errors.
Source keys are unique within each entity type/book; they are producer identities,
not database IDs. Array order is not document order: sections use
`sibling_sequence` and segments use `sequence`, both nonnegative integers with
gaps allowed. Siblings (including roots) require unique order; their text ranges
must agree with that order. Keys can repeat between sections and segments.

Sections have `source_key`, nullable `parent_source_key`, kind (`part`, `chapter`,
`section`), title, sibling order, and optional `audio_file_id` as a linking
instruction. The resulting file owns the section via `files.section_id`;
neither sections nor segments store audio file FKs. Hierarchies have no fixed
depth limit. Missing references, cycles, and interleaved subtree ranges fail.
Empty sections are allowed. Front matter may have no section.

Segments have `source_key`, nullable `section_source_key`, sequence, kind
(`heading`, `prose`, `verse`), `html_content`, optional language, regions, optional timing,
and optional passive alignment status/metadata. Link the deepest structural
section. Section titles are ToC metadata; a selectable heading is a segment and
renders once. Do not supply `text_content`, `page_indexes`, paths, or breadcrumbs.
Language, if present, must be a recognized language tag, as for book metadata.
`SectionKind` and `SegmentKind` are shared Python string enums used by canonical
schemas and ORM columns; JSON still uses the lowercase strings shown above.
Database kind constraints enforce the same closed sets. `alignment_status` remains
an optional producer-defined string, with no closed vocabulary or review gate.

## Formatting and exact text

Supply exactly one explicitly closed outer `p` for prose/verse, or `h1`–`h6` for
headings. Only `strong`, `em`, and `br` are allowed inside. Normalize source `b`/`i`
to `strong`/`em`. `br`, `br/`, and `br /` spellings are accepted and serialize as
`<br>`. Other self-closing elements are invalid. All tags serialize lowercase,
attributes use double quotes, class tokens sort alphabetically, and decoded text
is consistently HTML-escaped. Strict HTML5 syntax validation runs first; the
limited-fragment parser then checks allowed tags, attributes, and explicit balancing
while deriving text. Malformed input is rejected, never repaired or silently stripped. Serialization is stable on repeated validation.

Only the outer block can have one `class` attribute. Tokens are `align-left`,
`align-center`, `align-right`, `no-indent`, `gap-before`, `gap-after`, without
repetitions and with at most one alignment. Other attributes/elements, comments,
declarations, hyperlinks, images, CSS, SVG, URLs, and embedded content fail.
Reader CSS will implement paragraph indentation, its `no-indent` exception,
alignment, and one em of extra block spacing for each gap class.

Derived text concatenates decoded text nodes and converts each `br` to LF.
There is no trimming, Unicode normalization, or outer-block newline. Whitespace,
NBSP, astral characters, combining sequences, and literal escaped markup survive.
Do not pretty-print whitespace inside fragments. Use LF, not CR/CRLF; NUL,
surrogates, other HTML control characters, and character references that browsers
would rewrite are rejected. Producers retaining plain text must compare it exactly
with the derived value before discarding their redundant text field. Gap classes
add styling, not text newlines.

Preparation maps text-bearing copyright, footnote, signature, caption, and literal
scene-marker categories to prose and reports each conversion. Report/skip empty,
image-only, or non-text separator blocks before canonical input. Unwrap source
links to visible text, report dropped images and unsupported layout, and translate
only supported source styles. The canonical validator never skips an entry.

## Geometry, media, and timing

Each region contains integer `page_index` and finite numeric `x0`, `y0`, `x1`,
`y1`: `0 <= x0 < x1 <= 1`, `0 <= y0 < y1 <= 1`. Coordinates use the displayed
PDF CropBox after intrinsic rotation, with a top-left origin. Page indexes are
zero-based physical indexes, not printed labels. Keep every rectangle in source
order, including multiple rectangles on the same page and multi-page segments.
An empty regions list means text-only locations. Referenced pages must be below
the exact prepared PDF's existing `page_count`; no server PDF processing is added.

Use integer milliseconds relative to the segment's root section audio. Supply
`narration_start_ms`/`narration_end_ms` together, `0 <= narration_start_ms < narration_end_ms`, and optional
`0 <= narration_seek_ms <= narration_start_ms`. End must not exceed that root audio's integer
`duration_ms`. Timed sectionless text fails. Nested audio is ignored even when
root audio is absent; there is no ancestor fallback. Untimed text remains valid
without audio. Timing intervals are half-open. Segments may move backward in
source time or switch root files: sequence alone controls document/playback order.
No global timestamp monotonicity or alignment confidence gate is imposed.

Asset validation accepts unowned prepared files, or the PDF already owned by this
book. Wrong kind, ownership by another book/section, reused audio IDs, missing
files, invalid duration metadata, and page/duration overflows fail. This is a
prospective validation snapshot; phase 09 must recheck under locks and require an
already-attached PDF to match the import PDF. Files are immutable prepared bytes;
file IDs never accept filesystem paths.

## Validation library and limits

```python
from functools import partial

from voice_archive_api.application.content_validation.validation import validate_content
from voice_archive_api.application.content_validation.assets import validate_referenced_assets
from voice_archive_api.book_files.service import get_prepared_assets

# Wire the session at the calling boundary, not inside application validation.
lookup = partial(get_prepared_assets, db)
validated = validate_content(json_bytes)  # schema, HTML/text, graph; no database
validate_referenced_assets(validated, lookup, book_id=book_id)  # one prepared-file query
# validated.content.segments[0].html_content / .text_content are ready for import storage.
# validated.playback_audio_id(section_source_key) resolves only root audio.
```

`validate_referenced_assets` collects media IDs and calls the supplied lookup once
(or not at all for text-only input). The file service owns SQLAlchemy access and
returns file-owned `PreparedAsset` snapshots with copied, read-only metadata;
missing IDs are omitted so validation can report their canonical input paths.
The lookup never claims files or commits. `validate_assets` also accepts an
in-memory map of those snapshots for producer tests. `ContentInput` defines the
structural schema; use `validate_content` for full canonical validation. Validation performs no database writes, UUID
assignment, upload, repair, or byte inspection. `ContentValidationError.issues`
contains code, JSON path, source key where available, and actionable detail;
at most 100 issues are returned. Schema errors use Pydantic codes; graph/HTML/asset
errors have specific codes such as `section_cycle`, `noncontiguous_subtree`,
`invalid_html`, `asset_owned`, `page_bounds`, and `root_audio_required`.

| Bound | v1 limit |
| --- | --- |
| UTF-8 JSON payload, checked before parsing bytes | 32,000,000 bytes |
| Sections / segments | 50,000 / 100,000 |
| Source key / section title | 256 / 2,000 Unicode code points |
| Sequence, sibling sequence, page index | 0–2,147,483,647 |
| Prepared file ID / timestamp milliseconds | positive ID / nonnegative time, at most 9,223,372,036,854,775,807 |
| Per-segment regions | 256 |
| Input and serialized HTML / derived text | 256,000 UTF-8 bytes / 128,000 code points |
| HTML nodes / inline nesting depth | 4,096 / 32 (hierarchy has no fixed depth) |
| Language / alignment status | 100 / 128 code points |
| Alignment metadata | Optional JSON object; finite numbers, valid UTF-8, and no NUL characters; covered by the overall payload limit |

JSON must be UTF-8 with unique object keys and finite numbers. Booleans/strings
are not coerced into integers, and coordinates are not coerced from strings.
Forest traversal/aggregation is iterative and linear; ordering costs
O(segments log segments + sections log sections). No ancestor-per-segment walk
is needed. Byte/count/string bounds apply before expensive graph work.

Regenerate the schema after changing the Pydantic models (the test suite compares
it to the committed file):

```powershell
.venv/Scripts/python.exe -c "import json,pathlib; from voice_archive_api.application.content_validation.schemas import ContentInput; pathlib.Path('src/voice_archive_api/application/content_validation/docs/schema.v1.json').write_text(json.dumps(ContentInput.model_json_schema(),indent=2)+'\n', encoding='utf-8')"
```

## PostgreSQL and lifecycle

Migration `0012_content_domain` adds only `sections` and `segments`, plus the three
all-or-nothing nullable Book import metadata fields and nullable `files.section_id`.
Composite hierarchy FKs enforce same-book references; native NULLS NOT DISTINCT
sibling uniqueness includes roots. Closed kinds, timing consistency, positive IDs,
source uniqueness, exclusive/kind-correct file ownership, and one audio per section
are constrained. Cycles, subtree ranges, HTML and geometry belong to full canonical
validation, not direct SQL. No individual content-edit API is provided.

`public.region_pages(jsonb)` is IMMUTABLE and reads only its argument. It rejects
malformed arrays/entries and non-integer, negative, or out-of-range page indexes
before casting. The stored generated `page_indexes` column returns sorted distinct
indexes, with a GIN index; producers cannot supply it. Changing helper semantics
requires explicitly recomputing stored results. Downgrade drops dependent columns
before the helper and refuses to discard existing audio files: remove them through
authorized lifecycle operations first. Downgrade removes imported text/structure;
back up content before rolling back.

Both owners participate in timestamp maintenance, deletion protection, unlinked
cleanup, and atomic file claims. Whole-book deletion now queues section audio as
well as cover/PDF bytes in the existing transaction and cascades content removal.
Direct section deletion (unsupported as a content API) detaches its files using
ON DELETE SET NULL and resets their grace-period timestamps, like direct book
deletion. Never unlink bytes before the database transaction commits. The existing
maintenance worker retries durable queued physical deletions.

## Local producer sample

`sample_books/out/paragraphs.formatted.json` is producer data. Map chapters to roots,
source categories to the closed kinds, source IDs to keys, one-based physical pages
to zero-based indexes, top-left point boxes to normalized displayed coordinates,
and seconds to integer milliseconds by an explicit rounding policy. Preserve all
96 multi-page blocks and passive alignment diagnostics. Chapter file IDs become
section linking instructions. Canonical adaptation and uploads remain phase 09.
Do not restore the omitted signup page; retain the existing formatting report.

A local manual check on 2026-10-22 validated all 2,611 supplied non-null HTML
fragments and found exact equality with all corresponding producer text fields;
14 image blocks had null HTML. This took about 0.26 seconds on the current host.
It checks formatting/text only, not canonical IDs, units, media, or alignment
accuracy. The sample's full text/audio is not copied into routine fixtures.
