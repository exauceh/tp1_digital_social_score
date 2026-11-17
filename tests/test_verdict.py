"""The verdict is the only thing the API reveals about the model's decision."""

import pytest

from tp1_digital_social_score.scoring import LABELS
from tp1_digital_social_score.verdict import (
    FALLBACK_THRESHOLD,
    TOXIC_MIN_SCORE,
    Verdict,
    verdict_of,
)

THRESHOLDS: dict[str, float | None] = dict.fromkeys(LABELS, 0.4)


def probabilities(**overrides: float) -> list[float]:
    return [overrides.get(label, 0.0) for label in LABELS]


def test_nothing_above_a_threshold_is_clean() -> None:
    assert verdict_of(probabilities(toxic=0.39), 80.0, THRESHOLDS) is Verdict.CLEAN


def test_a_flagged_text_below_the_score_cut_is_slightly_toxic() -> None:
    result = verdict_of(probabilities(insult=0.6), TOXIC_MIN_SCORE - 0.1, THRESHOLDS)
    assert result is Verdict.SLIGHTLY_TOXIC


def test_a_flagged_text_at_the_score_cut_is_toxic() -> None:
    assert verdict_of(probabilities(insult=0.6), TOXIC_MIN_SCORE, THRESHOLDS) is Verdict.TOXIC


@pytest.mark.parametrize("label", ["severe_toxic", "threat", "identity_hate"])
def test_a_text_flagged_on_a_severe_label_is_toxic_even_with_a_low_score(label: str) -> None:
    assert verdict_of(probabilities(**{label: 0.6}), 15.0, THRESHOLDS) is Verdict.TOXIC


@pytest.mark.parametrize("label", ["toxic", "obscene", "insult"])
def test_a_text_flagged_only_on_a_mild_label_stays_slightly_toxic(label: str) -> None:
    assert verdict_of(probabilities(**{label: 0.6}), 15.0, THRESHOLDS) is Verdict.SLIGHTLY_TOXIC


def test_each_label_uses_its_own_threshold() -> None:
    thresholds: dict[str, float | None] = {**THRESHOLDS, "threat": 0.1}
    assert verdict_of(probabilities(threat=0.2), 10.0, thresholds) is Verdict.TOXIC
    assert verdict_of(probabilities(insult=0.2), 10.0, thresholds) is Verdict.CLEAN


@pytest.mark.parametrize(
    ("probability", "expected"),
    [(FALLBACK_THRESHOLD - 0.01, Verdict.CLEAN), (FALLBACK_THRESHOLD + 0.01, Verdict.TOXIC)],
)
def test_a_label_without_a_recorded_threshold_falls_back(
    probability: float, expected: Verdict
) -> None:
    unknown: dict[str, float | None] = dict.fromkeys(LABELS)
    assert verdict_of(probabilities(toxic=probability), 60.0, unknown) is expected


def test_the_verdict_serialises_as_a_plain_string() -> None:
    assert Verdict.SLIGHTLY_TOXIC.value == "slightly_toxic"
    assert str(Verdict.SLIGHTLY_TOXIC) == "slightly_toxic"
