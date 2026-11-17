"""Sheds load on ``/score`` instead of letting a queue of requests build up behind the model lock.

The model and spaCy sit behind a single lock (``ScoringService.score``), so a pod serves one text
at a time. Accepting more requests than it can serve in about a second does not raise throughput,
it only makes every caller wait behind all the others: during a flood of 200 concurrent clients the
measured median latency was 22 s, for 9 requests served per second. Past ``max_in_flight`` this
middleware answers ``503`` with ``Retry-After`` at once, so the requests that are accepted stay fast
and a well-behaved client knows to retry.

Only the paths in ``limited_paths`` are counted. ``/health`` and ``/metrics`` are deliberately left
out: Kubernetes takes a pod out of the Service when its readiness probe fails, so refusing a probe
because the pod is busy would turn an overload into an outage. That is also why the limit is here
and not uvicorn's ``--limit-concurrency``, which counts every request, probes included.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# About one second of work for this pod: 30 queued texts x 30 to 80 ms each (measured on a mix of
# short and 5000-character comments). Re-measure with the load test before moving it.
MAX_IN_FLIGHT_SCORES = 30
LIMITED_PATHS = frozenset({"/score"})
RETRY_AFTER_SECONDS = "1"


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    """Returns 503 when ``max_in_flight`` limited requests are already being handled."""

    def __init__(
        self,
        app: ASGIApp,
        max_in_flight: int = MAX_IN_FLIGHT_SCORES,
        limited_paths: frozenset[str] = LIMITED_PATHS,
        on_shed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(app)
        self.max_in_flight = max_in_flight
        self.limited_paths = limited_paths
        self.on_shed = on_shed
        # One event loop per process, and no `await` between the check and the increment below, so
        # no lock is needed.
        self.in_flight = 0

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path not in self.limited_paths:
            return await call_next(request)
        if self.in_flight >= self.max_in_flight:
            if self.on_shed is not None:
                self.on_shed()
            # Not logged per request: under a flood that would be thousands of lines a minute.
            # The api_requests_shed_total metric carries the count.
            return JSONResponse(
                {"detail": "Server busy, retry shortly"},
                status_code=503,
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
        self.in_flight += 1
        try:
            return await call_next(request)
        finally:
            self.in_flight -= 1
