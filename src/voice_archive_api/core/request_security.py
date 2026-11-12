"""Reject untrusted browser writes before routing, upload parsing or providers."""

from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestSecurityMiddleware:
    def __init__(self, app: ASGIApp, origins: tuple[str, ...]):
        self.app = app
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not self.has_trusted_origin(headers):
            await JSONResponse(
                status_code=403,
                content={"error": {"code": "http_error", "message": "Untrusted request origin"}},
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return

        async def send_private_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if "no-store" not in response_headers.get("Cache-Control", ""):
                    response_headers["Cache-Control"] = "private, no-store"
            await send(message)

        await self.app(scope, receive, send_private_response)

    def has_trusted_origin(self, headers: Headers) -> bool:
        if "origin" in headers:
            # Exact allowlist also supports explicitly trusted cross-origin dev clients.
            return headers["origin"] in self.origins
        if "referer" in headers:
            try:
                source = urlsplit(headers["referer"])
                return (
                    source.username is None
                    and source.password is None
                    and f"{source.scheme}://{source.netloc}" in self.origins
                )
            except ValueError:
                return False
        return headers.get("sec-fetch-site") == "same-origin"
