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
| `/api/v1/cover-files` and `/api/v1/source-pdf-files` | Upload and prepare files |
| `/api/v1/cover-files/{id}` and `/api/v1/source-pdf-files/{id}` | GET/HEAD delivery with native byte ranges; DELETE unlinked files |
| `/api/v1/health` | Liveness without database access |
| `/api/v1/ready` | Database readiness; used by Compose |

Upload files first, then link their returned IDs through an explicit book
create/save. Covers are normalized to WebP; PDFs retain their original bytes.
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

PostgreSQL uses the `postgres-data` volume; files use `uploads-data`, mounted at
`/app/uploads`. `docker compose down` preserves both; `docker compose down -v`
deletes both. Changing `.env` credentials does not update an existing database's
users.

The `maintenance` service runs two independent jobs at startup and daily at
midnight UTC: one removes temporary files inactive for 24 hours; the other
removes unlinked prepared files older than seven days and retries pending
deletions. Replacing/clearing a book's file or deleting its book removes the file
record and queues its bytes for maintenance. Failed file deletions are retried
on later runs. For an immediate run:
`docker compose run --rm --no-deps maintenance python -m voice_archive_api.maintenance --once`.
Use `--once --job temporary` or `--once --job promoted` to run just one job.
The default `--job all` runs both sequentially, attempting each even if the other
fails. Without `--once`, the selected jobs run independently on their schedules.

## Tests

```powershell
docker compose --profile test run --rm --build tests
docker compose --profile test stop db-test
```

The suite uses a separate PostgreSQL service, isolated databases, and temporary
file storage. Coverage is printed in the terminal.

- [Features](tests/features/) and [step implementations](tests/feature_tests/): executable behavior specifications.
- [Contracts](tests/contract/): request/response schemas and OpenAPI declarations.
- [Database](tests/database/): constraints, transactions, concurrency, and migrations.
- [File processing](tests/file_processing/) and [HTTP](tests/http/): file preparation/storage and request handling.

To save coverage reports on the host:

```powershell
docker compose --profile test run --rm -v "./reports:/app/reports" tests pytest --cov=voice_archive_api --cov-report=term-missing --cov-report=html:reports/htmlcov --cov-report=xml:reports/coverage.xml
```

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

## Code and migrations

Under [src/voice_archive_api](src/voice_archive_api/), `books/` and `book_files/`
contain domain models and services; `file_handling/` provides storage and delivery;
`api/` defines HTTP contracts; `core/` and `db/` provide configuration, dependencies,
and database resources. `main.py` wires the application together.

Schema changes belong in [Alembic migrations](alembic/versions/). Generate them
on the host and review before applying:

```powershell
uv run --env-file .env alembic revision --autogenerate -m "describe the schema change"
```

The migration role needs permission to install PostgreSQL's bundled `pgcrypto`
extension, as provided by local Compose.
