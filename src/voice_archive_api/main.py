from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import make_url

from voice_archive_api import __version__
from voice_archive_api.api.v1.router import router as v1_router
from voice_archive_api.core.config import Settings, get_settings

# Uvicorn configures this logger in both CLI and PyCharm module runs.
logger = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database_backend = make_url(resolved_settings.database_url).get_backend_name()
        logger.info(
            "Starting Voice Archive API version=%s environment=%s database_backend=%s",
            __version__,
            resolved_settings.environment,
            database_backend,
        )
        yield
        logger.info("Stopping Voice Archive API")

    application = FastAPI(
        title="Voice Archive API",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(v1_router, prefix="/api/v1")
    return application


app = create_app()
