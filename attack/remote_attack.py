"""TP2 — the adversarial-example attack replayed against another team's deployed API.

Same sample, same perturbation and same metrics as ``adversarial_attack.py``, but the verdict
comes from the remote ``POST /score`` (its own ``toxic`` flag) instead of a local model. The API
key is read from the ``TARGET_API_KEY`` environment variable (never a file, never logged).

Usage::

    $env:TARGET_API_KEY = "..."
    uv run python -I attack/remote_attack.py --url http://HOST/score --sample-size 200
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "attack"))  # python -I does not add the script's own directory

from adversarial_attack import compute_metrics, load_labeled_sample  # noqa: E402
from perturb import perturb_text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REQUEST_PAUSE_SECONDS = (
    0.05  # one request at a time, no burst: this is a measurement, not a load test
)
TIMEOUT_SECONDS = 10.0


def score_remote(
    client: httpx.Client, url: str, texts: list[str]
) -> tuple[np.ndarray, np.ndarray, list[float], list[float]]:
    """(flagged, scores, round-trip ms, server-reported ms) for each text, one request each."""
    flagged: list[bool] = []
    scores: list[float] = []
    round_trip: list[float] = []
    server: list[float] = []
    for text in texts:
        started = time.perf_counter()
        response = client.post(url, json={"text": text})
        round_trip.append(1000 * (time.perf_counter() - started))
        response.raise_for_status()
        body = response.json()
        flagged.append(bool(body["toxic"]))
        scores.append(float(body["score"]))
        server.append(float(body["latency_ms"]))
        time.sleep(REQUEST_PAUSE_SECONDS)
    return np.array(flagged), np.array(scores), round_trip, server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="full URL of the target's /score endpoint")
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--rate", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True, help="directory for the results")
    args = parser.parse_args()

    api_key = os.environ.get("TARGET_API_KEY", "")
    if not api_key:
        logger.error("Set TARGET_API_KEY in the environment")
        return 2
    args.output.mkdir(parents=True, exist_ok=True)

    sample = load_labeled_sample(args.sample_size, args.seed)
    originals = sample["comment_text"].tolist()
    perturbed = [
        perturb_text(text, rate=args.rate, seed=args.seed * 1_000_000 + i)
        for i, text in enumerate(originals)
    ]
    is_toxic = sample["is_toxic"].to_numpy()

    summary: dict[str, object] = {
        "target": args.url,
        "sample_size": len(sample),
        "perturbation_rate": args.rate,
        "seed": args.seed,
    }
    with httpx.Client(headers={"X-API-Key": api_key}, timeout=TIMEOUT_SECONDS) as client:
        flagged: dict[str, np.ndarray] = {}
        for name, texts in (("before", originals), ("after", perturbed)):
            logger.info("Sending %d '%s' texts to the target...", len(texts), name)
            is_flagged, scores, round_trip, server = score_remote(client, args.url, texts)
            flagged[name] = is_flagged
            metrics = compute_metrics(is_flagged, is_toxic, scores)
            summary[name] = {
                "precision": metrics.precision,
                "recall": metrics.recall,
                "false_positive_rate": metrics.false_positive_rate,
                "accuracy": metrics.accuracy,
                "mean_score": metrics.mean_score,
                "round_trip_ms_mean": float(np.mean(round_trip)),
                "round_trip_ms_p95": float(np.percentile(round_trip, 95)),
                "server_latency_ms_mean": float(np.mean(server)),
            }
    evaded = is_toxic & flagged["before"] & ~flagged["after"]
    summary["toxic_comments_that_evaded_detection"] = int(np.sum(evaded))
    summary["toxic_comments_total"] = int(np.sum(is_toxic))

    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Results written to %s", args.output)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
