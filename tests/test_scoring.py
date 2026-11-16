"""Digital Social Score: weighted combination of the six label probabilities."""

import json
from pathlib import Path

import numpy as np
import pytest

from tp1_digital_social_score.scoring import (
    DEFAULT_SCORE_WEIGHTS,
    LABELS,
    load_thresholds,
    load_weights,
    toxicity_score,
    validate_weights,
)


def test_default_weights_are_valid() -> None:
    assert validate_weights(dict(DEFAULT_SCORE_WEIGHTS)) == DEFAULT_SCORE_WEIGHTS


def test_score_bounds() -> None:
    probs = np.array([[0.0] * 6, [1.0] * 6], dtype=np.float32)
    scores = toxicity_score(probs, DEFAULT_SCORE_WEIGHTS)
    assert scores[0] == pytest.approx(0.0)
    assert scores[1] == pytest.approx(100.0)


def test_each_label_contributes_its_weight() -> None:
    for index, label in enumerate(LABELS):
        probs = np.zeros((1, len(LABELS)), dtype=np.float32)
        probs[0, index] = 1.0
        score = toxicity_score(probs, DEFAULT_SCORE_WEIGHTS)[0]
        assert score == pytest.approx(100 * DEFAULT_SCORE_WEIGHTS[label])


def test_severe_labels_weigh_more_than_obscene() -> None:
    assert DEFAULT_SCORE_WEIGHTS["severe_toxic"] > DEFAULT_SCORE_WEIGHTS["obscene"]
    assert DEFAULT_SCORE_WEIGHTS["threat"] > DEFAULT_SCORE_WEIGHTS["obscene"]


@pytest.mark.parametrize(
    "weights",
    [
        {**DEFAULT_SCORE_WEIGHTS, "toxic": 0.5},  # sum is not 1
        {label: weight for label, weight in DEFAULT_SCORE_WEIGHTS.items() if label != "threat"},
        {**DEFAULT_SCORE_WEIGHTS, "toxic": 0.6, "obscene": -0.05},  # negative weight
        {**DEFAULT_SCORE_WEIGHTS, "unknown": 0.0},
    ],
)
def test_invalid_weights_are_rejected(weights: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        validate_weights(weights)


def test_load_weights_defaults_when_no_file(tmp_path: Path) -> None:
    model_dir = tmp_path / "lstm"
    model_dir.mkdir()
    assert load_weights(model_dir) == DEFAULT_SCORE_WEIGHTS


def test_load_weights_reads_a_file_next_to_the_model(tmp_path: Path) -> None:
    model_dir = tmp_path / "lstm"
    model_dir.mkdir()
    custom = {label: 1 / len(LABELS) for label in LABELS}
    (model_dir / "score_weights.json").write_text(json.dumps(custom), encoding="utf-8")
    assert load_weights(model_dir) == pytest.approx(custom)


def test_load_thresholds_reads_the_calibrated_values(tmp_path: Path) -> None:
    per_label = {label: {"threshold": 0.5} for label in LABELS}
    per_label["threat"] = {"threshold": 0.123456}
    del per_label["insult"]  # a missing threshold is reported as None, not an error
    (tmp_path / "metrics.json").write_text(json.dumps({"per_label": per_label}), encoding="utf-8")
    thresholds = load_thresholds(tmp_path)
    assert set(thresholds) == set(LABELS)
    assert thresholds["threat"] == 0.1235
    assert thresholds["insult"] is None
    assert thresholds["toxic"] == 0.5


def test_load_thresholds_without_metrics_file(tmp_path: Path) -> None:
    assert load_thresholds(tmp_path) == dict.fromkeys(LABELS)
