"""Security headers on every response, including the ones the other middlewares produce.

Added as a pure ASGI middleware (not ``BaseHTTPMiddleware``): it only touches the response headers,
so it adds no body buffering and almost no per-request cost. Headers a route already set (the
test page sets its own Content-Security-Policy) are left alone.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Strict policy for the JSON API: a JSON answer never needs to load, frame or run anything.
API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'"

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # Browsers ignore HSTS on plain HTTP: it only takes effect once a TLS front (HTTPS load
    # balancer or Ingress with a certificate) is in place, and is harmless until then.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
                if headers.get("content-type", "").startswith("application/json"):
                    headers.setdefault("Content-Security-Policy", API_CONTENT_SECURITY_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_headers)
