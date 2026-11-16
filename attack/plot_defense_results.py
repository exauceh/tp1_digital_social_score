"""Draws the TP2 étape 3 comparison chart: original vs. attacked vs. attacked-and-defended.

Usage::

    uv run --extra attack python -I attack/plot_defense_results.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt

RESULTS_DIR = Path(__file__).resolve().parent / "results"

SERIES = [
    ("original_no_defense", "Original"),
    ("perturbed_no_defense", "Attaqué, sans défense"),
    ("perturbed_with_defense", "Attaqué, avec défense"),
]


def main() -> int:
    summary = json.loads((RESULTS_DIR / "defense_summary.json").read_text(encoding="utf-8"))
    conditions = summary["conditions"]

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    ratio_labels = ["Précision", "Rappel", "Taux de faux positifs"]
    ratio_keys = ["precision", "recall", "false_positive_rate"]
    x = range(len(ratio_labels))
    width = 0.25
    offsets = [-width, 0, width]
    for (key, label), offset in zip(SERIES, offsets, strict=True):
        values = [conditions[key][metric] for metric in ratio_keys]
        axes[0].bar([i + offset for i in x], values, width, label=label)
    axes[0].set_xticks(list(x), ratio_labels)
    axes[0].set_ylim(0, 1)
    axes[0].set_title("Qualité de détection")
    axes[0].legend(fontsize=8)

    latency_labels = ["Latence moyenne (ms)", "Latence P95 (ms)"]
    latency_keys = ["latency_ms_mean", "latency_ms_p95"]
    x2 = range(len(latency_labels))
    for (key, label), offset in zip(SERIES, offsets, strict=True):
        values = [conditions[key][metric] for metric in latency_keys]
        axes[1].bar([i + offset for i in x2], values, width, label=label)
    axes[1].set_xticks(list(x2), latency_labels)
    axes[1].set_title("Latence (inférence seule)")
    axes[1].legend(fontsize=8)

    evaded = summary["toxic_comments_that_evaded_detection_without_defense"]
    recovered = summary["of_those_recovered_by_the_defense"]
    total = summary["toxic_comments_total"]
    fig.suptitle(
        f"Défense contre l'attaque adversariale : {recovered}/{evaded} commentaires toxiques\n"
        f"qui avaient échappé à la détection sont de nouveau détectés ({total} au total)",
        fontsize=11,
    )
    fig.tight_layout()
    output = RESULTS_DIR / "defense_comparison.png"
    fig.savefig(output, dpi=150)
    print(f"Chart written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
