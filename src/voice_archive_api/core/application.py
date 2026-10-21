from typing import Any

from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import State
from starlette.types import ASGIApp

from voice_archive_api.core.config import Settings


class ApplicationState(State):
    """Settings exist at construction; database resources are assigned at startup."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings


class Application(FastAPI):
    state: ApplicationState

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.state = ApplicationState(settings)

    def build_middleware_stack(self) -> ASGIApp:
        # Wrap the complete FastAPI stack, including ServerErrorMiddleware, so
        # the library middleware adds request IDs and CORS headers to 500s too.
        active_middleware: ASGIApp = CORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=list(self.state.settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
        active_middleware = CorrelationIdMiddleware(active_middleware)

        return active_middleware

    def openapi(self) -> dict[str, Any]:
        document = super().openapi()
        for path in document["paths"].values():
            for method, operation in path.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                    continue
                for response in operation["responses"].values():
                    response.setdefault("headers", {})["X-Request-ID"] = {
                        "description": "Request identifier for correlating server logs",
                        "schema": {"type": "string"},
                    }
        return document
