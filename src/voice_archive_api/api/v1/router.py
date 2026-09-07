from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from voice_archive_api.db.session import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    api_version: Literal["v1"]


@router.get(
    "/health",
    operation_id="get_api_v1_health",
    response_model=HealthResponse,
    summary="Check API and database health",
)
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", api_version="v1")

