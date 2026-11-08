"""Attach the package-provided request-ID filter to application/server logging."""

import logging
import os
from traceback import extract_tb

from asgi_correlation_id import CorrelationIdFilter
from psycopg import Error as PostgreSQLError
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import StatementError

from voice_archive_api.core.config import Settings


def log_exception(
    logger: logging.Logger,
    message: str,
    error: BaseException,
    *,
    settings: Settings | None = None,
    level: int = logging.ERROR,
) -> None:
    """Record causes and stack locations without locals, SQL payloads or credentials."""
    details = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            reason = "; ".join(
                f"{'.'.join(map(str, issue['loc']))}: {issue['type']}"
                for issue in current.errors(include_input=False, include_context=False, include_url=False)
            )
        elif isinstance(current, StatementError):
            original = current.orig
            seen.add(id(original))  # Its driver diagnostic is included here, without SQL/parameters.
            primary = getattr(getattr(original, "diag", None), "message_primary", None)
            driver_message = primary or (
                str(original) if isinstance(original, PostgreSQLError) else "Driver message unavailable"
            )
            reason = f"{type(original).__name__} SQLSTATE={getattr(original, 'sqlstate', None)}: {driver_message}"
        else:
            reason = str(current)
        locations = "\n".join(
            f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in extract_tb(current.__traceback__)
        )
        details.append(f"{type(current).__name__}: {reason}\n{locations}")
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    secrets = [
        value
        for name, value in os.environ.items()
        if name.startswith("VOICE_ARCHIVE_") and ("PASSWORD" in name or "API_KEY" in name) and value
    ]
    if settings is not None:
        secrets.extend(
            value.get_secret_value()
            for name in type(settings).model_fields
            if isinstance(value := getattr(settings, name), SecretStr) and value.get_secret_value()
        )
    diagnostic = "\nCaused by:\n".join(details)
    for secret in sorted(secrets, key=len, reverse=True):
        diagnostic = diagnostic.replace(secret, "[redacted]")
    logger.log(level, "%s\n%s", message, diagnostic)


def configure_logging() -> None:
    # Uvicorn installs its own handlers before app startup. Direct/IDE runs may
    # have only root handlers. Preserve handlers (including test log capture).
    logging.basicConfig(level=logging.INFO)
    formatter = logging.Formatter("%(levelname)s [request_id=%(correlation_id)s] %(name)s: %(message)s")
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(item, CorrelationIdFilter) for item in handler.filters):
                handler.addFilter(CorrelationIdFilter(default_value="-"))
            handler.setFormatter(formatter)
