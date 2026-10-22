# Voice Archive API

FastAPI backend using Python 3.14, SQLAlchemy, and PostgreSQL 18. Supports book
CRUD and pagination, optional cover/PDF links, file uploads, and range-enabled
file delivery. The frontend is a separate project; authentication is not
implemented yet.

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

| API | Purpose |
| --- | --- |
| `/api/v1/books` and `/{id}` | Create, list, retrieve, update, and delete books |
| `/api/v1/files/{kind}` | Upload and prepare a `cover` or `source_pdf` |
| `/api/v1/files/{id}` | GET/HEAD delivery with native byte ranges; DELETE unlinked files |
| `/api/v1/health` | Liveness without database access |
| `/api/v1/ready` | Database readiness; used by Compose |

Upload files first, then link their returned IDs through an explicit book
create/save. Upload responses contain `id`, `kind`, `byte_size`, `metadata`, and
`created_at`. Covers are normalized to WebP; PDFs retain their original bytes.
Derived dimensions/page counts are stored in JSONB metadata. Database checks
require cover `width`/`height` and PDF `page_count` to be positive integers in the
PostgreSQL `INTEGER` range. The upload response is a discriminated union on
`kind`, with required typed metadata fields for each variant. A kind registry
selects preparation, input size limits, delivery media type, and download filename.
Files hold an optional `book_id` foreign key, with one file per `(book_id, kind)`.
Book cover/PDF IDs are derived from those rows; their kind determines their role.
The service validates requested IDs and atomically claims unowned files.
Database constraints enforce ownership and the supported book file kinds.
Deleting a linked file row is rejected by a database trigger, including direct
SQL deletes. Book operations detach old files before deleting them and queueing
their bytes, all in the same transaction.
Default input limits are 25 MiB per cover and 1.5 GB per PDF, configurable through
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

The `maintenance` service runs three independent jobs at startup and daily at
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
one job. The default `--job all` runs all three sequentially, attempting each even
if another fails. Without `--once`, selected jobs run independently on their schedules.

## Errors, request IDs, and OpenAPI

Application errors use `{"error":{"code":"...","message":"..."}}`; validation
errors also include `issues`. Unexpected errors return a generic `internal_error`
500. Native file-range errors and the readiness response retain their existing
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

## Tests

```powershell
docker compose --profile test run --rm --build tests
docker compose --profile test stop db-test
```

The suite uses a separate PostgreSQL service, isolated databases, and temporary
file storage. Coverage is printed in the terminal.

- [Features](tests/features/) and [step implementations](tests/feature_tests/): executable behavior specifications.
- [Contracts](tests/contract/): request/response schemas and OpenAPI declarations.
- [Application](tests/application/): capability rules and integrations, including prepared-content validation.
- [Database](tests/database/): constraints, transactions, concurrency, and migrations.
- [File processing](tests/file_processing/) and [HTTP](tests/http/): file preparation/storage and request handling.

To save coverage reports on the host:

```powershell
docker compose --profile test run --rm -v "./reports:/app/reports" tests pytest --cov=voice_archive_api --cov-report=term-missing --cov-report=html:reports/htmlcov --cov-report=xml:reports/coverage.xml
```

For one quality command using the same test environment:

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

Under [src/voice_archive_api](src/voice_archive_api/), `domain/` groups related
models and services into `books/`, `content/`, and `files/`. These services
own database access for their capabilities. `application/` groups broader capabilities;
its `content_validation/` module owns prepared-content validation and its guide,
JSON Schema, and example. Each application module keeps one README alongside its
implementation and obtains persisted data through domain services. Its tests
mirror the module name under `tests/application/`.
`file_handling/` provides storage and delivery;
`api/` defines HTTP contracts; `core/` and `db/` provide configuration, dependencies,
and database resources. `main.py` wires the application together.

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

## Prepared content domain (phase 08)

The [canonical content guide](src/voice_archive_api/application/content_validation/README.md) documents the v1 JSON Schema,
complete synthetic example, HTML/text rules, coordinate/timing conventions,
validation library, resource limits, and sample verification. Migration
`0012_content_domain` adds sections/segments, Book import metadata, generated
indexed page lookup, and section-owned audio file invariants. Whole-book deletion
and maintenance include section audio. Audio preparation, content import/dry-run,
manifest/purge, and CLI endpoints are phase 09; current uploads still accept only
cover/PDF. Run migrations as usual before using the new models. A downgrade refuses
existing audio files to avoid silently losing their ownership or bytes.
