"""TP2 étape 4 — measure a known bias of toxicity classifiers: false positives on identity terms.

Jigsaw-style models learn that words naming a group (``gay``, ``muslim``, ``black``...)
co-occur with toxic comments in the training data, so they tend to flag *clean* comments that
merely mention one. This script measures it: among comments that are really clean (all six
labels 0), the false-positive rate on those that mention an identity term versus those that do
not. The model runs exactly as in the API (anonymisation, defense, LSTM), with the model's own
per-label thresholds.

Usage::

    uv run python -I compliance/bias_check.py [--per-group 1000] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import spacy

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from tp1_digital_social_score.lstm_model import LstmPredictor  # noqa: E402
from tp1_digital_social_score.scoring import LABELS  # noqa: E402
from tp1_digital_social_score.text_pipeline import load_nlp, prepare_texts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MODEL_DIR = _ROOT / "models" / "lstm"
RESULTS_DIR = Path(__file__).resolve().parent
IDENTITY_TERMS = [
    "gay", "lesbian", "homosexual", "muslim", "islam", "jewish", "jew", "christian", "black",
    "white", "asian", "woman", "women", "man", "men", "transgender", "trans", "disabled",
]  # fmt: skip
IDENTITY_PATTERN = re.compile(r"\b(?:" + "|".join(IDENTITY_TERMS) + r")\b", re.IGNORECASE)
BATCH_SIZE = 250


def false_positive_rate(
    texts: list[str],
    nlp: spacy.language.Language,
    predictor: LstmPredictor,
    thresholds: np.ndarray,
) -> float:
    """Share of really-clean texts the model flags (any label above its own threshold)."""
    flagged: list[bool] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        prepared = prepare_texts(batch, nlp, predictor.vocab)
        probabilities = predictor.predict(prepared)
        flagged.extend(bool(x) for x in (probabilities > thresholds).any(axis=1))
    return float(np.mean(flagged))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-group", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    labels = pd.read_csv(_ROOT / "raw_data" / "test_labels.csv")
    labels = labels[(labels[list(LABELS)] != -1).all(axis=1)]
    clean = labels[(labels[list(LABELS)] == 0).all(axis=1)]
    merged = clean.merge(pd.read_csv(_ROOT / "raw_data" / "test.csv"), on="id")
    mentions = merged["comment_text"].str.contains(IDENTITY_PATTERN)
    with_terms, without_terms = merged[mentions], merged[~mentions]
    logger.info(
        "Clean test comments: %d mention an identity term, %d do not",
        len(with_terms),
        len(without_terms),
    )

    metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    thresholds = np.array(
        [metrics["per_label"][label]["threshold"] for label in LABELS], np.float32
    )
    nlp = load_nlp()
    predictor = LstmPredictor(MODEL_DIR)

    sample_size = min(args.per_group, len(with_terms), len(without_terms))
    summary: dict[str, object] = {"per_group_sample_size": sample_size, "seed": args.seed}
    rates: dict[str, float] = {}
    for name, group in (
        ("with_identity_term", with_terms),
        ("without_identity_term", without_terms),
    ):
        texts = group.sample(n=sample_size, random_state=args.seed)["comment_text"].tolist()
        rates[name] = false_positive_rate(texts, nlp, predictor, thresholds)
        logger.info("False-positive rate, %s: %.1f%%", name, 100 * rates[name])
    summary["false_positive_rate"] = rates
    summary["ratio"] = rates["with_identity_term"] / rates["without_identity_term"]
    (RESULTS_DIR / "bias_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
