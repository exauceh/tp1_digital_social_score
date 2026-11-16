"""TP2 étape 3 — measure the adversarial-example defense (see ``src/tp1_digital_social_score/
defense.py``) against the same attack and sample as ``adversarial_attack.py`` (étape 2).

Re-scores the same texts under four conditions (original/perturbed x without/with the defense) so
the defense's effect can be read in two ways: does it recover the performance lost to the attack
(perturbed, without vs. with), and does it cost anything on traffic that was never attacked
(original, without vs. with)?

Usage::

    uv run python -I attack/defense_eval.py [--sample-size 500] [--rate 0.5] [--seed 0]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "attack"))  # python -I does not add the script's own directory

from adversarial_attack import (  # noqa: E402  (needs the sys.path tweak above)
    MODEL_DIR,
    compute_metrics,
    flagged,
    load_labeled_sample,
    measure_latency,
    score_texts,
)
from perturb import perturb_text  # noqa: E402

from tp1_digital_social_score.lstm_model import LstmPredictor  # noqa: E402
from tp1_digital_social_score.scoring import LABELS, load_weights  # noqa: E402
from tp1_digital_social_score.text_pipeline import load_nlp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATENCY_SAMPLE_SIZE = 50


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--rate", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    model_metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    thresholds = {label: model_metrics["per_label"][label]["threshold"] for label in LABELS}

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
    vocab = predictor.vocab

    conditions = {
        "original_no_defense": (original_texts, None),
        "original_with_defense": (original_texts, vocab),
        "perturbed_no_defense": (perturbed_texts, None),
        "perturbed_with_defense": (perturbed_texts, vocab),
    }

    results: dict[str, dict[str, float]] = {}
    for name, (texts, condition_vocab) in conditions.items():
        logger.info("Scoring condition '%s' (%d texts)...", name, len(texts))
        probabilities, scores = score_texts(texts, nlp, predictor, weights, condition_vocab)
        is_flagged = flagged(probabilities, thresholds)
        metrics = compute_metrics(is_flagged, is_toxic, scores)
        latency_subset = texts[:LATENCY_SAMPLE_SIZE]
        latency = measure_latency(latency_subset, nlp, predictor, weights, condition_vocab)
        results[name] = {
            "precision": metrics.precision,
            "recall": metrics.recall,
            "false_positive_rate": metrics.false_positive_rate,
            "accuracy": metrics.accuracy,
            "mean_score": metrics.mean_score,
            "latency_ms_mean": 1000 * float(np.mean(latency)),
            "latency_ms_p95": 1000 * float(np.percentile(latency, 95)),
        }

    # Of the comments the attack alone made evade detection, how many does the defense recover?
    probabilities_before, _ = score_texts(original_texts, nlp, predictor, weights, None)
    probabilities_attacked, _ = score_texts(perturbed_texts, nlp, predictor, weights, None)
    probabilities_defended, _ = score_texts(perturbed_texts, nlp, predictor, weights, vocab)
    flagged_before = flagged(probabilities_before, thresholds)
    flagged_attacked = flagged(probabilities_attacked, thresholds)
    flagged_defended = flagged(probabilities_defended, thresholds)
    evaded = is_toxic & flagged_before & ~flagged_attacked
    evaded_without_defense = int(np.sum(evaded))
    recovered_by_defense = int(np.sum(evaded & flagged_defended))

    summary = {
        "sample_size": len(sample),
        "perturbation_rate": args.rate,
        "seed": args.seed,
        "conditions": results,
        "toxic_comments_that_evaded_detection_without_defense": evaded_without_defense,
        "of_those_recovered_by_the_defense": recovered_by_defense,
        "toxic_comments_total": int(np.sum(is_toxic)),
    }
    summary_path = RESULTS_DIR / "defense_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Results written to %s", RESULTS_DIR)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
