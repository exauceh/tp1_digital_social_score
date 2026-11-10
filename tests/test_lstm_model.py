"""The BiLSTM predictor: loading, shapes, and independence from batch padding."""

from pathlib import Path

import numpy as np
import pytest

from tp1_digital_social_score.lstm_model import LstmPredictor
from tp1_digital_social_score.scoring import LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_MODEL_DIR = PROJECT_ROOT / "models" / "lstm"


def test_predict_shape_and_range(artifacts_dir: Path) -> None:
    predictor = LstmPredictor(artifacts_dir)
    probs = predictor.predict(["you are an idiot", "thanks", ""])
    assert probs.shape == (3, len(LABELS))
    assert probs.dtype == np.float32
    assert ((probs >= 0) & (probs <= 1)).all()


def test_prediction_does_not_depend_on_the_batch(artifacts_dir: Path) -> None:
    """Padding must not change a text's probabilities."""
    predictor = LstmPredictor(artifacts_dir)
    alone = predictor.predict(["you are an idiot"])[0]
    in_batch = predictor.predict(["thanks", "you are an idiot", "tag_person " * 15])[1]
    np.testing.assert_allclose(alone, in_batch, atol=1e-5)


def test_unknown_words_and_empty_text_do_not_crash(artifacts_dir: Path) -> None:
    predictor = LstmPredictor(artifacts_dir)
    probs = predictor.predict(["zzzz qqqq", "", "word " * 500])
    assert probs.shape == (3, len(LABELS))


@pytest.mark.skipif(not REAL_MODEL_DIR.exists(), reason="trained LSTM artifacts not available")
def test_trained_model_ranks_an_insult_above_a_polite_text() -> None:
    """Smoke test on the real artifacts (weights saved on GPU, loaded on CPU)."""
    predictor = LstmPredictor(REAL_MODEL_DIR)
    insult, polite = predictor.predict(["you are a stupid idiot", "thank you for the help"])
    assert insult[LABELS.index("insult")] > polite[LABELS.index("insult")]
    assert insult[LABELS.index("toxic")] > polite[LABELS.index("toxic")]
