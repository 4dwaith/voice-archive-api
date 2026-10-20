# Create the app folder and user
FROM python:3.14-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    TZ=UTC \
    PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home appuser \
    && mkdir -p /app/uploads \
    && chown appuser:appuser /app /app/uploads
USER appuser
WORKDIR /app
COPY --chown=appuser pyproject.toml uv.lock .python-version ./

FROM base AS test
# Install runtime + dev dependencies
RUN uv sync --locked --no-install-project

# File copying repeated since the install layer is different for different stages
COPY --chown=appuser README.md alembic.ini .env.example compose.yaml ./
COPY --chown=appuser src ./src
COPY --chown=appuser alembic ./alembic
COPY --chown=appuser tests ./tests
RUN uv sync --locked
CMD ["pytest", "--cov=voice_archive_api", "--cov-report=term-missing", "--cov-report=xml", "--cov-report=html"]

FROM base AS runtime
# Install only runtime dependencies
RUN uv sync --locked --no-dev --no-install-project

# File copying repeated since the install layer is different for different stages
COPY --chown=appuser README.md alembic.ini ./
COPY --chown=appuser src ./src
COPY --chown=appuser alembic ./alembic
RUN uv sync --locked --no-dev
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn voice_archive_api.main:app --host 0.0.0.0 --port 8000"]
