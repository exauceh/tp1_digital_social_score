"""The coarse verdict the API returns instead of the model's raw outputs.

Exact per-label probabilities, a 0-100 score and the decision thresholds are exactly what an
attacker needs to copy the model by querying it (model extraction) and to tune an evasion just
under the decision boundary (adversarial examples). The API therefore answers with one of three
levels and keeps the numbers server-side, for the logs and the metrics.

Rule, in two steps:

1. A text is *flagged* when any label's probability exceeds that label's own threshold, calibrated
   at training time (``metrics.json``): the same operating point the project's evaluation uses.
2. A flagged text is ``toxic`` when its weighted score reaches ``TOXIC_MIN_SCORE`` or when it is
   flagged on a severe label (``SEVERE_LABELS``), otherwise ``slightly_toxic``.

Both numbers come from a 1 000-comment sample of the Jigsaw test set. Flagged comments scoring 50
or more: 201 of 201 really toxic. The score alone under-rates a threat or a hateful remark, because
those labels weigh little in it (0.15 and 0.10): flagging them on the severe labels promotes 13
more comments to ``toxic``, 11 of them really toxic (85 %), for 214 toxic verdicts at 99 %
precision overall. One sample, so the cut is a reasoned choice, not a tuned optimum.

An unflagged text is ``clean``.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum

from tp1_digital_social_score.scoring import LABELS

TOXIC_MIN_SCORE = 50.0
SEVERE_LABELS = frozenset({"severe_toxic", "threat", "identity_hate"})
# Used for a label whose calibrated threshold is not recorded (e.g. a model without metrics.json).
FALLBACK_THRESHOLD = 0.5


class Verdict(StrEnum):
    CLEAN = "clean"
    SLIGHTLY_TOXIC = "slightly_toxic"
    TOXIC = "toxic"


def verdict_of(
    probabilities: Sequence[float], score: float, thresholds: Mapping[str, float | None]
) -> Verdict:
    """Verdict for one text: its six probabilities (in ``LABELS`` order) and its 0-100 score."""
    over = {
        label
        for label, probability in zip(LABELS, probabilities, strict=True)
        if probability > (thresholds.get(label) or FALLBACK_THRESHOLD)
    }
    if not over:
        return Verdict.CLEAN
    if score >= TOXIC_MIN_SCORE or over & SEVERE_LABELS:
        return Verdict.TOXIC
    return Verdict.SLIGHTLY_TOXIC
