# Voice Archive API

FastAPI backend using Python 3.14, SQLAlchemy, and PostgreSQL 18. Supports book
CRUD and pagination, optional cover/PDF links, file uploads, and range-enabled
file delivery, cookie JWT authentication and owner-scoped libraries. The frontend
login/guest interface is a separate review and is not implemented yet.

## Users and ownership administration (first review)

The schema, local commands, and backend authentication/access enforcement are
implemented. HTTP creates assign the authenticated user's ownership. Public demo
content is maintained through trusted local commands; the login/guest UI follows
in review 3.

Migration `0028_users_and_ownership` creates the disabled placeholder account
`Unassigned` and assigns all existing books and series to it. No migration
argument or account-creation step is required. From this directory with the
configured database environment:

```powershell
uv run --env-file .env alembic upgrade head
uv run --env-file .env manage-users create reader@example.com --display-name "Reader"
uv run --env-file .env manage-users reset-password reader@example.com
uv run --env-file .env manage-users publish-demo --book-id 12
uv run --env-file .env manage-users publish-demo --series-id 3
```

Accounts are identified by email. Creation and password reset validate the
address with email-validator, normalize it, and treat it case-insensitively.
Validation does not perform DNS lookups or verify mailbox ownership. Unicode
email addresses and internationalized domains are supported; plus tags and dots
are preserved. Display names are optional Unicode text and need not be unique.
Both commands prompt twice without echo and store an Argon2 hash through pwdlib.
Passwords are never command-line arguments or command output.

`Unassigned` is the disabled placeholder's display name. It has no email or
password, including on an empty database, and cannot be targeted by the
email-based password-reset command. Database constraints prevent an account
without an email from having a password or being enabled. Public signup and
mailbox verification remain future work.

`owner_user_id IS NULL` identifies public books/series. Demo designation publishes
a standalone book or an entire series atomically; publishing a single series
member is rejected. A public identity conflict rolls back the whole change.
Book title/author uniqueness applies separately to each user and to the public
collection. A deferred composite foreign key requires series/book ownership to
match, using a generated `ownership_key` (zero for public) solely for that
constraint. User deletion is restricted while books/series reference the account.

Files record `uploaded_by_user_id`. Linked-file access inherits the book's
ownership, directly or through a section; unlinked-file access belongs only to
the uploader. The uploader is retained when linking or unlinking. No file bytes, content versions, imported
content, preparation records or media links are changed by migration or demo
designation. Local commands are trusted operator tools, not public API endpoints.

Downgrade restores global title/author uniqueness and refuses conflicting data
transactionally. Resolve cross-owner duplicates before attempting a downgrade;
there is no automatic deletion or merge of books.

## Sign-in and API access (second review)

Set `VOICE_ARCHIVE_AUTH_SECRET` to a random secret of at least 32 characters.
Keep it stable across restarts and API workers. Without it, login returns 503;
there is no development authentication bypass. Compose passes the secret and
cookie settings from `.env`. Keep `VOICE_ARCHIVE_AUTH_COOKIE_SECURE=true` for
HTTPS, including the Caddy LAN deployment; use false only for local HTTP.
Use the same hostname for local frontend/API so SameSite cookies work across ports.

- `POST /api/v1/auth/login`: JSON `email` and `password`; returns ID, email and
  display name, and sets `voice_archive_session` as an HttpOnly, SameSite=Lax,
  host-only cookie scoped to `/api`. Passwords and JWTs are not returned in JSON.
- `GET /api/v1/auth/me`: current user or JSON null for a visitor without a cookie.
  Expired/invalid credentials or a disabled/missing account return 401, including
  when requesting public resources. Authentication is checked on every request.
- `POST /api/v1/auth/logout`: clears the cookie, including an invalid/expired
  cookie. Logout still requires a trusted request origin. It does not revoke a
  copied JWT before expiration; refresh/revocation infrastructure is out of scope.
- Tokens expire after `VOICE_ARCHIVE_AUTH_TOKEN_TTL_SECONDS` (default 43,200;
  allowed range 60-86,400). Login is limited to ten attempts per client IP per
  minute in each API process. This bounded in-memory limit is not a distributed
  rate limiter; an edge limit is appropriate if running multiple workers.

All unsafe `/api/` requests are checked before routing/body parsing. An `Origin`
must exactly match `VOICE_ARCHIVE_CORS_ORIGINS`; an explicit untrusted/null Origin
is rejected even if Fetch Metadata says same-origin. When Origin is absent,
the middleware validates the Referer's origin, or requires
`Sec-Fetch-Site: same-origin` if neither source header is present. Missing headers,
untrusted same-site subdomains, and direct navigations do not authorize writes.
Explicitly trusted development origins may be cross-origin/cross-site. No CSRF
token cookie or custom CSRF header is needed. This is browser CSRF protection;
non-browser clients still need valid credentials and may send a trusted Origin.
API responses prevent browser/shared caching of account-sensitive data.

Visitors can read public books, content and media. Users can also read their own
books; mutations require ownership. Inaccessible books, series and files return
404. Book/series lists and totals are filtered in SQL. Uploads record the signed-in
user. Only that user may read, delete or claim an unlinked file. Once linked,
the book's owner governs access, including audio range and HEAD requests.
Public books cannot be mutated through the ordinary API. Health/readiness and
OpenAPI remain independent of authentication.

Gemini Live tokens and library questions require login. Library-wide, series and
book retrieval use request-scoped database sessions, including source consistency
checks, so other users' private content is excluded before provider calls.

`AccessContext.is_write` distinguishes read and write access; `is_public()`
encodes whether public resources are eligible. Local commands and the maintenance
entry point explicitly create sessions with `is_trusted=True`. Missing context
grants guest access only, never unrestricted access. HTTP contexts are untrusted.

Migration `0029_file_uploaders` backfills linked files from the current book owner
(including section-owned audio); unlinked files use the reserved Unassigned user
ID 1. Legacy public files without a user owner also use Unassigned for provenance.
New HTTP uploads always override that local-command default with the uploader's
ID. This migration does not move bytes or alter book/content ownership.

Operational failures consumed by CLI, HTTP/provider fallbacks or maintenance are
logged with their causes, stack locations and operation/resource identifiers.
`core/logging.py` redacts configured credentials and omits SQL statements/parameters,
validation input values and traceback locals. Public HTTP errors remain generic;
operator details go to server logs or CLI stderr. Expected validation and normal
cancellation do not produce duplicate error reports. See the shared
[error-diagnostic convention](../AGENTS.md#error-diagnostics).

## Run

Install Docker Desktop with Linux containers. From this directory, copy
[.env.example](.env.example) to `.env` once and adjust the local values:

```powershell
Copy-Item .env.example .env
docker compose up --build -d --wait
```

Keep your existing `.env` on subsequent runs. Migrations run before startup.
The API starts at [localhost:8000](http://localhost:8000), with interactive
[API documentation](http://localhost:8000/docs) and the
[OpenAPI contract](http://localhost:8000/openapi.json). Re-run the Compose command
after code or dependency changes; Compose does not enable hot reload.

An optional external reranker is configured through `VOICE_ARCHIVE_RERANKER_BASE_URL`.
GPU model hosting is managed separately from this application. When configured,
passage search reranks all unique segment candidates with their summaries and
neighboring originals before selecting up to 25 target segments. Answer evidence
also retains their original neighbors, deduplicated in passage-group order;
summaries are used only for reranking. Whole-book answering bypasses reranking.
Timeout, unsupported input or invalid provider output preserves hybrid ordering
and logs the cause. `VOICE_ARCHIVE_RERANKER_MODEL`, `_TIMEOUT_SECONDS` and
`_MAX_INPUT_TOKENS` configure the model and bounds (defaults: Qwen3-Reranker-0.6B,
60 seconds across expansion/scoring, and 4,096 tokens per pair). Requests are
batched in groups of 32. A conservative UTF-8 byte bound reserves template space
and removes whole neighboring passages, then the summary, when needed; target
passages and questions are never truncated. No numerical relevance cutoff is used.

| API                                           | Purpose                                                              |
| --------------------------------------------- | -------------------------------------------------------------------- |
| `/api/v1/books` and `/{id}`                   | Create, list, retrieve, update, and delete books                     |
| `/api/v1/series` and `/{id}`                  | Create, list, read, rename and delete series                         |
| `/api/v1/files/{kind}`                        | Upload and prepare a `cover`, `source_pdf`, or `audio_chapter` MP3   |
| `/api/v1/books/{id}/content-imports`          | Atomic canonical import, with optional dry-run                       |
| `/api/v1/books/{id}/content`                  | Content manifest/ETag and conditional PDF/audio/text purge           |
| `/api/v1/books/{id}/sections` and `/segments` | If-Content-Version-pinned reads with integer offset/limit pagination |
| `/api/v1/files/{id}`                          | GET/HEAD delivery with native byte ranges; DELETE unlinked files     |
| `/api/v1/health`                              | Liveness without database access                                     |
| `/api/v1/ready`                               | Database readiness; used by Compose                                  |
| `/api/v1/live/token`                          | POST a single-use ephemeral Gemini Live credential (no-store)        |

For Live conversation, set `VOICE_ARCHIVE_GEMINI_API_KEY` on the backend only
and restart it. `VOICE_ARCHIVE_GEMINI_LIVE_MODEL` defaults to `gemini-3.8-live`,
which supports later asynchronous tools. No tools or app/book context are sent yet.
The browser connects directly to Gemini and needs HTTPS (or localhost), microphone
permission and working audio output. Start/Stop Live is the microphone icon at the
top left. Entering/browsing a reader preserves Live; Play stops it before narration.
Starting Live pauses narration. Stop releases capture/output; starting again creates
a new conversation. Session resumption is not implemented in 14.1.

Books expose nullable `series_id`, `series_title` and `series_index`. Positions
are 1-based and continuous. Book POST/PATCH appends a new member when its index
is omitted; an explicit index must be the next position. PATCH omission preserves
membership; null `series_id` clears both membership and index. Only the last
member can leave, move to another series or be deleted through book operations.
Writes that would leave gaps or change an occupied position return 409.

`PUT /api/v1/series/{id}/books` accepts the complete ordered array of book IDs,
for example `[12, 4, 9]`. It reorders/assigns positions atomically, detaches omitted
members and accepts unassigned books. An empty array clears membership. Duplicate
books or books already assigned to another series reject the whole request with
409. Missing books and books with a different owner return `book_not_found` (404).
Book writes referencing a series with a different owner return `series_not_found`
(404), as do unknown series IDs. Series detail returns `book_ids` in series order; series lists
retain ascending ID order and the book pagination limits.

Series titles retain display whitespace and use book title limits; duplicate
series titles are allowed. API deletion of a series atomically detaches all its
books without changing canonical content versions, media or files. Migration
`0015_series_order` backfills existing memberships in their prior book-ID order
and adds pairing/positive-position/deferred-unique constraints. Its restricted FK
requires explicit detachment before direct series deletion. Membership writes lock
series rows before book rows and revalidate after waiting. Catalog refresh updates
saved metadata normally; content reimport is unnecessary.

Upload files first, then link their returned IDs through an explicit book
create/save. Upload responses contain `id`, `kind`, `byte_size`, `metadata`, and
`created_at`. Covers are normalized to WebP; PDFs retain their original bytes.
Derived dimensions/page counts are stored in JSONB metadata. Database checks
require cover `width`/`height` and PDF `page_count` to be positive integers in the
PostgreSQL `INTEGER` range. The upload response is a discriminated union on
`kind`, with required typed metadata fields for each variant. A kind registry
selects preparation, input size limits, delivery media type, and download filename.
Files hold exclusive optional `book_id`/`section_id` owners. Cover/PDF belong to
books, and chapter audio belongs to sections, with one file per owner and kind.
Book cover/PDF IDs are derived from those rows; their kind determines their role.
The service validates requested IDs and atomically claims unowned files.
Database constraints enforce ownership and the supported book file kinds.
Deleting a linked file row is rejected by a database trigger, including direct
SQL deletes. Book operations detach old files before deleting them and queueing
their bytes, all in the same transaction.
Default input limits are 25 MiB per cover and 1.5 GB per PDF or MP3, configurable through
[.env.example](.env.example).
Book responses include nullable `cover_url` and `source_pdf_url` API paths for
delivery, alongside file IDs used for linking. Deleting an unlinked file returns
204 and queues its bytes for maintenance; linked files return 409. Missing file
records return 404.

## Configuration and data

Compose reads `.env` and injects process variables; the application does not load
`.env` itself. See [.env.example](.env.example) for database credentials, ports,
CORS origins, and upload settings. Local `.env` is excluded from Git and images.
The stack binds to localhost and uses development credentials.
Public-network deployment is blocked until authentication and authorization are
designed. Keep the supported Compose ports bound to loopback; CORS is not access
control. No authentication implementation is scheduled in the current reader phases.

PostgreSQL uses the `postgres-data` volume; files use `uploads-data`, mounted at
`/app/uploads`. `docker compose down` preserves both; `docker compose down -v`
deletes both. Changing `.env` credentials does not update an existing database's
users.

The `maintenance` service runs three file-cleanup jobs at startup and daily at
midnight UTC. Scheduled jobs may run concurrently:

- `temporary`: removes temporary files inactive for 24 hours.
- `promoted`: removes prepared-file records unlinked for more than seven days and
  retries queued byte deletions. Replacing/clearing a book's file or deleting its
  book queues its bytes in the same database transaction.
- `orphan`: scans all regular promoted files, checking their keys against
  the shared file table in bounded batches. Bytes last modified strictly more than
  seven days ago with no file record are deleted. Database lookup failures abort
  deletion of that batch; failed deletes are rediscovered on the next scan.

A database trigger initializes `unlinked_at` to `created_at` for new uploads,
clears it on attachment, and resets it to the database statement time on
detachment. Direct book deletion uses `ON DELETE SET NULL`, preserving files
for this grace period. Cleanup scans `(unlinked_at, id)`, so an old file detached
after a completed scan is still visited. API replacements, clears, and book
deletions continue to queue old files immediately.

The grace period protects uploads between promotion and database commit. Files
are immutable after promotion; operators must not reuse keys or restore old
files into active storage while orphan cleanup runs. This recovers orphan bytes
after crashes or failed immediate cleanup; filesystem and PostgreSQL writes are
not one atomic transaction. Full-directory scans are intentional for now; an
incremental filesystem layout is deferred pending possible object storage.

For an immediate run:
`docker compose run --rm --no-deps maintenance python -m voice_archive_api.maintenance --once`.
Use `--once --job temporary`, `--job promoted`, or `--job orphan` to select
one cleanup job, or `--job embeddings` to collect results and advance the embedding queue. The default
`--job all` attempts all four jobs even if another fails. Without `--once`, cleanup
runs daily and embedding collection runs every three minutes, both starting at
startup. `VOICE_ARCHIVE_EMBEDDING_COLLECTION_INTERVAL_SECONDS` controls the collection
interval. Selected jobs run independently on their schedules.

## Errors, request IDs, and OpenAPI

Application errors use `{"error":{"code":"...","message":"..."}}`; validation
errors also include `issues`. Unexpected errors return a generic `internal_error` 500. Native file-range errors and the readiness response retain their existing
contracts. HEAD responses have no body.
Unregistered application errors also use the generic JSON 500 envelope;
contract tests require every application error code to have a registered status
and appear in its domain's OpenAPI error responses at that status.

`asgi-correlation-id` assigns an `X-Request-ID` to every request, including errors
and CORS preflights. Valid incoming UUID4 identifiers are reused; absent/invalid
ones are replaced. CORS exposes the response header. Logging filters add the ID
to application and Uvicorn logs; outside requests it is `-`. Application error
logs record exception type and stack locations without exception text, source
statements, locals, or uploaded bodies. SQLAlchemy hides bound parameters.

The live `/openapi.json` is the frontend generation input. There is no committed
schema snapshot, export command, or stale-artifact check. Contract tests verify
operation IDs, error schemas, multipart/binary responses, nullability, and limits.

## PostgreSQL image and pgvector

Both `db` and `db-test` use `pgvector/pgvector:0.8.7-pg18-bookworm` instead of
`postgres:18-bookworm`. This is PostgreSQL 18 on Debian Bookworm with the pgvector
server library, control file and extension SQL already installed. No manual
installation inside a running container or custom database Dockerfile is needed.
The image comes from the [pgvector project](https://github.com/pgvector/pgvector#docker).
The Python `pgvector` dependency supplies SQLAlchemy integration; it does not
install the PostgreSQL server extension.

Migration `0026_pgvector` runs `CREATE EXTENSION IF NOT EXISTS vector` to enable
the installed extension in that database, then converts `window_embeddings.vector`
from `double precision[]` to native `vector` using the stored values. Embedding
IDs, hashes, batch request links and pending NULL vectors are preserved; no
re-embedding/provider request is required. The dimension check now uses
`vector_dims`. Dimensions remain row-specific rather than fixing every row at 768.
Native vectors use single precision, so conversion rounds values accordingly;
downgrading restores the array type but cannot recover the original extra precision.
Invalid/unrepresentable stored values abort the transactional migration rather
than being dropped. Downgrade leaves the extension installed for other consumers.

Changing an image does not delete a named volume. Keep the same PostgreSQL major
version, Compose project and `postgres-data:/var/lib/postgresql` mount. Before an
application database image replacement or migration, save a custom-format
`pg_dump` outside Docker and test restoring it to an isolated database. Do not use
`docker compose down -v`: that removes the named data volume. The conversion
rewrites/locks the embedding table, so schedule the application migration with
writers stopped. Deployment commands belong in `D:\voice_archive\deploy` to retain
the existing PDF/audio bind mounts.

The pre-conversion backup is in
`D:/voice_archive/backups/before-pgvector-20261108T234520Z/`: `voice_archive.dump`,
password-free globals, archive contents, SHA-256 and restore verification. The
timestamp is UTC (2026-11-09 in India). This is a database backup; external PDF,
cover and audio bytes on `H:/voice_archive_data` are not included in `pg_dump`.
After explicit approval, application migration `0026_pgvector` was applied on
2026-11-09 using a fresh restore-verified backup at
`D:/voice_archive/backups/before-pgvector-apply-20261108T235309Z/`. PostgreSQL remains
18.6 on the original volume, with pgvector 0.8.7 enabled. Preservation checks and
post-deployment application/media results are recorded in
[the chunk implementation record](../plan/16.5-dense-retrieval.md#application-pgvector-migration---2026-11-09).

## Tests

```powershell
docker compose --profile test run --rm --build tests
docker compose --profile test stop db-test
```

The suite uses a separate PostgreSQL service, isolated databases, and temporary
file storage. Coverage is printed in the terminal.

The shared `client` fixture signs in through the real login endpoint as
`test_user` (`reader@example.com`); authorization remains enabled. API-created
resources belong to that user. Tests that seed resources directly specify their
owner/uploader where needed. `guest_client` has no session cookie, and
`schema_only_client` serves documentation checks without a database.

- [Features](tests/features/) and [step implementations](tests/feature_tests/): executable behavior specifications. Content [import](tests/features/content/imports.feature) and [purge](tests/features/content/purge.feature) scenarios exercise real uploads, publication/dry-run, rejected input, concurrent import, and conditional cleanup.
- [Contracts](tests/contract/): request/response schemas and OpenAPI declarations.
- [Access](tests/access/): authentication, origin checks, and guest/owner/other-user resource access.
- [Logic](tests/logic/): pure validation, transformations, and dependency boundaries.
- [Services](tests/services/): integrations between services, persisted data, and pure rules.
- [Database](tests/database/): constraints, transactions, concurrency, and migrations.
- [File processing](tests/file_processing/) and [HTTP](tests/http/): file preparation/storage and request handling.

To save coverage reports on the host:

```powershell
docker compose --profile test run --rm -v "./reports:/app/reports" tests pytest --cov=voice_archive_api --cov-report=term-missing --cov-report=html:reports/htmlcov --cov-report=xml:reports/coverage.xml
```

For one quality command using the same test environment, with eight parallel
pytest workers and work-stealing scheduling matching **Tests (parallel)**:

```powershell
docker compose --profile test run --rm --build -v "./reports:/app/reports" tests python -m voice_archive_api.quality
```

This checks formatting and lint across all Python files, enforces strict
application types, and runs the PostgreSQL suite. Combined statement/branch
coverage must reach 90%; reports are written to `reports/htmlcov/index.html` and
`reports/coverage.xml`. No CI setup or application database is required. Format
on the host with `uv run ruff format .`; individual checks are `uv run ruff
format --check .`, `uv run ruff check .`, and `uv run mypy`.
Source, tests, and migrations all follow the same formatting and lint rules.
Ruff enables `E4`, `E7`, `E9`, `F`, `I`, `B`, `UP`, and `SIM`; mypy checks
application code with `strict = true`.

## Develop outside Docker

With Python 3.14 and uv installed:

```powershell
docker compose up -d --wait db
docker compose stop api
uv sync --locked
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn voice_archive_api.main:app --reload
```

uv injects `.env` into the process. For IDE runs, select the `.venv` interpreter,
use this directory as the working directory, and inject the same environment
variables.

## PyCharm Run/Debug

Open this directory as the project and use `.venv/Scripts/python.exe` (Python
3.14). The saved configurations in `.run/` use the project root as their working
directory:

- **Application** (FastAPI): runs `voice_archive_api.main:app` on
  `127.0.0.1:8000` with reload. Uvicorn loads `.env` through `--env-file`.
  Both Run and Debug use this configuration; the explicit application name is
  `app` because the app uses a custom FastAPI subclass.
- **Database migrations** (Python module): runs `alembic upgrade head`, loading
  `.env`. Run this before the app on a new database or after migration changes.
- **Tests** (pytest): runs the complete `tests` directory, loading `.env.test`.
  The native test tree supports navigating and rerunning individual failures.
- **Tests (parallel)** (pytest): runs the same suite using eight worker processes
  (`-n 8 --dist worksteal`) and a separate `.pytest-tmp-parallel` directory.
  Use this for full-suite runs and **Tests** for stepping through a test in Debug.
  Each worker uses the same test PostgreSQL service, with unique per-test databases.
  More workers can increase database contention; benchmark before raising the count.

Local measurements on a Ryzen 7 7700X (8 cores / 16 threads, 64 GB RAM) put
full-suite wall time at about 36 s with 2 workers, 21 s with 4, and 15–16 s
with 8, 12, or 16. All 546 tests passed at each count. Eight is the default
because additional workers provided little improvement. These measurements include
Python/pytest startup and depend on current machine load; remeasure after major
suite or hardware changes. Keep a fixed worker count for predictable resource use.

Keep PostgreSQL running with `docker compose --profile test up -d --wait db db-test`.
Stop the Compose `api` service before running **Application** to free port 8000.
The application and tests run in the host interpreter; only their databases run
in Docker.

Create the ignored `.env.test` locally with these variables, using the
`VOICE_ARCHIVE_TEST_POSTGRES_*` credentials and port from `.env`:

```dotenv
VOICE_ARCHIVE_ENVIRONMENT=test
VOICE_ARCHIVE_POSTGRES_HOST=127.0.0.1
VOICE_ARCHIVE_POSTGRES_PORT=5433
VOICE_ARCHIVE_POSTGRES_USER=voice_archive_test
VOICE_ARCHIVE_POSTGRES_PASSWORD=voice_archive_test
VOICE_ARCHIVE_POSTGRES_DB=voice_archive_test
```

The development `.env` targets `voice_archive` on port 5432. Tests require
`voice_archive_test` and create/drop separate randomly named databases per test.
Credentials stay in the ignored environment files, outside the saved configurations.

## Code and migrations

Under [src/voice_archive_api](src/voice_archive_api/), code is grouped by responsibility:

| Package           | Responsibility                                                                                                                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `logic/`          | Pure rules, validation, transformations, enums, and data snapshots; no database, service, HTTP, or storage implementation imports         |
| `services/`       | Book/content/file operations, database transactions, file preparation and cleanup; service input/result schemas stay with each capability |
| `db/models/`      | SQLAlchemy models in `book.py`, `content.py`, and `file.py`; the package registers complete metadata for sessions and Alembic             |
| `api/`            | HTTP routes and error handling; HTTP response schemas live in `api/schemas/`                                                              |
| `cli/`            | Local commands calling services directly                                                                                                  |
| `file_handling/`  | Storage and streaming mechanics                                                                                                           |
| `core/` and `db/` | Configuration, dependencies, logging, and database resources                                                                              |

`main.py` wires the application together. APIs/CLI call services; services load
and lock data, call pure logic, and persist the results. Pure logic receives values
and prepared-asset snapshots rather than sessions or lookup callbacks. Shared
content enums and file kinds/snapshots live in `logic/`, so validation does not
import ORM models. Database constraint names live in `db/constants.py`.

The [logic guide](src/voice_archive_api/logic/README.md) and
[service guide](src/voice_archive_api/services/README.md) explain these boundaries.
Content validation keeps its JSON Schema/example and guide alongside the code;
tests mirror it under `tests/logic/`. Database-backed asset integration tests live
under `tests/services/content/`. Existing database, HTTP, and feature tests retain
their responsibility-based locations.

Migration `0008_unified_files` combines the legacy cover/PDF tables. It retains
storage keys and metadata, shifts PDF IDs above the cover ID range, and updates
book foreign keys in the same transaction. Clients must use the new file URLs
returned by books. The migration resets the shared cleanup scan boundary;
pending deletions remain intact. A downgrade requires valid cover dimensions and
PDF page counts in metadata because the legacy tables require those columns.

Migration `0009_file_ownership` moves book links onto `files.book_id`, backfills
`unlinked_at` for unattached uploads, installs the ownership timestamp trigger,
and resets the cleanup boundary. It refuses legacy links whose kind disagrees
with their book field. Downgrading restores the book-side links.

Migration `0010_protect_linked_files` adds a database trigger rejecting deletion
of file rows whose `book_id` is set. Detachment must happen before deletion.

Migration `0011_file_metadata_constraints` restores required positive integer
metadata checks for both kinds. It validates existing data and refuses to apply
if any row is invalid, leaving those rows intact for correction.

Schema changes belong in [Alembic migrations](alembic/versions/). Generate them
on the host and review before applying:

```powershell
uv run --env-file .env alembic revision --autogenerate -m "describe the schema change"
```

The migration role needs permission to install PostgreSQL's bundled `pgcrypto`
extension, as provided by local Compose.

## English summaries

After applying the normal migrations, generate or clear all segment, section and
book summaries directly through the CLI (no API server is required):

```powershell
uv run --env-file .env summarize-book generate --book-id 34
uv run --env-file .env summarize-book generate --book-id 34 --root-section-id 120
uv run --env-file .env summarize-book generate --book-id 34 --root-section-id 120 --log-level DEBUG
uv run --env-file .env summarize-book generate --book-id 34 --root-section-id 120 --effort high
uv run --env-file .env summarize-book clear --book-id 34
```

Summary generation logs failed attempts with their original exception tracebacks
to stderr. Use `--log-level DEBUG` to also log complete model responses before
validation, including responses that fail validation. This enables DEBUG logging
for `voice_archive_api` while keeping third-party libraries at WARNING. The
default application log level is INFO; model responses are not logged at that level.

`--root-section-id` selects one root section belonging to the book. It generates
and atomically publishes that root's entire subtree, including nested section and
passage summaries and required passage translations, changing the content version
once. Other roots, sectionless passages and the book summary are left unchanged.
Run consecutive roots in canonical order; each uses stored earlier-root summaries
and the immediately previous root's full text. A whole-book run still generates
all roots, sectionless passages and the book summary.

Both generation modes require nonempty book summaries for all earlier books in
the series. Root generation also requires nonempty root summaries for all earlier
roots. Missing prerequisites fail before provider work or creation of a run.
Generation fails if later roots (for root mode) or later books in the series
(both modes) contain any book, section or passage summaries, including partially
summarized scopes. The error identifies the blockers. Explicitly accept the risk
of stale downstream output with `--allow-stale-downstream`; this leaves later
summaries unchanged and reports affected scopes. It does not bypass prerequisites
or other validation. Both new flags apply only to `generate`.

Summary generation and its cache calls use the server-only
`VOICE_ARCHIVE_GEMINI_BATCH_API_KEY`, falling back to `VOICE_ARCHIVE_GEMINI_API_KEY`
when the batch key is unset or empty. Live and book questions continue to use
`VOICE_ARCHIVE_GEMINI_API_KEY`. Generation reuses `VOICE_ARCHIVE_GEMINI_TEXT_MODEL`
and the existing Gemini input-token/timeout settings. Each root supplies complete
canonical context; requests batch targets for an estimated 6,000-token response,
with background from earlier book/root summaries and the previous root's full text.
Background book/root summaries contain only titles and summaries. Previous-root
text is one plain-text block with section headings inline and passages in canonical
reading order; provider-visible background has no IDs, version identifiers or target metadata.
The book summary uses full canonical book text. Background clarifies references;
target summaries are not derived from child summaries. Progress goes to stderr,
and stdout reports the resulting book ID/content version as JSON.
Before preparing summaries, the existing `langcodes` parser resolves the declared
`Book.language` to one primary language. English aliases such as `eng`, `en_US`
and `en-US` resolve to `en`; the stored declaration is preserved for provenance.
Unresolved declarations (`und`, `mul`, `mis`, `zxx`) require a single primary book
language before any provider work or new run is created. The resolved language
controls the prompt, translation allowance and required output consistently.
For a book whose resolved primary language is not `en`, each passage target also
produces a full faithful English translation in `segments.english_translation`.
English-source passages and all book/section targets return no translation.
Translations preserve detail, order, names, attribution and uncertainty; contextual
clarification belongs in the separate summary. This internal field is stored for
later retrieval preparation; current public content/Live response shapes are unchanged.
Output checks enforce target coverage, required translations, nonempty text,
absence of NULs and the summary length limit. They do not guess language
from script proportions or certify translation fidelity. Faithfulness and English
prose remain prompt requirements, with representative human quality review when
real provider checks are approved; no second-model judge is called automatically.
Translation text adds an uncapped allowance of 64 tokens plus half the canonical
passage character count to the working output estimate. This remains a heuristic;
it does not prove actual output size or translation fidelity. A very long passage
still forms one target and can exceed provider output capacity.
Source uses one reading-ordered `fragments` array of `{id, parent_id, kind, text}`;
kind is `section` (heading) or `passage`, and parent IDs always name sections.
Fragment identities include kind because section/passage IDs can overlap. Passage
fragments map to the existing segment summary targets. Each target supplies an
inclusive zero-based `end_fragment_index`; instructions forbid later evidence,
including later identity revelations, and prioritize relevant preceding text.
Section cutoffs include their entire subtree. Targets are requested in cutoff
order, with passages before enclosing summaries at the same cutoff. This remains
a prompt rule: full-root batching exposes later text, so semantic isolation cannot
be guaranteed by the cutoff metadata or structural output validation.
`--effort low|medium|high` selects reasoning effort and defaults to `low`.
Medium and high effort are recorded in the run's policy identifier, so changing
effort permits regeneration rather than skipping an existing result. Existing
policies without an effort suffix represent low effort.
`--retries 0..2` defaults to one retry per batch;
oversize context is not retried. Database writes are never retried automatically.
Exit 1 means invalid input, unavailable source/provider or stale work; exit 3 means
a database failure whose commit outcome should be inspected before retrying.

Summary batches target approximately 12,000 output tokens, including an allowance
for complete passage translations when needed. This is a batching estimate;
requests use the model's reported full output ceiling independently of that target.
English books use summary-only instructions and an output schema without translation
fields. Non-English books add translation instructions and passage translation fields.
Whole-book answers retain their existing response cap.

Multi-request roots share an explicit provider cache when their context meets the
configured model's 4,096-token caching minimum. Scopes with fewer than two
unfinished batches skip cache preparation. Caches start with a 20-minute TTL, renew before another request would
approach expiry, and are explicitly deleted when that scope finishes or fails.
An unusually long configured request timeout can require a longer renewal.
Cache preparation/renewal failures use full canonical context directly; generation
retries also stop using the cache. Failed cleanup leaves the TTL as a fallback.
This uses ordinary generation requests, not the asynchronous Batch API.

Migration `0021_summary_checkpoints` adds `book_summary_batch_checkpoints`.
After a batch passes all output validation, its summaries and translations are
committed as a checkpoint before proceeding. A new invocation automatically
reuses checkpoints from compatible failed runs, including expired runs recovered
by admission, while generating only unfinished batches. Compatibility requires
matching source/background fingerprints, selected scope, downstream override,
model and effort policy; each reused request must also match the exact prompt,
output schema, context and targets. Restored batches are validated again and
reported as `reused checkpoint`. Fully restored scopes make no provider calls.
Each invocation has a new run ID and records only its own provider token usage;
previous invocations retain their original measurements. Checkpoints remain
associated with the run that generated them. The clear job deletes all of a
book's checkpoints as well as its published output, providing a fresh start.

Publication waits until the complete selected scope, including every required passage
translation, passes validation. It stores summaries and translations
atomically and changes the book content version once; a clear removes both and
changes the version when either was present. Policy `reader-context-english-v7`
requires summaries to preserve attribution, uncertainty and source specificity,
and translations to preserve source errors and reference values without silent
corrections. Earlier runs are eligible for regeneration under this policy. The nullable field
requires migration `0017_passage_translations`; scoped runs require
`0018_summary_scopes`. `book_summary_runs` records the selected root (null for a
whole book), the downstream override, source fingerprints, running/completed/
failed state, timestamps and model/policy/source-language provenance. Before any
provider work, generation exits successfully if a job is already running or the
accepted summaries match the current source, background, model and policy. Clearing
summaries permits regeneration even when source text is unchanged.

Migration `0019_summary_token_usage` adds per-run generation token totals:
`input_tokens` (including cached input), `cached_input_tokens` (a subset of input),
`output_tokens` (generated text), `thinking_tokens`, and the provider-reported
`total_tokens`. Each response is recorded in its own transaction before finish
reason, JSON, coverage or translation validation, so rejected responses and
retries remain counted even when generation fails or publication rolls back.
`generation_response_count` counts returned generation responses;
`missing_usage_response_count` flags responses with missing usage or missing
input/total counts. Missing optional cached/output/thinking counts contribute zero.
Totals with missing usage are partial. Requests that time out or fail without a
response cannot be measured here. Preflight token estimates and cache creation
or storage charges are not included. Migration `0020_required_summary_usage`
backfills historical NULL values to zero and makes all usage fields mandatory
with zero defaults. A historical zero means no recorded usage.

For example, inspect recent measurements with:

```sql
SELECT root_section_id, state, policy,
       input_tokens, cached_input_tokens, output_tokens, thinking_tokens, total_tokens,
       generation_response_count, missing_usage_response_count
FROM book_summary_runs
WHERE book_id = 34
ORDER BY started_at DESC;
```

A new invocation marks running jobs at least four hours old failed and can start
a replacement. Recovery runs only on invocation, using short SQLAlchemy transactions
and the existing book write lock; no advisory lock, heartbeat or scheduler is used.
The fingerprint covers selected canonical text/structure/IDs, relevant book metadata, series
membership and earlier-book summary inputs, excluding content versions and generated
current-book summaries outside a root's earlier-root background. Root fingerprints
also include earlier-root summaries and the previous root's canonical text. Inputs
and downstream blockers are rechecked at publication under the existing book/series
write locks. A completed root cannot skip book generation or another root; subsequent
overlapping publications supersede its eligibility for skipping. Clear/purge invalidate
running jobs; replaced workers stop before later provider work and cannot publish.
Failures preserve accepted data. Purge removes summaries with canonical content.
Generate books in series order to provide required earlier-book summaries;
changed background dependencies reject late work, even with the downstream override.
Summary reads use the existing catalog/content endpoints and client version
refresh, including normal stale-position/reference and pagination rebuild rules.
Whole-series summaries are deferred.

## Retrieval windows (16.4 checkpoint 1)

```powershell
build-book-windows dry-run --book-id 34
build-book-windows rebuild --book-id 34
build-book-windows clear --book-id 34
```

Apply migration `0022_retrieval_windows` through the normal migration workflow
before using this CLI. The CLI always uses Google's SDK Gemma 3 tokenizer, selected
with its fixed `gemini-2.5-flash` tokenizer alias. This name does not change the
configured embedding model. On first use, the SDK downloads the pinned tokenizer
data from GitHub, validates its SHA-256 and caches it in the system temporary
directory's `vertexai_tokenizer_model` folder. A fresh container or cleared cache
requires another download. Token counting then runs locally; book text is not
uploaded and no model API is called. There is no tokenizer option or environment
setting. Counts are proxy estimates; the conservative budgets retain headroom
below Embedding 2's input limit.

Rebuild atomically replaces all windows and their planned embedding rows. It packs
whole segments within contiguous section runs, assembles original and accepted
English translations, and creates embedding rows with current input hashes and
NULL vectors. Settings read `VOICE_ARCHIVE_GEMINI_EMBEDDING_MODEL`,
`VOICE_ARCHIVE_GEMINI_EMBEDDING_DIMENSIONS`, and `VOICE_ARCHIVE_EMBEDDING_INPUT_FORMAT`
from the environment; `.env.example` supplies `gemini-embedding-2`, 768 and
`retrieval-document-v1`. The input-format setting identifies the document-format
version; the implemented text format remains `title: {path} | text: {text}`.
Non-English books require translations
from `summarize-book` first. English regional variants need no translation.

Complete-input budgets include paths, document formatting and a 16-token reserve:
1,000 tokens by default, 1,500 for Kannada. `--window-tokens` accepts 32..4096;
`--overlap-tokens` defaults to 125 and reuses whole segments only when they fit.
Both representations must fit. An oversized segment fails without splitting or
truncation and preserves existing data. Dry-run reports assembled windows without
writes. JSON goes to stdout and errors
to stderr. Clear/purge/delete remove windows and cascade to their embeddings.
Canonical content and client `content_version` are unchanged. Window rebuilding
makes no provider calls and stores no windowing job records.

## Embedding batch queue (16.4)

```powershell
submit-book-embeddings --book-id 34
```

Apply migrations through `0025_batch_state_constraints` first. The command durably queues
this book's current window embeddings and advances the oldest queued embedding
work if the global batch slot is idle. It uses `VOICE_ARCHIVE_GEMINI_BATCH_API_KEY`,
falling back to `VOICE_ARCHIVE_GEMINI_API_KEY`. JSON output includes the requested
`book_id`, its queue `state` and any newly submitted `batches` (each identifies its
own originating book). An empty batch list can mean the request is waiting behind
other accepted work. Repeating the command while queued/running preserves its
request time and retry budget. Repeating a failed/completed request starts a fresh
retry budget; an already-complete book needs no provider calls.

Maintenance collects results first, then submits at most one next group. One
outstanding batch globally includes uncertain submissions, across books and
operation kinds. Pre-existing open embedding batches also block admission until
collected/reconciled. No project/model capacity accounting is performed. Each
provider batch contains one stored model, with dimensions specified per request.
Only the selected group receives batch/request rows; remaining NULL vectors are
the pending work. Successful vectors are excluded from future attempts.

`VOICE_ARCHIVE_EMBEDDING_BATCH_TOKENS` defaults to 150,000 estimated input tokens.
The fixed Gemma 3 proxy tokenizer (`gemini-2.5-flash` SDK alias) counts complete
formatted inputs, with 16 tokens of reserve per request. Initialization/download
occurs outside database transactions; counting is local. This leaves substantial
headroom below the observed 500,000-token provider quota, but estimates do not
guarantee admission. Lower the budget if the provider still rejects it. A single
input exceeding the configured batch budget fails the book job without truncation.

`VOICE_ARCHIVE_EMBEDDING_MAX_ATTEMPTS` defaults to three attempts per embedding
ID/current hash within the queued request. Upload failures, rejected batches,
reconciled unmatched submissions and explicit failed results consume attempts.
Uncertain outcomes retain the global slot until reconciliation. Automatic attempts
occur on maintenance ticks; they stop when the budget is exhausted and mark the
book job failed. Other queued books can then proceed. Invalid stored input also
fails its book job with a diagnostic. Input-validation and expected book lookup/
locking failures are contained per book: the same scan continues to healthy queued
books without creating batch/request records for the failed input. Shared database
failures still abort the scan, and uncertain provider work retains the global slot.
Fix the cause and rerun the CLI to retry.

Queue and embedding batch states are defined in `logic/batches.py` as `StrEnum`
values, following the existing file-kind convention. SQLAlchemy validates state
strings on writes and database check constraints reject invalid direct SQL writes.
`db/operations.py` contains the PostgreSQL lock, wall-clock, identity-sequence and
driver-diagnostic details; its advisory-lock comment explains cooperative locking
and transaction lifetime.

The shared `book_batch_jobs` table records `(book_id, kind)`, state, request time
and active batch UUID. `services/batch_queue.py` supplies shared enrollment,
transactional queue locking and batch-ID-specific slot release. Future summary
batching can reuse this coordination, with its own input/result handling; current
summarization behavior is unchanged. Operational rows survive book deletion until
outstanding work is collected. Queue transactions acquire the global advisory
lock before domain/queue row locks. Provider I/O holds no database locks. Request
mappings retain submitted embedding IDs/hashes through source rebuild/deletion.
The file-based SDK path uploads UTF-8 JSONL with request UUID keys and records the
uploaded file name. It neither rebuilds windows nor generates translations/summaries.

Upload failure before batch creation and explicit provider rejection release
claims for retry. Rejection diagnostics include Google's HTTP code, status and
message, with configured API keys redacted. A lost
creation response, server failure or interrupted local publication leaves the
batch in `submitting` with active claims. The error reports its local UUID, also
used as Google's display name. Maintenance reconciles `submitting` batches after
`VOICE_ARCHIVE_EMBEDDING_SUBMISSION_GRACE_SECONDS` (default 900 seconds). A complete
provider listing attaches a unique matching display name, or marks an unmatched
local job failed and releases its claims. Failed/incomplete scans and ambiguous
matches retain claims. Reconciled batches are polled on the next collection run.
The grace period is a practical recovery policy, not an idempotency guarantee:
late provider visibility or a still-running submitter can permit duplicate work.
Completed groups keep their vectors if a later group fails.
Uploaded files can remain until provider expiry. Batch creation sends a file
reference instead of the full book payload. Exit code 1 reports configuration/input/
submission failure; exit code 3 reports a database failure.

Maintenance polls submitted batches and downloads keyed JSONL only after success.
It validates finite, nonzero vectors and dimensions, and publishes only to existing
embedding IDs whose current input hashes still match. Vectors, terminal batch state
and claim/global-slot release commit together. Repeated/concurrent collection is safe; deleted
or changed targets are skipped. Explicit request errors leave their vectors empty,
release claims and mark the batch `completed_with_errors`. Failed/cancelled/expired
provider batches release claims and become `failed`. Provider calls hold no database
locks. Malformed, incomplete or unavailable output retains the batch/claims for a
later collection attempt and logs the cause; expired output can require operator
intervention. No provider file cleanup is performed.

For a one-off collection and queue advance: `python -m voice_archive_api.maintenance --once --job embeddings`.
No `--book-id=all`, conversion of summary generation to provider batches, summary
embeddings or answer changes are included in this review.

## Prepared content domain (phase 08)

The [canonical content guide](src/voice_archive_api/logic/content_validation/README.md) documents the v1 JSON Schema,
complete synthetic example, HTML/text rules, coordinate/timing conventions,
validation library, resource limits, and sample verification. Migration
`0012_content_domain` adds sections/segments, Book import metadata, generated
indexed page lookup, and section-owned audio file invariants. Whole-book deletion
and maintenance include section audio. Phase 09 now implements audio preparation,
content import/dry-run, manifest/purge, and the CLI calling the content service directly. See the
[publication and purge guide](src/voice_archive_api/services/content/README.md) for
commands, limits, errors, cancellation, sample conversion, and media lifecycle.
PDF links can be replaced or cleared through book PATCH until content is imported.
After import, changing the PDF requires conditional content purge. Run migrations as usual before using the new models. A downgrade refuses
existing audio files to avoid silently losing their ownership or bytes.
