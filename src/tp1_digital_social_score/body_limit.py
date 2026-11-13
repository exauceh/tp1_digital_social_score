"""Caps the size of an HTTP request body before it reaches the application.

Starlette (under FastAPI) buffers the whole request body in memory with no limit of its own, and
only *after* that does Pydantic check a field's ``max_length``. A caller can send a body of
arbitrary size (nothing in this project's stack rejects it early) and exhaust the pod's memory,
which gets it killed (``OOMKilled`` / ``CrashLoopBackOff``): a real denial-of-service, not merely a
slow response. This middleware rejects an oversized body before it is read into memory.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# The scoring text is capped at 5000 characters (MAX_TEXT_CHARS in api.py); 5000 UTF-8 characters
# never need more than 4 bytes each, and even a JSON payload that escapes every character as
# \uXXXX stays under 30 000 bytes. 64 KiB leaves ample room for that and the surrounding JSON
# without coming close to a size that could threaten the pod's memory limit.
MAX_BODY_BYTES = 64 * 1024

_TOO_LARGE = JSONResponse({"detail": "Request body too large"}, status_code=413)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Returns 413 instead of reading a body bigger than ``max_bytes``."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Cheap check first: most honest and most malicious callers alike declare Content-Length.
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            return _TOO_LARGE
        # Still read defensively, in case Content-Length is absent, wrong, or chunked encoding is
        # used: the body is read in chunks and abandoned as soon as it grows past the limit, so at
        # most `max_bytes` (plus one chunk) is ever held in memory for this request.
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > self.max_bytes:
                return _TOO_LARGE
        # The body was read from the raw stream above (not through `request.body()`), which would
        # otherwise leave the route handler nothing to read: caching it here on the private
        # attribute `Request.body()` itself uses lets it return this value instead of re-reading
        # the (already exhausted) socket stream.
        request._body = bytes(chunks)  # noqa: SLF001
        return await call_next(request)
