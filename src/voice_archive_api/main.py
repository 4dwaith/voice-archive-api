from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from voice_archive_api import __version__
from voice_archive_api.api.errors import COMMON_ERROR_RESPONSES, install_error_handlers
from voice_archive_api.api.v1.books import install_book_error_handlers
from voice_archive_api.api.v1.cover_files import install_cover_error_handlers
from voice_archive_api.api.v1.file_uploads import install_upload_error_handlers
from voice_archive_api.api.v1.source_pdf_files import install_pdf_error_handlers
from voice_archive_api.api.v1.router import router as v1_router
from voice_archive_api.core.application import Application
from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import configure_logging
from voice_archive_api.db.session import create_database_engine, create_session_factory

# Uvicorn configures this logger in both CLI and PyCharm module runs.
logger = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None) -> Application:
    resolved_settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: Application) -> AsyncIterator[None]:
        configure_logging()
        # Engine creation is lazy about connecting; liveness/docs need no database.
        engine = create_database_engine(resolved_settings.database_url)
        try:
            application.state.engine = engine
            application.state.session_factory = create_session_factory(engine)
            logger.info(
                "Starting Voice Archive API version=%s environment=%s database_backend=%s",
                __version__,
                resolved_settings.environment,
                engine.url.get_backend_name(),
            )
            yield
        finally:
            engine.dispose()
            logger.info("Stopping Voice Archive API")

    application = Application(
        settings=resolved_settings,
        title="Voice Archive API",
        version=__version__,
        lifespan=lifespan,
        responses=COMMON_ERROR_RESPONSES,
    )
    application.include_router(v1_router, prefix="/api/v1")
    install_error_handlers(application)
    install_book_error_handlers(application)
    install_cover_error_handlers(application)
    install_pdf_error_handlers(application)
    install_upload_error_handlers(application)
    return application


app = create_app()
