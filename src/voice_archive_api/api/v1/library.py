from fastapi import APIRouter, Request, Response

from voice_archive_api.api.schemas.errors import ValidationErrorResponse
from voice_archive_api.api.v1.books import missing_book
from voice_archive_api.services.library.schemas import AskRequest, AskResult
from voice_archive_api.services.library.service import answer_library_question

router = APIRouter(prefix="/library", tags=["Library"])


@router.post(
    "/ask",
    operation_id="ask_api_v1_library",
    response_model=AskResult,
    responses=missing_book | {422: {"model": ValidationErrorResponse}},
)
async def ask_library(payload: AskRequest, request: Request, response: Response) -> AskResult:
    response.headers["Cache-Control"] = "no-store"
    return await answer_library_question(request.app.state.session_factory, request.app.state.settings, payload)
