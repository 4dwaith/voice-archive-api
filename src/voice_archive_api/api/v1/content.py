"""Bounded canonical ingestion, publication, and conditional content purge."""

from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.types import Message

from voice_archive_api.api.schemas.content import ContentErrorResponse, ContentValidationResponse
from voice_archive_api.api.schemas.errors import ValidationErrorResponse
from voice_archive_api.api.v1.books import BookId, Database, missing_book
from voice_archive_api.logic.content_validation.schemas import MAX_PAYLOAD_BYTES, ContentInput
from voice_archive_api.logic.content_validation.validation import (
    ContentIssue,
    ContentValidationError,
    validate_content,
)
from voice_archive_api.services.content import service
from voice_archive_api.services.content.schemas import ContentManifest, ImportResult


class ContentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()
        if self.methods is None or "POST" not in self.methods:
            return handler

        async def bounded_import(request: Request) -> Response:
            # Bound actual received bytes before FastAPI's JSON parsing/dependencies.
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_PAYLOAD_BYTES:
                    raise ContentValidationError(
                        [ContentIssue("payload_limit", "$", None, f"Input exceeds {MAX_PAYLOAD_BYTES} bytes")]
                    )
                body.extend(chunk)
            raw = bytes(body)
            request.state.validated_content = await run_in_threadpool(validate_content, raw)

            # Replay the bounded body through a new Request, preserving scope/state.
            async def receive() -> Message:
                return {"type": "http.request", "body": raw, "more_body": False}

            return await handler(Request(request.scope, receive))

        return bounded_import


router = APIRouter(prefix="/books", tags=["Content"], route_class=ContentRoute)


@router.post(
    "/{book_id}/content-imports",
    response_model=ImportResult,
    operation_id="import_api_v1_book_content",
    responses=missing_book
    | {
        409: {"model": ContentErrorResponse},
        413: {"model": ContentErrorResponse},
        422: {"model": ContentValidationResponse | ValidationErrorResponse},
    },
)
def import_content(
    book_id: BookId, payload: ContentInput, request: Request, db: Database, dry_run: bool = False
) -> ImportResult:
    """Validate and publish once (200); dry-run checks the same preconditions without writes."""
    # The typed body supplies the generated contract. The route's raw-byte result
    # preserves duplicate-key validation and the derived HTML/text representation.
    return service.publish(db, book_id, request.state.validated_content, dry_run=dry_run)


@router.get(
    "/{book_id}/content",
    response_model=ContentManifest,
    operation_id="get_api_v1_book_content",
    responses=missing_book
    | {200: {"headers": {"ETag": {"schema": {"type": "string"}, "description": "Strong content-state tag for purge"}}}},
)
def get_content(book_id: BookId, db: Database, response: Response) -> ContentManifest:
    manifest, etag = service.get_manifest_and_etag(db, book_id)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return manifest


@router.delete(
    "/{book_id}/content",
    status_code=204,
    response_class=Response,
    operation_id="purge_api_v1_book_content",
    responses=missing_book | {412: {"model": ContentErrorResponse}, 428: {"model": ContentErrorResponse}},
)
def purge_content(book_id: BookId, db: Database, if_match: Annotated[str, Header()] = "") -> Response:
    """Purge PDF/audio/text together, preserving cover and descriptive book metadata."""
    service.purge(db, book_id, if_match or None)
    return Response(status_code=204)
