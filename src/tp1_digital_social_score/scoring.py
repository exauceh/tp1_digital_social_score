"""Digital Social Score: weighted combination of the six toxicity label probabilities.

``score = 100 x sum(weight_label x probability_label)``, with weights summing to 1. The weights
are a documented design choice (see the notebook, section 7), not learned values. They can be
overridden by a ``score_weights.json`` file next to the model artifacts.
"""

import json
import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

FloatArray = NDArray[np.float32]

LABELS: tuple[str, ...] = (
    "toxic",
    "severe_toxic",
    "obscene",
    "threat",
    "insult",
    "identity_hate",
)

# Same values as SCORE_WEIGHTS in train.ipynb (section 7).
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "toxic": 0.40,
    "severe_toxic": 0.20,
    "obscene": 0.05,
    "threat": 0.15,
    "insult": 0.10,
    "identity_hate": 0.10,
}


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Check that the weights cover every label, are not negative and sum to 1."""
    if set(weights) != set(LABELS):
        raise ValueError(f"Score weights must cover exactly these labels: {LABELS}")
    if any(value < 0 for value in weights.values()):
        raise ValueError("Score weights must not be negative")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("Score weights must sum to 1")
    return weights


def load_weights(model_dir: Path) -> dict[str, float]:
    """Load ``score_weights.json`` from the model directory or its parent, else use the defaults."""
    for candidate in (model_dir / "score_weights.json", model_dir.parent / "score_weights.json"):
        if candidate.exists():
            weights = validate_weights(json.loads(candidate.read_text(encoding="utf-8")))
            logger.info("Score weights loaded from %s", candidate)
            return weights
    logger.info("No score_weights.json found next to the model, using the default weights")
    return validate_weights(dict(DEFAULT_SCORE_WEIGHTS))


def load_thresholds(model_dir: Path) -> dict[str, float | None]:
    """Decision threshold of each label, calibrated at training time (``metrics.json``).

    ``None`` for a label whose threshold is not recorded: the thresholds are only used to explain
    a result (test page), never to compute the score, so a missing one is not an error.
    """
    path = model_dir / "metrics.json"
    per_label: dict[str, dict[str, float]] = {}
    if path.exists():
        per_label = json.loads(path.read_text(encoding="utf-8")).get("per_label", {})
    thresholds: dict[str, float | None] = {}
    for label in LABELS:
        value = per_label.get(label, {}).get("threshold")
        thresholds[label] = round(float(value), 4) if value is not None else None
    return thresholds


def toxicity_score(probs: FloatArray, weights: dict[str, float]) -> FloatArray:
    """Digital Social Score in [0, 100] for label probabilities of shape (n_rows, 6)."""
    weight_vector = np.array([weights[label] for label in LABELS], dtype=np.float32)
    scores = 100.0 * (probs @ weight_vector)
    return np.asarray(np.clip(scores, 0.0, 100.0), dtype=np.float32)
