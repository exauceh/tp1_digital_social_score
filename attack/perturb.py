"""Text perturbations for the adversarial-example attack (TP2, course n°5 section 15).

Each function changes a text just enough that a human reader still understands it (same
meaning, same intent), while trying to break the tokens our pipeline relies on: regex
anonymisation, spaCy NER, and the LSTM's word-level vocabulary (``LstmPredictor.encode_texts``
splits on whitespace and looks each token up in a fixed vocabulary — any token it does not know
becomes the single ``<unk>`` embedding, which loses whatever that word meant to the model).
"""

from __future__ import annotations

import random
import re

# A few Cyrillic/Greek look-alikes of common Latin letters: visually almost identical, but a
# different Unicode code point, so the tokeniser cannot match them against the vocabulary learned
# from Latin-script training data.
HOMOGLYPHS: dict[str, str] = {
    "a": "а",  # U+0430 CYRILLIC SMALL LETTER A
    "e": "е",  # U+0435 CYRILLIC SMALL LETTER IE
    "o": "о",  # U+043E CYRILLIC SMALL LETTER O
    "p": "р",  # U+0440 CYRILLIC SMALL LETTER ER
    "c": "с",  # U+0441 CYRILLIC SMALL LETTER ES
    "i": "і",  # U+0456 CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
}
LEETSPEAK: dict[str, str] = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5"}
ZERO_WIDTH_SPACE = "​"
WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


def _perturb_word(word: str, rng: random.Random) -> str:
    """Apply one random, meaning-preserving change to a single word of 4+ letters."""
    if len(word) < 4:
        return word
    technique = rng.choice(["homoglyph", "leetspeak", "swap", "double", "zero_width"])
    chars = list(word)
    if technique == "homoglyph":
        candidates = [i for i, c in enumerate(chars) if c.lower() in HOMOGLYPHS]
        if candidates:
            i = rng.choice(candidates)
            chars[i] = HOMOGLYPHS[chars[i].lower()]
    elif technique == "leetspeak":
        candidates = [i for i, c in enumerate(chars) if c.lower() in LEETSPEAK]
        if candidates:
            i = rng.choice(candidates)
            chars[i] = LEETSPEAK[chars[i].lower()]
    elif technique == "swap":
        # Adjacent-letter transposition, never the first or last letter: readable typo (common in
        # real evasion attempts), and it still looks like a word, unlike a random letter drop.
        if len(chars) >= 4:
            i = rng.randint(1, len(chars) - 3)
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
    elif technique == "double":
        i = rng.randint(0, len(chars) - 1)
        chars.insert(i, chars[i])
    else:  # zero_width: invisible to a human, but splits the word for a naive tokeniser
        i = rng.randint(1, len(chars) - 1)
        chars.insert(i, ZERO_WIDTH_SPACE)
    return "".join(chars)


def perturb_text(text: str, rate: float = 0.5, seed: int | None = None) -> str:
    """Perturb a random subset (``rate``) of words of 4+ letters, keeping the text readable.

    Deterministic for a given ``(text, rate, seed)``, so a run can be reproduced exactly.
    """
    rng = random.Random(seed)  # noqa: S311 (reproducible test data, not cryptography)
    words = WORD_PATTERN.findall(text)
    eligible = [w for w in words if len(w) >= 4]
    # Approximate: this samples among distinct word *strings* here but matches by string below, so
    # a repeated word is perturbed at every occurrence or none — close enough to `rate` for a demo.
    to_perturb = set(rng.sample(eligible, k=round(len(eligible) * rate))) if eligible else set()

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        return _perturb_word(word, rng) if word in to_perturb else word

    return WORD_PATTERN.sub(replace, text)
