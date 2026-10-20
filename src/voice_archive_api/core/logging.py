"""Attach the package-provided request-ID filter to application/server logging."""

import logging

from asgi_correlation_id import CorrelationIdFilter


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
