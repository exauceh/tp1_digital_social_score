"""Draws the comparison chart asked by the TP (precision, FPR, latency, before/after).

Usage::

    uv run --extra attack python -I attack/plot_results.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main() -> int:
    summary = json.loads((RESULTS_DIR / "summary.json").read_text(encoding="utf-8"))
    before, after = summary["before"], summary["after"]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    ratio_labels = ["Précision", "Rappel", "Taux de faux positifs"]
    ratio_keys = ["precision", "recall", "false_positive_rate"]
    x = range(len(ratio_labels))
    width = 0.35
    axes[0].bar([i - width / 2 for i in x], [before[k] for k in ratio_keys], width, label="Avant")
    axes[0].bar([i + width / 2 for i in x], [after[k] for k in ratio_keys], width, label="Après")
    axes[0].set_xticks(list(x), ratio_labels)
    axes[0].set_ylim(0, 1)
    axes[0].set_title("Qualité de détection")
    axes[0].legend()

    latency_labels = ["Latence moyenne (ms)", "Latence P95 (ms)"]
    latency_keys = ["latency_ms_mean", "latency_ms_p95"]
    x2 = range(len(latency_labels))
    before_latency = [before[k] for k in latency_keys]
    after_latency = [after[k] for k in latency_keys]
    axes[1].bar([i - width / 2 for i in x2], before_latency, width, label="Avant")
    axes[1].bar([i + width / 2 for i in x2], after_latency, width, label="Après")
    axes[1].set_xticks(list(x2), latency_labels)
    axes[1].set_title("Latence (inférence seule)")
    axes[1].legend()

    fig.suptitle(
        f"Attaque adversariale : {summary['sample_size']} commentaires, "
        f"{summary['toxic_comments_that_evaded_detection']}/{summary['toxic_comments_total']} "
        "toxiques ont échappé à la détection"
    )
    fig.tight_layout()
    output = RESULTS_DIR / "comparison.png"
    fig.savefig(output, dpi=150)
    print(f"Chart written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
