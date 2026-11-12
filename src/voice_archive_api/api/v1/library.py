from fastapi import APIRouter, Request, Response
from sqlalchemy.orm import sessionmaker

from voice_archive_api.api.schemas.books import BookErrorResponse
from voice_archive_api.api.schemas.errors import ValidationErrorResponse
from voice_archive_api.api.schemas.series import SeriesErrorResponse
from voice_archive_api.services.library.schemas import AskRequest, AskResult
from voice_archive_api.services.library.service import answer_library_question

router = APIRouter(prefix="/library", tags=["Library"])


@router.post(
    "/ask",
    operation_id="ask_api_v1_library",
    response_model=AskResult,
    responses={404: {"model": BookErrorResponse | SeriesErrorResponse}, 422: {"model": ValidationErrorResponse}},
)
async def ask_library(payload: AskRequest, request: Request, response: Response) -> AskResult:
    response.headers["Cache-Control"] = "no-store"
    factory = request.app.state.session_factory
    if (access := getattr(request.state, "access", None)) is not None:
        factory = sessionmaker(
            bind=request.app.state.engine, autoflush=False, expire_on_commit=False, info={"access": access}
        )
    return await answer_library_question(factory, request.app.state.settings, payload)
