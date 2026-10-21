"""HTTP delivery contracts and documentation for files identified by storage keys."""

from typing import Any, Protocol

from fastapi import Response


class FileDelivery(Protocol):
    """A delivery backend owns how opaque storage keys become HTTP responses."""

    def response(self, key: str, *, media_type: str, filename: str) -> Response: ...


# These headers are consumed by FileResponse directly from the ASGI request.
delivery_parameters = {
    "parameters": [
        {
            "name": "Range",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": "Byte range or ranges, such as bytes=0-65535",
        },
        {
            "name": "If-Range",
            "in": "header",
            "required": False,
            "schema": {"type": "string"},
            "description": "ETag or Last-Modified validator for the range",
        },
    ]
}


def delivery_responses(media_type: str, *, head: bool = False) -> dict[int | str, dict[str, Any]]:
    """Document native FileResponse bodies and headers for GET and HEAD."""
    binary = {"schema": {"type": "string", "format": "binary"}}
    headers = {
        name: {"schema": {"type": "string"}}
        for name in ("Accept-Ranges", "Content-Type", "Content-Length", "Content-Disposition", "ETag", "Last-Modified")
    }
    content_range = {"Content-Range": {"schema": {"type": "string"}}}
    responses: dict[int | str, dict[str, Any]] = {
        200: {"description": "Complete file", "headers": headers},
        206: {"description": "Requested byte range or ranges", "headers": headers | content_range},
        400: {"description": "Malformed or unsupported range"},
        416: {"description": "Unsatisfiable range", "headers": content_range},
    }
    if not head:
        responses[200]["content"] = {media_type: binary}
        responses[206]["content"] = {media_type: binary, "multipart/byteranges": binary}
        responses[400]["content"] = {"text/plain": {"schema": {"type": "string"}}}
    return responses
