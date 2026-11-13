"""Digital Social Score API: receives a text, returns a toxicity score from 0 to 100.

Routes:

- ``GET  /health``: startup, readiness and liveness probe (Kubernetes); needs no key.
- ``GET  /metrics``: Prometheus metrics (see ``metrics.py``), aggregated numbers, never a text.
  Open unless ``METRICS_TOKEN`` is set, in which case it needs ``Authorization: Bearer <token>``
  (a separate secret from the client API keys: it does not unlock ``/score``).
- ``POST /score``: ``{"text": "..."}`` -> ``{"score": ..., "labels": {...}, "request_id": ...}``.
  Needs a valid API key in the ``X-API-Key`` header, otherwise the answer is ``401``.

RGPD: the received text is anonymised (regex + spaCy NER) before it reaches the model, it is
never stored, and it is never written to the logs. Only a request id, the client id (a pseudonym
tied to the API key), the text length, the score and the latency are logged: never the text, the
key or the caller's IP address (the uvicorn access log, which contains it, is disabled).

Configuration (environment variables): ``API_KEYS`` (required, see ``security.py``),
``METRICS_TOKEN`` (optional, protects ``/metrics``), ``MODEL_DIR`` (artifacts directory, default
``models/lstm``), ``LOG_LEVEL`` (default ``INFO``) and ``TORCH_NUM_THREADS`` (default 2: measured
on CPU, using every core makes this small LSTM slower and its latency erratic).
"""

import logging
import os
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import spacy
import torch
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, StringConstraints

from tp1_digital_social_score.body_limit import MAX_BODY_BYTES, MaxBodySizeMiddleware
from tp1_digital_social_score.lstm_model import LstmPredictor
from tp1_digital_social_score.metrics import (
    CONTENT_TYPE_LATEST,
    IGNORED_ROUTES,
    UNMATCHED_ROUTE,
    ApiMetrics,
)
from tp1_digital_social_score.scoring import LABELS, load_weights, toxicity_score
from tp1_digital_social_score.security import (
    API_KEY_HEADER,
    ApiKeyStore,
    load_api_keys,
    load_metrics_token,
    metrics_token_matches,
)
from tp1_digital_social_score.text_pipeline import load_nlp, prepare_texts

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 5000  # the longest comment of the Jigsaw dataset has 5000 characters
DEFAULT_TORCH_THREADS = 2
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)
]


class ScoreRequest(BaseModel):
    text: Text


class Prediction(BaseModel):
    """Score from 0 (harmless) to 100 (very toxic) and the probability of each label."""

    score: float = Field(ge=0, le=100)
    labels: dict[str, float]


class ScoreResponse(Prediction):
    request_id: str


class HealthResponse(BaseModel):
    status: str


@dataclass
class ScoringService:
    """Everything needed to score texts: loaded once at startup, shared by all requests."""

    nlp: spacy.language.Language
    predictor: LstmPredictor
    weights: dict[str, float]
    lock: threading.Lock
    metrics: ApiMetrics

    def score(self, texts: Sequence[str]) -> list[Prediction]:
        """Anonymise, clean and score raw texts."""
        # spaCy pipelines are not guaranteed thread-safe: one request at a time uses the models.
        with self.lock:
            # Timed once the lock is held: the metric is the work itself, without the queueing.
            started = time.perf_counter()
            prepared = prepare_texts(texts, self.nlp)
            probabilities = self.predictor.predict(prepared)
            self.metrics.inference_latency.observe(time.perf_counter() - started)
        scores = toxicity_score(probabilities, self.weights)
        self.metrics.observe_scores([float(score) for score in scores])
        return [
            Prediction(
                score=round(float(score), 2),
                labels={
                    label: round(float(probability), 4)
                    for label, probability in zip(LABELS, row, strict=True)
                },
            )
            for score, row in zip(scores, probabilities, strict=True)
        ]


def default_model_dir() -> Path:
    """Artifacts directory: ``MODEL_DIR`` if set, else ``models/lstm`` of the repository."""
    configured = os.environ.get("MODEL_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "models" / "lstm"


def configure_torch_threads() -> int:
    """Limit the torch CPU threads (``TORCH_NUM_THREADS``, default 2) and return the value used."""
    threads = max(1, int(os.environ.get("TORCH_NUM_THREADS", DEFAULT_TORCH_THREADS)))
    torch.set_num_threads(threads)
    return threads


def request_id_from(request: Request) -> str:
    """Reuse the caller's ``X-Request-ID`` when it is a safe short token, else generate one."""
    candidate = request.headers.get("X-Request-ID", "")
    return candidate if REQUEST_ID_PATTERN.match(candidate) else uuid.uuid4().hex


def get_service(request: Request) -> ScoringService:
    service: ScoringService = request.app.state.service
    return service


# auto_error=False: a missing header is handled below, so that it gives the same 401 as a wrong key.
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def authenticate(
    request: Request, api_key: Annotated[str | None, Security(api_key_header)]
) -> str:
    """Return the client id of the API key sent in ``X-API-Key``, or answer 401."""
    store: ApiKeyStore = request.app.state.api_keys
    client_id = store.authenticate(api_key) if api_key else None
    if client_id is None:
        metrics: ApiMetrics = request.app.state.metrics
        metrics.auth_failures.inc()
        # Neither the key nor the caller's address is logged.
        logger.warning("authentication failed request_id=%s", request_id_from(request))
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return client_id


def create_app(model_dir: Path | None = None, api_keys: ApiKeyStore | None = None) -> FastAPI:
    """Build the application; the keys and the models are loaded when the server starts.

    ``api_keys`` is meant for tests; in production the keys come from ``API_KEYS``.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    metrics = ApiMetrics()  # one registry per application, so tests can build many apps

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Keys first: without them the API must not start (fail closed), and it fails fast.
        app.state.api_keys = api_keys or load_api_keys()
        directory = model_dir or default_model_dir()
        logger.info(
            "Starting the scoring service with artifacts from %s (torch threads: %d)",
            directory,
            configure_torch_threads(),
        )
        app.state.service = ScoringService(
            nlp=load_nlp(),
            predictor=LstmPredictor(directory),
            weights=load_weights(directory),
            lock=threading.Lock(),
            metrics=metrics,
        )
        logger.info("Scoring service ready")
        yield

    app = FastAPI(title="Digital Social Score API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_BODY_BYTES)
    app.state.metrics = metrics
    # "" (the default) leaves /metrics open: see load_metrics_token's docstring.
    app.state.metrics_token = load_metrics_token()

    @app.middleware("http")
    async def record_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Count and time every request, by route template (never by raw path)."""
        if request.url.path in IGNORED_ROUTES:
            return await call_next(request)
        started = time.perf_counter()
        status = 500  # what the caller gets if the handler raises
        metrics.in_progress.inc()
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            metrics.in_progress.dec()
            route = request.scope.get("route")
            template = getattr(route, "path", UNMATCHED_ROUTE)
            metrics.observe_request(request.method, template, status, time.perf_counter() - started)

    async def authorize_metrics(request: Request) -> None:
        """When ``METRICS_TOKEN`` is set, require it as ``Authorization: Bearer <token>``.

        ``async def`` for the same reason as the routes below: a sync dependency would run in the
        shared thread pool and could queue behind ``/score`` under load.

        /metrics is scraped by Prometheus, not called by a client: there is no caller identity to
        log on a failure (logging the presented value would be the secret itself), so unlike
        ``authenticate`` this raises without writing a log line.
        """
        token: str = request.app.state.metrics_token
        if token and not metrics_token_matches(token, request.headers.get("Authorization")):
            raise HTTPException(status_code=401, detail="Invalid or missing metrics token")

    # /metrics and /health are `async def` on purpose. A plain `def` route runs in the thread pool
    # (40 threads) that /score also uses; under load all of them wait for the model, so a probe
    # would queue behind them, miss its 2 s timeout and get the pod removed from the Service, then
    # killed by the liveness probe. Both handlers are instant, so the event loop can run them.
    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(authorize_metrics)])
    async def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post("/score", response_model=ScoreResponse)
    def score(
        body: ScoreRequest, request: Request, client_id: Annotated[str, Depends(authenticate)]
    ) -> ScoreResponse:
        started = time.perf_counter()
        request_id = request_id_from(request)
        prediction = get_service(request).score([body.text])[0]
        logger.info(
            "score request_id=%s client=%s chars=%d score=%.2f latency_ms=%.1f",
            request_id,
            client_id,
            len(body.text),
            prediction.score,
            1000 * (time.perf_counter() - started),
        )
        return ScoreResponse(request_id=request_id, **prediction.model_dump())

    return app


app = create_app()
