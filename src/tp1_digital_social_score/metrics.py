"""Prometheus metrics of the scoring API (course n°3: monitoring and observability).

Metrics are exposed on ``GET /metrics`` and collected by Google Managed Service for Prometheus
(``k8s/podmonitoring.yaml``). The names match the course examples (``api_requests_total``,
``api_latency_seconds``) so that the SLO alert rules apply as they are.

RGPD: only aggregated numbers are exposed. The labels are the HTTP method, the route *template*
(``/score``, never the raw path) and the status code: no text, no key, no client id, no address.
"""

from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

# Latency buckets in seconds: dense around the 300 ms and 500 ms SLO thresholds of the course.
LATENCY_BUCKETS: tuple[float, ...] = (0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0)
SCORE_BUCKETS: tuple[float, ...] = (1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)

# Status codes /score can answer: 200 scored, 401 refused key, 422 invalid body, 500 server error.
KNOWN_SCORE_STATUSES = ("200", "401", "422", "500")

# Routes that are not counted: probes and scraping would drown the real traffic.
IGNORED_ROUTES = frozenset({"/health", "/metrics"})
UNMATCHED_ROUTE = "unmatched"


class ApiMetrics:
    """All the metrics of one application, in their own registry (no global state)."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "api_requests_total",
            "HTTP requests handled, by method, route template and status code",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "api_latency_seconds",
            "HTTP request latency in seconds, by route template",
            ["route"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.in_progress = Gauge(
            "api_requests_in_progress",
            "HTTP requests being handled right now",
            registry=self.registry,
        )
        self.inference_latency = Histogram(
            "inference_latency_seconds",
            "Time spent anonymising, cleaning and scoring a text (without HTTP and queueing)",
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.score = Histogram(
            "toxicity_score",
            "Distribution of the scores returned (watch it to detect a drift of the model)",
            buckets=SCORE_BUCKETS,
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "api_auth_failures_total",
            "Requests refused because the API key was missing or invalid",
            registry=self.registry,
        )
        self._declare_known_series()

    def _declare_known_series(self) -> None:
        """Create the expected series at zero when the application starts.

        A labelled counter only exists after its first increment, and Prometheus ignores the first
        sample of a series it has not seen before: without this, the very first requests of a pod
        would be invisible to ``rate()``.
        """
        for status in KNOWN_SCORE_STATUSES:
            self.requests.labels(method="POST", route="/score", status=status)
        self.latency.labels(route="/score")

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        """Record one finished request (probes and scraping are ignored)."""
        if route in IGNORED_ROUTES:
            return
        self.requests.labels(method=method, route=route, status=str(status)).inc()
        self.latency.labels(route=route).observe(seconds)

    def observe_scores(self, scores: Sequence[float]) -> None:
        for value in scores:
            self.score.observe(value)

    def render(self) -> bytes:
        """The exposition format read by Prometheus."""
        return generate_latest(self.registry)
