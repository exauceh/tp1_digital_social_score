"""Defense against the adversarial-example attack (TP2 étape 3).

The attack (see ``attack/perturb.py``) changes one word at a time with exactly one of five
techniques: a homoglyph substitution, a leetspeak substitution, an adjacent-letter transposition,
a doubled letter (insertion), or an invisible zero-width space (insertion). Four of those five are,
by construction, exactly one Damerau-Levenshtein edit away from the original word — substitution,
transposition and insertion are each one edit — so a single generic mechanism, correcting any
out-of-vocabulary token to the vocabulary word one edit away from it, undoes all four at once,
regardless of which one was used.

The fifth, the zero-width space, cannot be fixed that way: by the time a perturbed word reaches
tokenisation, ``clean_data``'s own cleaning (``NON_WORD_PATTERN = r"[^\\w\\s]"``) has already turned
the invisible character into a visible space, splitting the word into two separate whitespace
tokens — there is no longer a single token to apply an edit-distance correction to. It needs an
explicit strip, applied before that splitting happens (``defend_raw_text``), not after.
"""

import re
import string

MIN_CORRECTION_LENGTH = 4  # the attack never perturbs words shorter than this either
MAX_CORRECTION_LENGTH = 20  # longer than any real English word: skip to bound worst-case cost
ALPHABET = string.ascii_lowercase

# U+200B zero-width space (the one the attack uses), plus the other invisible joiners/BOM that the
# same trick could use: none of them is ever meaningful in English prose.
ZERO_WIDTH_PATTERN = re.compile("[​‌‍﻿]")


def defend_raw_text(text: str) -> str:
    """Strip invisible characters before ``clean_text`` turns them into a word-splitting space."""
    return ZERO_WIDTH_PATTERN.sub("", text)


def _edits1(word: str) -> set[str]:
    """Every string one deletion, transposition, substitution or insertion away from ``word``."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = {left + right[1:] for left, right in splits if right}
    transposes = {
        left + right[1] + right[0] + right[2:] for left, right in splits if len(right) > 1
    }
    replaces = {left + letter + right[1:] for left, right in splits if right for letter in ALPHABET}
    inserts = {left + letter + right for left, right in splits for letter in ALPHABET}
    return deletes | transposes | replaces | inserts


def _correct_token(token: str, vocab: dict[str, int]) -> str:
    """``token`` unchanged if known or too short/long, else the vocabulary word one edit away."""
    if token in vocab or not (MIN_CORRECTION_LENGTH <= len(token) <= MAX_CORRECTION_LENGTH):
        return token
    for candidate in sorted(_edits1(token)):
        if candidate in vocab:
            return candidate
    return token


def correct_unknown_words(text: str, vocab: dict[str, int]) -> str:
    """Replace every token unknown to the model's vocabulary by its closest single-edit match."""
    return " ".join(_correct_token(token, vocab) for token in text.split())
