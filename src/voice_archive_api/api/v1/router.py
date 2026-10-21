from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from voice_archive_api.api.v1.books import router as books_router
from voice_archive_api.api.v1.files import router as files_router
from voice_archive_api.db.session import get_db

router = APIRouter()
router.include_router(books_router)
router.include_router(files_router)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    api_version: Literal["v1"]


class NotReadyResponse(BaseModel):
    status: Literal["unavailable"]
    api_version: Literal["v1"]


@router.get(
    "/health",
    operation_id="get_api_v1_health",
    response_model=HealthResponse,
    summary="Check API liveness",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok", api_version="v1")


@router.get(
    "/ready",
    operation_id="get_api_v1_ready",
    response_model=HealthResponse,
    responses={503: {"model": NotReadyResponse}},
    summary="Check API and database readiness",
)
def ready(db: Annotated[Session, Depends(get_db)]) -> HealthResponse | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "api_version": "v1"},
        )
    return HealthResponse(status="ok", api_version="v1")
