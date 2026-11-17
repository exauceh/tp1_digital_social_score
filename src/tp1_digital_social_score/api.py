"""Digital Social Score API: receives a text, returns a coarse toxicity verdict.

Routes:

- ``GET  /health``: startup, readiness and liveness probe (Kubernetes); needs no key.
- ``GET  /metrics``: Prometheus metrics (see ``metrics.py``), aggregated numbers, never a text.
  Open unless ``METRICS_TOKEN`` is set, in which case it needs ``Authorization: Bearer <token>``
  (a separate secret from the client API keys: it does not unlock ``/score``).
- ``POST /score``: ``{"text": "..."}`` -> ``{"verdict": "clean" | "slightly_toxic" | "toxic",
  "request_id": ...}``. Needs a valid API key in the ``X-API-Key`` header, otherwise the answer is
  ``401``. The score, the label probabilities and the decision thresholds stay server-side (see
  ``verdict.py``): they are what an attacker needs to copy or evade the model.

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
from importlib import resources
from pathlib import Path
from typing import Annotated

import spacy
import torch
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tp1_digital_social_score.body_limit import MAX_BODY_BYTES, MaxBodySizeMiddleware
from tp1_digital_social_score.concurrency_limit import (
    MAX_IN_FLIGHT_SCORES,
    ConcurrencyLimitMiddleware,
)
from tp1_digital_social_score.lstm_model import LstmPredictor
from tp1_digital_social_score.metrics import (
    CONTENT_TYPE_LATEST,
    IGNORED_ROUTES,
    UNMATCHED_ROUTE,
    ApiMetrics,
)
from tp1_digital_social_score.scoring import (
    load_thresholds,
    load_weights,
    toxicity_score,
)
from tp1_digital_social_score.security import (
    API_KEY_HEADER,
    ApiKeyStore,
    load_api_keys,
    load_metrics_token,
    metrics_token_matches,
)
from tp1_digital_social_score.security_headers import SecurityHeadersMiddleware
from tp1_digital_social_score.text_pipeline import load_nlp, prepare_texts
from tp1_digital_social_score.verdict import Verdict, verdict_of

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 5000  # the longest comment of the Jigsaw dataset has 5000 characters
DEFAULT_TORCH_THREADS = 2
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Test page served at "/" (static/): it only talks to this same origin, loads nothing from
# elsewhere and runs no inline script, so the policy can forbid everything else.
UI_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
UI_HEADERS = {
    "Content-Security-Policy": UI_CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
UI_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


def load_ui_files() -> dict[str, bytes]:
    """Read the test page once at startup (``static/`` ships inside the installed package)."""
    static = resources.files("tp1_digital_social_score") / "static"
    return {name: (static / name).read_bytes() for name in UI_FILES}


Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)
]


class ScoreRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"text": "You are a stupid idiot and nobody cares about your edits."}]
        }
    )

    text: Annotated[Text, Field(description="Texte à évaluer (anglais), 1 à 5 000 caractères")]


class ScoreResponse(BaseModel):
    """What the caller gets: a coarse verdict only, never the score or the label probabilities."""

    verdict: Verdict
    request_id: str


@dataclass(frozen=True)
class Assessment:
    """Result for one text. ``score`` is for the logs and metrics only, it is never returned."""

    verdict: Verdict
    score: float


class HealthResponse(BaseModel):
    status: str


@dataclass
class ScoringService:
    """Everything needed to score texts: loaded once at startup, shared by all requests."""

    nlp: spacy.language.Language
    predictor: LstmPredictor
    weights: dict[str, float]
    thresholds: dict[str, float | None]
    lock: threading.Lock
    metrics: ApiMetrics

    def score(self, texts: Sequence[str]) -> list[Assessment]:
        """Anonymise, clean and score raw texts."""
        # spaCy pipelines are not guaranteed thread-safe: one request at a time uses the models.
        with self.lock:
            # Timed once the lock is held: the metric is the work itself, without the queueing.
            started = time.perf_counter()
            prepared = prepare_texts(texts, self.nlp, self.predictor.vocab)
            probabilities = self.predictor.predict(prepared)
            self.metrics.inference_latency.observe(time.perf_counter() - started)
        scores = toxicity_score(probabilities, self.weights)
        self.metrics.observe_scores([float(score) for score in scores])
        return [
            Assessment(
                verdict=verdict_of([float(p) for p in row], float(score), self.thresholds),
                score=round(float(score), 2),
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
            thresholds=load_thresholds(directory),
            lock=threading.Lock(),
            metrics=metrics,
        )
        logger.info("Scoring service ready")
        yield

    app = FastAPI(
        title="Digital Social Score API",
        version="2.1",
        description=(
            "Score de toxicité d'un texte, de 0 (inoffensif) à 100 (très toxique).\n\n"
            "Cliquez sur **Authorize** et saisissez votre clé API (en-tête `X-API-Key`), puis "
            "« Try it out » sur `POST /score`. Une page de test plus simple est disponible "
            "sur [`/`](/).\n\nLe texte est anonymisé avant le modèle, n'est jamais stocké ni "
            "journalisé."
        ),
        lifespan=lifespan,
    )
    ui_files = load_ui_files()
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_BODY_BYTES)
    # Added after the body limit, so it wraps it: a shed request is refused before its body is read.
    app.add_middleware(
        ConcurrencyLimitMiddleware,
        max_in_flight=MAX_IN_FLIGHT_SCORES,
        on_shed=metrics.shed.inc,
    )
    logger.info("Load shedding on /score above %d requests in flight", MAX_IN_FLIGHT_SCORES)
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

    # Added last, so it is the outermost layer: even the 413 and 503 answers of the middlewares
    # below carry the headers.
    app.add_middleware(SecurityHeadersMiddleware)

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

    # Test page: static files, no key needed to load it (the key is typed into the page and only
    # sent with each /score call). `async def` like /health: never queued behind /score.
    @app.get("/", include_in_schema=False)
    async def ui_page() -> HTMLResponse:
        return HTMLResponse(ui_files["index.html"], headers=UI_HEADERS)

    @app.get("/ui/{name}", include_in_schema=False)
    async def ui_asset(name: str) -> Response:
        if name == "index.html" or name not in UI_FILES:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(ui_files[name], media_type=UI_FILES[name], headers=UI_HEADERS)

    @app.post(
        "/score",
        response_model=ScoreResponse,
        summary="Évaluer la toxicité d'un texte",
        description=(
            "Renvoie un verdict (`clean`, `slightly_toxic` ou `toxic`) et l'identifiant de la "
            "requête (repris de `X-Request-ID` s'il est sûr). Ni score, ni probabilités, ni "
            "seuils : ils restent côté serveur."
        ),
        responses={
            401: {"description": "Clé API absente ou invalide"},
            413: {"description": "Corps de requête supérieur à 64 Kio"},
            422: {"description": "Texte vide, absent ou supérieur à 5 000 caractères"},
        },
    )
    def score(
        body: ScoreRequest, request: Request, client_id: Annotated[str, Depends(authenticate)]
    ) -> ScoreResponse:
        started = time.perf_counter()
        request_id = request_id_from(request)
        assessment = get_service(request).score([body.text])[0]
        logger.info(
            "score request_id=%s client=%s chars=%d verdict=%s score=%.2f latency_ms=%.1f",
            request_id,
            client_id,
            len(body.text),
            assessment.verdict,
            assessment.score,
            1000 * (time.perf_counter() - started),
        )
        return ScoreResponse(request_id=request_id, verdict=assessment.verdict)

    return app


app = create_app()
