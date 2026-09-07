# Voice Archive API

FastAPI backend for Voice Archive. Phase 01 provides configuration, synchronous
SQLAlchemy sessions, Alembic migrations, API documentation, and a database-backed
health endpoint. It intentionally contains no domain tables yet.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- Python 3.14 (installed automatically by `uv` when needed)

## Set up and run

From `voice-archive-api`:

```powershell
Copy-Item .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn voice_archive_api.main:app --reload
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

Configuration is loaded from `.env` and environment variables prefixed with
`VOICE_ARCHIVE_`. See `.env.example` for all current settings. Environment
variables take precedence over `.env` values. CORS is a browser access policy,
not authentication; keep write-capable deployments on a trusted boundary until
authentication is implemented.

`VOICE_ARCHIVE_CORS_ORIGINS` is a JSON array. The defaults allow HTTP and HTTPS
for both `localhost:5173` and `127.0.0.1:5173`.

### Optional local HTTPS

The repository does not generate or store TLS keys. With a locally trusted
certificate and key, Uvicorn can serve HTTPS using:

```powershell
uv run uvicorn voice_archive_api.main:app --reload `
  --ssl-certfile C:\path\to\localhost.pem `
  --ssl-keyfile C:\path\to\localhost-key.pem
```

Configure Vite separately with a trusted certificate if the frontend must also
run over HTTPS. An HTTPS frontend should call an HTTPS API to avoid mixed-content
restrictions.

## Tests and coverage

```powershell
uv run pytest
uv run pytest --cov=voice_archive_api --cov-report=term-missing `
  --cov-report=xml --cov-report=html
```

Tests use SQLite files inside pytest-managed temporary directories and override
the FastAPI database dependency. They do not access the developer database or
require existing `data` or `uploads` directories.

## Alembic workflow

Apply all migrations:

```powershell
uv run alembic upgrade head
```

After changing SQLAlchemy model metadata in a later phase, draft a revision:

```powershell
uv run alembic revision --autogenerate -m "describe the schema change"
```

Always review generated migrations before applying them. Alembic tracks the
current revision in the database's `alembic_version` table.

## PyCharm

1. Open `voice-archive-api` as the project directory.
2. Run `uv sync` once.
3. Select `.venv\Scripts\python.exe` as the project interpreter. Recent
   PyCharm versions can also create/select the environment directly from
   `pyproject.toml` using `uv`.
4. Add a Python run configuration with module name `uvicorn` and parameters
   `voice_archive_api.main:app --reload`.
5. Set the working directory to the `voice-archive-api` project directory.

The package is installed editable by `uv`, so manually marking `src` as a
source root is optional.
