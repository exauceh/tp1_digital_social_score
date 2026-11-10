"""Digital Social Score API: receives a text, returns a toxicity score from 0 to 100.

Routes:

- ``GET  /health``: startup, readiness and liveness probe (Kubernetes).
- ``POST /score``: ``{"text": "..."}`` -> ``{"score": ..., "labels": {...}, "request_id": ...}``.

RGPD: the received text is anonymised (regex + spaCy NER) before it reaches the model, it is
never stored, and it is never written to the logs. Only a request id, the text length, the score
and the latency are logged.

Configuration (environment variables): ``MODEL_DIR`` (artifacts directory, default
``models/lstm``), ``LOG_LEVEL`` (default ``INFO``) and ``TORCH_NUM_THREADS`` (default 2: measured
on CPU, using every core makes this small LSTM slower and its latency erratic).
"""

import logging
import os
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import spacy
import torch
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field, StringConstraints

from tp1_digital_social_score.lstm_model import LstmPredictor
from tp1_digital_social_score.scoring import LABELS, load_weights, toxicity_score
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

    def score(self, texts: Sequence[str]) -> list[Prediction]:
        """Anonymise, clean and score raw texts."""
        # spaCy pipelines are not guaranteed thread-safe: one request at a time uses the models.
        with self.lock:
            prepared = prepare_texts(texts, self.nlp)
            probabilities = self.predictor.predict(prepared)
        scores = toxicity_score(probabilities, self.weights)
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


def create_app(model_dir: Path | None = None) -> FastAPI:
    """Build the application; the models are loaded when the server starts."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
        )
        logger.info("Scoring service ready")
        yield

    app = FastAPI(title="Digital Social Score API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post("/score", response_model=ScoreResponse)
    def score(body: ScoreRequest, request: Request) -> ScoreResponse:
        started = time.perf_counter()
        request_id = request_id_from(request)
        prediction = get_service(request).score([body.text])[0]
        logger.info(
            "score request_id=%s chars=%d score=%.2f latency_ms=%.1f",
            request_id,
            len(body.text),
            prediction.score,
            1000 * (time.perf_counter() - started),
        )
        return ScoreResponse(request_id=request_id, **prediction.model_dump())

    return app


app = create_app()
