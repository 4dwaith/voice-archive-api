from fastapi import APIRouter, Request, Response

from voice_archive_api.api.schemas.live import LiveErrorResponse, LiveTokenResponse
from voice_archive_api.services.live import create_live_token

router = APIRouter(prefix="/live", tags=["Live"])


@router.post(
    "/token",
    operation_id="create_api_v1_live_token",
    response_model=LiveTokenResponse,
    responses={503: {"model": LiveErrorResponse, "description": "Live provisioning unavailable"}},
)
async def provision_live_token(request: Request, response: Response) -> LiveTokenResponse:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    return LiveTokenResponse(token=await create_live_token(settings), model=settings.gemini_live_model)
