"""TP2 (course n°5, section 15) — adversarial-example attack against the toxicity model.

Measures the impact announced in the TP: precision, false-positive rate, and latency, before and
after perturbing the text (see ``perturb.py``). Runs the exact pipeline used by the API
(anonymise -> clean -> LSTM -> weighted score, see ``api.py`` / ``ScoringService.score``) directly
against the model artifacts, so it needs no running server and no API key, and scales to a
real sample instead of the course's one-sentence example.

Ground truth and "flagged" decision:
    - ground truth: a comment is toxic if any of the 6 Jigsaw labels is 1 (raw_data/test_labels.csv,
      joined to the original raw text in raw_data/test.csv by id).
    - flagged: the model flags a comment if any of its 6 predicted probabilities exceeds that
      label's own calibrated threshold (models/lstm/metrics.json, "per_label"."threshold" — the
      operating point chosen at training time), not an arbitrary cut on the final 0-100 score.

Usage::

    uv run python -I attack/adversarial_attack.py [--sample-size 500] [--rate 0.5] [--seed 0]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import spacy

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "attack"))  # python -I does not add the script's own directory
from perturb import perturb_text  # noqa: E402  (needs the sys.path tweak above)

from tp1_digital_social_score.lstm_model import LstmPredictor  # noqa: E402
from tp1_digital_social_score.scoring import LABELS, load_weights, toxicity_score  # noqa: E402
from tp1_digital_social_score.text_pipeline import load_nlp, prepare_texts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "lstm"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATENCY_SAMPLE_SIZE = 50  # separate, smaller run: one call at a time, to measure real latency


@dataclass
class Metrics:
    precision: float
    recall: float
    false_positive_rate: float
    accuracy: float
    mean_score: float


def load_labeled_sample(sample_size: int, seed: int) -> pd.DataFrame:
    """Balanced sample of the Jigsaw test set: half really toxic, half really clean."""
    labels = pd.read_csv(ROOT / "raw_data" / "test_labels.csv")
    # -1 = excluded from the original Kaggle score, so it is not real ground truth here either.
    labels = labels[(labels[list(LABELS)] != -1).all(axis=1)]
    labels["is_toxic"] = (labels[list(LABELS)] == 1).any(axis=1)
    texts = pd.read_csv(ROOT / "raw_data" / "test.csv")
    merged = labels.merge(texts, on="id", how="inner")

    half = sample_size // 2
    toxic = merged[merged["is_toxic"]].sample(n=half, random_state=seed)
    clean = merged[~merged["is_toxic"]].sample(n=sample_size - half, random_state=seed)
    sample = pd.concat([toxic, clean], ignore_index=True)
    logger.info(
        "Sample: %d comments (%d really toxic, %d really clean)",
        len(sample),
        len(toxic),
        len(clean),
    )
    return sample


def flagged(probabilities: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    """One row per text: True if any label's probability exceeds that label's own threshold."""
    threshold_vector = np.array([thresholds[label] for label in LABELS], dtype=np.float32)
    return np.asarray((probabilities > threshold_vector).any(axis=1))


def compute_metrics(is_flagged: np.ndarray, is_toxic: np.ndarray, scores: np.ndarray) -> Metrics:
    true_positive = int(np.sum(is_flagged & is_toxic))
    false_positive = int(np.sum(is_flagged & ~is_toxic))
    false_negative = int(np.sum(~is_flagged & is_toxic))
    true_negative = int(np.sum(~is_flagged & ~is_toxic))
    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative
    actual_negative = false_positive + true_negative
    precision = true_positive / predicted_positive if predicted_positive else float("nan")
    recall = true_positive / actual_positive if actual_positive else float("nan")
    fpr = false_positive / actual_negative if actual_negative else float("nan")
    accuracy = (true_positive + true_negative) / len(is_toxic)
    return Metrics(precision, recall, fpr, accuracy, float(np.mean(scores)))


def score_texts(
    texts: list[str],
    nlp: spacy.language.Language,
    predictor: LstmPredictor,
    weights: dict[str, float],
    vocab: dict[str, int] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Same steps as ScoringService.score in api.py: anonymise, clean, predict, weigh.

    ``vocab`` turns the defense on (see ``defense.py``); left out, matches the "without defense"
    behaviour this script measured before the defense existed (TP2 étape 2).
    """
    prepared = prepare_texts(texts, nlp, vocab)
    probabilities = predictor.predict(prepared)
    scores = toxicity_score(probabilities, weights)
    return probabilities, scores


def measure_latency(
    texts: list[str],
    nlp: spacy.language.Language,
    predictor: LstmPredictor,
    weights: dict[str, float],
    vocab: dict[str, int] | None = None,
) -> list[float]:
    """Latency of scoring ONE text at a time (what a /score caller actually experiences)."""
    durations = []
    for text in texts:
        started = time.perf_counter()
        score_texts([text], nlp, predictor, weights, vocab)
        durations.append(time.perf_counter() - started)
    return durations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument(
        "--rate", type=float, default=0.5, help="Share of 4+ letter words perturbed"
    )
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    model_metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    thresholds = {label: model_metrics["per_label"][label]["threshold"] for label in LABELS}
    logger.info("Per-label thresholds (model's own calibration): %s", thresholds)

    sample = load_labeled_sample(args.sample_size, args.seed)
    original_texts = sample["comment_text"].tolist()
    perturbed_texts = [
        perturb_text(text, rate=args.rate, seed=args.seed * 1_000_000 + i)
        for i, text in enumerate(original_texts)
    ]
    is_toxic = sample["is_toxic"].to_numpy()

    logger.info("Loading spaCy and the LSTM (same artifacts as the API)...")
    nlp = load_nlp()
    predictor = LstmPredictor(MODEL_DIR)
    weights = load_weights(MODEL_DIR)

    logger.info("Scoring the %d original texts...", len(original_texts))
    probabilities_before, scores_before = score_texts(original_texts, nlp, predictor, weights)
    logger.info("Scoring the %d perturbed texts...", len(perturbed_texts))
    probabilities_after, scores_after = score_texts(perturbed_texts, nlp, predictor, weights)

    flagged_before = flagged(probabilities_before, thresholds)
    flagged_after = flagged(probabilities_after, thresholds)
    metrics_before = compute_metrics(flagged_before, is_toxic, scores_before)
    metrics_after = compute_metrics(flagged_after, is_toxic, scores_after)

    logger.info(
        "Measuring per-request latency on %d comments (one call at a time)...", LATENCY_SAMPLE_SIZE
    )
    latency_subset = original_texts[:LATENCY_SAMPLE_SIZE]
    perturbed_latency_subset = perturbed_texts[:LATENCY_SAMPLE_SIZE]
    latency_before = measure_latency(latency_subset, nlp, predictor, weights)
    latency_after = measure_latency(perturbed_latency_subset, nlp, predictor, weights)

    # How many really-toxic comments evaded detection only once perturbed (the attack's own goal)?
    evaded = int(np.sum(is_toxic & flagged_before & ~flagged_after))

    summary = {
        "sample_size": len(sample),
        "perturbation_rate": args.rate,
        "seed": args.seed,
        "before": {
            "precision": metrics_before.precision,
            "recall": metrics_before.recall,
            "false_positive_rate": metrics_before.false_positive_rate,
            "accuracy": metrics_before.accuracy,
            "mean_score": metrics_before.mean_score,
            "latency_ms_mean": 1000 * float(np.mean(latency_before)),
            "latency_ms_p95": 1000 * float(np.percentile(latency_before, 95)),
        },
        "after": {
            "precision": metrics_after.precision,
            "recall": metrics_after.recall,
            "false_positive_rate": metrics_after.false_positive_rate,
            "accuracy": metrics_after.accuracy,
            "mean_score": metrics_after.mean_score,
            "latency_ms_mean": 1000 * float(np.mean(latency_after)),
            "latency_ms_p95": 1000 * float(np.percentile(latency_after, 95)),
        },
        "toxic_comments_that_evaded_detection": evaded,
        "toxic_comments_total": int(np.sum(is_toxic)),
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    with (RESULTS_DIR / "per_comment.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["is_toxic", "score_before", "flagged_before", "score_after", "flagged_after"]
        )
        columns = (is_toxic, scores_before, flagged_before, scores_after, flagged_after)
        rows = zip(*columns, strict=True)
        for row in rows:
            writer.writerow(row)

    logger.info("Results written to %s", RESULTS_DIR)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
