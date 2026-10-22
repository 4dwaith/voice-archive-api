# Content publication and purge

Phase 09 connects the phase 08 content model/validators to the API. Run existing
migrations through `0013_section_sequence`; the latter backfills section preorder
without changing existing content versions. The
[canonical preparation guide](../../logic/content_validation/README.md)
defines accepted input, HTML, geometry, timing, and resource bounds.

## Read published content

Phase 10 extends the existing `GET /api/v1/books/{id}/content` manifest with
section/segment counts and PDF/audio file metadata, explicit owners, and relative
delivery URLs. Cover metadata remains on the book response. No storage keys are
exposed. The strong content-state ETag continues to authorize conditional purge;
conditional GET/304 is not implemented for the manifest.

`GET /api/v1/books/{id}/sections?offset=0&limit=2000` returns `content_version`,
`items`, and nullable integer `next_offset`. Sections are flat, depth-first, parent
before child, ordered by their persisted `sequence`. Import derives this sequence
from the parent graph and sibling order, independent of input array order.
The response includes parent/source keys, sibling sequence, reading sequence,
owned audio file ID, and the first segment source key in each section's subtree
(null for empty subtrees). Default limit is 2,000; maximum is 5,000.

`GET /api/v1/books/{id}/segments?offset=0&limit=500` uses the same envelope and
returns complete HTML/plain text, typed ordered regions, generated physical PDF
page indexes, language, timing, and passive alignment data. Default limit is 500;
maximum is 1,000. Both endpoints use SQL `ORDER BY sequence LIMIT ... OFFSET ...`.
Offsets count rows, so gaps in either collection's sequences are supported.
Response sizes are bounded by these limits and the existing 32 MB import input
limit; derived plain text and response metadata can increase encoded size.

Both collections require `If-Content-Version: <UUID>` using the version from the
manifest. A shared dependency in the content API checks it under the book read
lock. A purged/unpublished or different version returns **412**
`content_version_mismatch`; missing or malformed headers and invalid offsets
return the ordinary **422** validation error. Missing books return **404**
`book_not_found`. The manifest remains readable without this precondition, so
clients can discover the current version. Book CRUD, uploads, file delivery,
and unrelated APIs do not require the header.

Follow `next_offset` until null, retaining the version header on every request.
No encoded cursors or section/page filters are included. Responses never silently
substitute a fresh version. Collection responses use `Cache-Control: no-store`;
Dexie manages offline data. Manifest ETags and purge's `If-Match`/412 contract
remain separate; 304 means Not Modified and is not used for invalidation.

Each manifest/collection read holds a shared book lock until its database session
ends. Import, purge, book edits/deletion take the corresponding write lock, so a
single response's version, rows, and file relationships remain consistent.
Separate paginated responses do not retain database transactions or snapshots.

Backend gzip middleware compresses eligible structured responses of at least
500 bytes at level 6 when requested, including `Vary: Accept-Encoding`.
GET/HEAD file delivery and requests with Range bypass compression. Native
PDF/audio URLs retain existing Range/If-Range semantics; CORS exposes ETag,
Accept-Ranges and Content-Range. Send URLs directly to PDF.js/native audio.

Stopping a structured read has no persistent server-side operation to resume.
Clients can restart safe GET requests, pinning them to the current manifest.
Mutation replay remains excluded. Apply migration `0013_section_sequence` before serving these reads. It backfills
existing section order and adds a nonnegative, book-unique sequence constraint;
no reimport or content-version change is required.

## Prepare media and publish

Upload PDFs with `POST /api/v1/files/source_pdf` and chapter MP3s with
`POST /api/v1/files/audio_chapter`, each as one multipart `file`. Use returned
file IDs in canonical JSON. Already-attached PDFs must match the input ID.
Audio must be unlinked and may be assigned to one section at any depth. Timed
segments require their root section's audio; nested-owned audio is ignored for
playback. All supplied audio must have positive measured duration metadata.

MP3 preparation preserves bytes and uses Mutagen's MPEG Layer III header probe,
rejecting missing/uncertain headers, other MPEG layers, and nonfinite/nonpositive
duration. Metadata includes integer `duration_ms` (nearest millisecond),
`sample_rate`, and `channels`. Xing/VBRI duration is used when present; otherwise
the probe estimates duration from bitrate and byte size. This is structural
inspection, not a full audio decode or a guarantee that every frame is intact.
Tags/artwork are skipped without loading them. No subprocess or shell is used.
The default input limit is 1.5 GB, configurable with
`VOICE_ARCHIVE_AUDIO_MAX_BYTES`; the existing overall multipart cap still applies.
GET/HEAD/Range delivery uses the existing file URL with `audio/mpeg`.

Send canonical JSON to `POST /api/v1/books/{id}/content-imports?dry_run=true`.
Success is 200 with `dry_run`, section/segment counts, and null `content_version`.
Dry-run checks publication preconditions and takes short-lived locks, but writes
nothing and reserves no assets. Publish to the same URL without `dry_run` for
200 with a fresh version UUID. Existing published content returns
409 `content_exists`, including identical input and dry-runs. Text-only imports
are supported; audio-only/zero-segment imports are not.

The book lock serializes import, purge, book edits, and deletion. Referenced files
are locked in ascending ID order and revalidated before claiming. Parent IDs are
allocated once; sections are inserted parent-first and segments in batches of
500. All graph rows, file ownership, and Book import metadata commit together.
There are no per-row commits. Failed imports leave prepared uploads unlinked for
the existing seven-day cleanup grace period.

Canonical JSON is limited to 32,000,000 received bytes before JSON parsing,
including requests without Content-Length. Other existing bounds include 50,000
sections, 100,000 segments, and 256 regions per segment. Schema/graph/media
errors return 422 `content_validation_error` with bounded `issues` containing
`code`, JSON `path`, nullable `source_key`, and `detail`. Oversized input returns
413 `content_payload_too_large`. Path/query validation retains the standard 422
`validation_error` envelope. Missing books return 404. Invalid MP3s return
422 `invalid_audio`; oversized uploads return 413 `file_too_large`.

## Import CLI

Install/sync the API project and supply the same `VOICE_ARCHIVE_POSTGRES_*`
configuration used by the backend. No running API server is required. For local
use, have uv load the environment file explicitly:

```powershell
uv run --env-file .env import-book-content --book-id 12 --json prepared.json --dry-run
uv run --env-file .env import-book-content --book-id 12 --json prepared.json
```

The CLI bounds/reads the input and calls the same pure canonical validator, then
opens a database session and calls `services.content.service.publish()` directly.
The service owns asset validation, locks, dry-run behavior, publication, and the
transaction for both API and CLI. There is no separate CLI publication implementation.
The CLI closes its session and disposes its engine on success or failure. Settings
come from process variables; the command does not load `.env` implicitly or run
migrations. Apply the existing migrations before importing.

Success prints the same JSON `ImportResult` shape as the API. Validation and domain
failures print structured JSON to stderr with their codes/diagnostics; local file
and configuration failures print concise messages. Exit codes: 0 success;
1 validation/domain/local/configuration error or database failure during dry-run;
2 invalid arguments; 3 database failure during publication, whose outcome should
be inspected before retrying. Database exception details are omitted to avoid
printing credentials. No automatic retry or purge is performed. The former
`--api-url`, `VOICE_ARCHIVE_API_URL`, and `--timeout` settings are no longer used.

A lost database connection during commit can leave the result uncertain; inspect
the content manifest before manually retrying. Cancellation around commit has the
same uncertainty. Transaction failures before commit roll back database changes.
For HTTP clients, a timeout/disconnection likewise does not prove rollback, since
the server may still finish. Explicit purge followed by import produces a new
UUID, even for the same payload. No checksum, retry ledger, historical revision,
or stale-create suppression is provided.

## Inspect and purge

`GET /api/v1/books/{id}/content` returns `state` (`empty`, `pdf_only`, or
`published`), `content_version`, `schema_version`, `imported_at`, and
`source_pdf_file_id`. Its quoted strong `ETag` is exposed through CORS. This is
the small manifest, not a canonical export. Phase 10 extends this same resource.
Book title, cover, and other descriptive metadata do not change its tag.

`DELETE /api/v1/books/{id}/content` requires that exact tag in `If-Match`.
Missing/empty headers return 428 `content_precondition_required`; stale, weak,
wildcard, or list values return 412 `content_precondition_failed` without changes.
Success is 204. Current empty-state purge is a no-op. Inspect again after an
uncertain purge result; a repeat using the old tag can return 412.

Purge deletes all text/sections, the PDF, and every section-owned audio file,
including nested audio. It preserves cover and descriptive metadata. Dependencies
are removed before owners are detached; file rows are deleted and byte deletion
is durably queued in the same transaction. No bytes are unlinked before commit.
The existing maintenance worker retries physical failures. Whole-book deletion
uses the shared `services/book_cleanup.py` helper and also removes the cover and book. There is no new
public cleanup-status endpoint.

Book PATCH may attach, replace, or clear a PDF while the book has no imported
content. Replacement/clearing uses the existing transactional file cleanup. Once
content is imported, any change to its PDF link (including attaching a PDF to
published text-only content) returns 409 `content_purge_required`; resubmitting
the unchanged PDF ID remains allowed. Cover edits remain available. Direct linked file
DELETE remains 409 `file_already_linked`.

## Local sample producer

`sample_books/prepare_canonical_content.py` converts the formatted local producer
data using an explicit map of uploaded IDs:

```json
{"source_pdf_file_id": 101, "audio_by_cid": {"c00": 201}}
```

Supply all selected chapter IDs. From the API directory:

```powershell
uv run python ../sample_books/prepare_canonical_content.py --media-map media.json --chapter c00 --output canonical.json --report conversion-report.json
```

Omit `--chapter` for the full book. The adapter verifies the local PDF's page
count, uniform dimensions, zero rotation, and CropBox origin before converting
top-left points and one-based pages. Front matter precedes chapter text; chapter
blocks follow producer sequence. It reports every skipped image/empty block and
source-kind conversion, compares HTML-derived text exactly, preserves multiple
regions and alignment diagnostics, and rounds seconds to milliseconds using
decimal round-half-up. It does not repair invalid intervals or restore the
omitted signup page. Use ordinary file APIs to prepare media; this adapter only
writes local JSON. The API still validates the exact referenced prepared assets.


## Executable behavior specifications

`tests/features/content/imports.feature` covers prepared publication, dry-run,
text-only content, matching PDFs, invalid schema/graph/media, duplicate and
concurrent imports, and linked chapter-audio deletion protection. Its valid
synthetic dataset includes child-before-parent input, multiple PDF regions/pages,
backward narration positions, passive alignment data, and nested audio too short
to narrate the text, demonstrating root-only audio resolution.

`tests/features/content/purge.feature` covers removal/queued cleanup, preservation
of cover and book details, missing/stale/weak/wildcard tags, fresh-import races,
PDF-only/empty books, and empty-to-PDF changes. Steps in
`tests/feature_tests/test_content.py` call the actual HTTP API and inspect isolated
PostgreSQL/storage state; no import, ownership, or purge services are mocked.
