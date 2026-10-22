"""Compress API/documentation responses, bypassing file delivery and byte ranges."""

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class StructuredGZipMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=500, compresslevel=6)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        is_media = path.startswith("/api/v1/files/") and scope.get("method") in {"GET", "HEAD"}
        has_range = any(name.lower() == b"range" for name, _ in scope.get("headers", []))
        handler = self.app if is_media or has_range else self.compressed
        await handler(scope, receive, send)
