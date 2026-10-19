# Voice Archive API

FastAPI backend for a book library, using Python 3.14, SQLAlchemy, and PostgreSQL 18.
Currently supports book metadata CRUD and paginated listing. File uploads and
reading features are not implemented yet. The frontend is a separate project.

## Run

Install Docker Desktop with Linux containers. From this directory, copy
[.env.example](.env.example) to `.env` once and adjust the local values:

```powershell
Copy-Item .env.example .env
docker compose up --build -d --wait
```

Keep your existing `.env` on subsequent runs. The API starts at
[localhost:8000](http://localhost:8000); migrations run automatically before startup.
Re-run the Compose command after code or dependency changes; source hot reload
is not enabled in the current Compose configuration.

| Endpoint | Purpose |
| --- | --- |
| `/docs` | Interactive API documentation |
| `/openapi.json` | Generated API contract |
| `/api/v1/books` | Create and list books |
| `/api/v1/books/{id}` | Retrieve, patch, or delete a book |
| `/api/v1/health` | App liveness, without database access |
| `/api/v1/ready` | Database readiness; used by Compose |

## Configuration and storage

Compose reads `.env` and injects settings into each container. The application
reads process environment variables only. Required database credentials use
`VOICE_ARCHIVE_POSTGRES_USER`, `VOICE_ARCHIVE_POSTGRES_PASSWORD`, and
`VOICE_ARCHIVE_POSTGRES_DB`; connection URLs are constructed internally.
See [.env.example](.env.example) for ports, CORS origins, and test credentials.
Local `.env` is excluded from Git and Docker images.

The stack binds to localhost and uses development credentials. Authentication
is not implemented. PostgreSQL data lives in the `voice-archive_postgres-data`
named volume, mounted at `/var/lib/postgresql`.

- `docker compose down` preserves the database.
- `docker compose down -v` deletes its volume and data.
- Changing `.env` credentials does not change users in an existing database.

## Tests

```powershell
docker compose --profile test run --rm --build tests
```

The full suite runs against a separate PostgreSQL service with temporary storage.
Database tests create, migrate, and drop isolated databases. Coverage is printed
in the terminal. Stop the test server afterward with:

```powershell
docker compose --profile test stop db-test
```

The [book feature scenarios](tests/features/books/) are the detailed behavior
specification. Their [pytest bindings](tests/feature_tests/test_books.py) discover
all scenarios in that folder; missing steps and failed assertions fail the suite.
[Contract tests](tests/contract/test_books.py) cover schemas and HTTP contracts;
[persistence tests](tests/persistence/test_books.py) cover transactions, constraints,
and migrations.

To save coverage reports on the host:

```powershell
docker compose --profile test run --rm -v "./reports:/app/reports" tests pytest --cov=voice_archive_api --cov-report=term-missing --cov-report=html:reports/htmlcov --cov-report=xml:reports/coverage.xml
```

## Develop outside Docker

With Python 3.14 and uv installed, use Docker for PostgreSQL and run the API locally:

```powershell
docker compose up -d --wait db
docker compose stop api
uv sync --locked
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn voice_archive_api.main:app --reload
```

Here uv injects `.env` into the process. For PyCharm/IntelliJ, select
`.venv/Scripts/python.exe`, use this directory as the working directory, and
configure the runner to inject the same variables. Run module `uvicorn` with
arguments `voice_archive_api.main:app --reload`.

## Code and migrations

Under [src/voice_archive_api](src/voice_archive_api/):

- `core/` and `db/`: settings, shared metadata, and session management.
- `books/`: constants, validation, request/response schemas, model, and CRUD service.
- `api/`: shared error envelopes, HTTP routes, and book-specific error handlers.
- `main.py`: application wiring and handler registration.

The service commits explicitly; its `book_write()` context manager rolls back
failures and translates duplicate-identity violations. PATCH uses Pydantic's
experimental `MISSING` sentinel to distinguish omission from explicit null.

Schema changes belong in [Alembic migrations](alembic/versions/). Generate a new
revision on the host so the file stays in the repository:

```powershell
uv run --env-file .env alembic revision --autogenerate -m "describe the schema change"
```

Review generated migrations before applying them. The migration chain is
`0001_backend_foundation` → `0002_book_metadata` → `0003_book_column_constraints`.
Book identity uses a unique digest index; the migration role needs permission to
install PostgreSQL's bundled `pgcrypto` extension, as provided by local Compose.
