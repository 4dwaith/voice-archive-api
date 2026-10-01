# Voice Archive API

FastAPI backend for Voice Archive. Phase 01 provides configuration, synchronous
SQLAlchemy sessions, Alembic migrations, API documentation, and a database-backed
health endpoint. It intentionally contains no domain tables yet.

## Requirements

- Docker Desktop with Linux containers and Docker Compose.
- The images provide Python 3.14, locked Python dependencies, and PostgreSQL 18.
  Host Python/uv is optional for PyCharm development.

## Set up and run

From `voice-archive-api`, first copy `.env.example` to `.env` and edit it for your local setup. Keep the existing `.env` when updating the project:

```powershell
docker compose up --build -d --wait
```

The API is then available at `http://127.0.0.1:8000`. Useful endpoints are:

- `GET /api/v1/health`
- `/docs`
- `/redoc`
- `/openapi.json`

The default SQLite database is `data/voice_archive.sqlite3`. Run migrations
before starting a new local database. The application never calls
`Base.metadata.create_all()`.

## Configuration

Compose reads `.env` and explicitly injects each service's settings into its
process environment. The application and Alembic do not read dotenv files.
Copy `.env.example` to `.env` once; CI uses `--env-file .env.example`.
Local `.env` is ignored by Git and excluded from images.

`VOICE_ARCHIVE_POSTGRES_USER`, `VOICE_ARCHIVE_POSTGRES_PASSWORD`, and
`VOICE_ARCHIVE_POSTGRES_DB` are required. Host and port default to
`127.0.0.1:5432`; Compose overrides them to `db:5432` (or `db-test:5432`).
Compose maps the same credentials to the PostgreSQL image's `POSTGRES_*` names.
Settings constructs a SQLAlchemy URL object, preserving special characters in
passwords without manual URL encoding. There is no configurable database URL.
Quote literal `$` values with single quotes in the Compose env file.

CORS, environment, and the optional upload limit are explicitly injected too.
An unset upload limit is passed as `null`; the container upload directory stays
`/app/uploads`. Tests receive separate credentials and must target the dedicated
`voice_archive_test` database before creating isolated databases.

PostgreSQL 18 stores its cluster under `/var/lib/postgresql/18/docker`, inside
the named volume mounted at `/var/lib/postgresql`. Docker manages this volume
in its Linux storage, not in the source directory. Container recreation and
ordinary `down` preserve it; `down -v` deletes it. Changing credentials in the
env file does not update users in an already initialized volume: change the
database role's password too. A major PostgreSQL upgrade still requires a
database migration/upgrade, not just an image tag change.

The test server instead mounts tmpfs at `/var/lib/postgresql`; stopping it
discards its database files. It does not share the application's named volume.

CORS is a browser access policy,
not authentication; keep write-capable deployments on a trusted boundary until
authentication is implemented.

`VOICE_ARCHIVE_CORS_ORIGINS` is a JSON array. The defaults allow HTTP and HTTPS
for both `localhost:5173` and `127.0.0.1:5173`.

### Optional local HTTPS

The repository does not generate or store TLS keys. With a locally trusted
certificate and key, Uvicorn can serve HTTPS using:

```powershell
uv run --env-file .env uvicorn voice_archive_api.main:app --reload `
  --ssl-certfile C:\path\to\localhost.pem `
  --ssl-keyfile C:\path\to\localhost-key.pem
```

Configure Vite separately with a trusted certificate if the frontend must also
run over HTTPS. An HTTPS frontend should call an HTTPS API to avoid mixed-content
restrictions.

## Tests and coverage

```powershell
docker compose --profile test run --rm --build tests
```

Tests run in their own Python container against a separate `db-test` PostgreSQL
container. Every database test creates, migrates, and drops its own uniquely
named database. Tests never fall back to SQLite and never reuse the app's data.
The test server uses memory-backed storage and publishes port 5433 for optional
host-side debugging. Database connections fail promptly if PostgreSQL is unavailable.

The test command prints coverage. To retain XML/HTML reports on the host, run:

```powershell
docker compose --profile test run --rm --build -v "./reports:/app/reports" tests pytest --cov=voice_archive_api --cov-report=term-missing --cov-report=xml:reports/coverage.xml --cov-report=html:reports/htmlcov
```

The `reports` directory must be writable by the container user. To stop the
disposable test database without stopping the app: `docker compose --profile test stop db-test`.

### Executable behavior documentation

[`tests/features/books/`](tests/features/books/) is the book API specification.
Read its Given/When/Then scenarios for duplicate policy, Unicode limits,
required language tags, PATCH semantics, pagination, and error responses.
[`tests/test_features.py`](tests/test_features.py) calls `scenarios("features")`
to discover every scenario recursively. New scenarios automatically become
tests; missing step definitions and failed assertions fail the test run.
Feature descriptions alone are explanatory prose, not executable checks.
Put requirements in scenarios with assertions, not only descriptions.

Run just the feature specifications or inspect what will run:

```powershell
docker compose --profile test run --rm --build tests pytest tests/test_features.py
docker compose --profile test run --rm tests pytest tests/test_features.py --collect-only -q
```

Ordinary pytest tests cover transactions, database constraints, migrations and
the generated OpenAPI contract. Swagger (`/docs`) provides interactive requests;
ReDoc (`/redoc`) provides reference documentation. Both use `/openapi.json`.
There is no separate handwritten book API reference to keep synchronized.

GitHub Actions uses the same Docker test image and PostgreSQL service. A failing
scenario produces a nonzero exit code and blocks the runtime image build.

### Language validation

Language is required and checked with `langcodes.tag_is_valid()`. The library
owns the reference data and accepted codes; the application does not maintain
a language registry or perform network lookups. English (`en`), Kannada (`kn`),
regional codes such as `kn-IN`, and library-supported aliases such as `en_US`
are accepted. Submitted spelling is retained. Strict BCP 47 conformance is
deliberately not enforced; the language scenarios define the application contract.

## Alembic workflow

Apply all migrations:

```powershell
docker compose exec api alembic upgrade head
```

After changing SQLAlchemy model metadata in a later phase, draft a revision:

```powershell
uv run alembic revision --autogenerate -m "describe the schema change"
```

Always review generated migrations before applying them. Alembic tracks the
current revision in the database's `alembic_version` table.

## PyCharm

1. Open `voice-archive-api` as the project directory.
2. Start PostgreSQL with `docker compose up -d --wait db`, copy `.env.example`
   to `.env` if needed, then run `uv sync --locked` and `uv run --env-file .env alembic upgrade head`.
3. Select `.venv\Scripts\python.exe` as the project interpreter. Recent
   PyCharm versions can also create/select the environment directly from
   `pyproject.toml` using `uv`.
4. Add a Python run configuration with module name `uvicorn` and parameters
   `voice_archive_api.main:app --reload`.
5. Configure the run configuration to inject the `VOICE_ARCHIVE_*` values from `.env` into the process environment. The application will not load the file itself.
6. Set the working directory to the `voice-archive-api` project directory.
   Stop the API container (`docker compose stop api`) before using host port 8000.

For host-side tests, start `docker compose --profile test up -d --wait db-test`.
In the test runner environment, set `VOICE_ARCHIVE_POSTGRES_USER` and
`VOICE_ARCHIVE_POSTGRES_PASSWORD` to the test credentials, `VOICE_ARCHIVE_POSTGRES_DB`
to `voice_archive_test`, host to `127.0.0.1`, and port to the published test port
(default 5433). Then run `uv run --locked pytest`. The test runner refuses the
application database. The default supported test command remains Docker.

The package is installed editable by `uv`, so manually marking `src` as a
source root is optional.
