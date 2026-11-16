"""Text preparation for serving: the same anonymisation and cleaning as the training data.

The model was trained on comments that went through ``process_data`` (regex + spaCy NER
anonymisation) and then ``clean_data`` (Unicode normalisation, placeholders, punctuation, emojis,
case). The API must apply exactly the same steps, in the same order, or the model would see a
different kind of text. The rules are imported from those two modules instead of being copied, so
they cannot drift apart.
"""

import logging
import unicodedata
from collections.abc import Sequence

import spacy

from tp1_digital_social_score.clean_data import (
    APOSTROPHE_PATTERN,
    NON_WORD_PATTERN,
    PLACEHOLDER_PATTERN,
    WHITESPACE_PATTERN,
)
from tp1_digital_social_score.defense import correct_unknown_words, defend_raw_text
from tp1_digital_social_score.process_data import SPACY_MODEL, anonymize_texts

logger = logging.getLogger(__name__)

# Same components excluded as in process_data.main(): only the NER is needed.
SPACY_EXCLUDED_COMPONENTS = ["parser", "lemmatizer", "senter", "attribute_ruler"]


def load_nlp() -> spacy.language.Language:
    """Load the spaCy model used to detect names and places."""
    logger.info("Loading spaCy model %s", SPACY_MODEL)
    return spacy.load(SPACY_MODEL, exclude=SPACY_EXCLUDED_COMPONENTS)


def clean_text(text: str) -> str:
    """Clean one anonymised comment. Mirrors ``clean_data.clean_series`` step by step."""
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = PLACEHOLDER_PATTERN.sub(r"tag_\1", cleaned)
    cleaned = APOSTROPHE_PATTERN.sub("", cleaned)
    cleaned = NON_WORD_PATTERN.sub(" ", cleaned)
    cleaned = cleaned.lower()
    return WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def prepare_texts(
    texts: Sequence[str],
    nlp: spacy.language.Language,
    vocab: dict[str, int] | None = None,
) -> list[str]:
    """Anonymise then clean raw comments, ready to be fed to the model.

    ``vocab`` is the model's own word -> id mapping (``LstmPredictor.vocab``). When given, it turns
    on the adversarial-example defense (see ``defense.py``): invisible characters are stripped
    before cleaning, and any token the model would not recognise is corrected to its closest
    single-edit vocabulary match. Left out (the default), the pipeline behaves exactly as it did
    before the defense existed — used by the attack script to measure the "without defense" case.
    """
    anonymized, _counts = anonymize_texts(texts, nlp, n_process=1)
    if vocab is not None:
        anonymized = [defend_raw_text(text) for text in anonymized]
    cleaned = [clean_text(text) for text in anonymized]
    if vocab is not None:
        cleaned = [correct_unknown_words(text, vocab) for text in cleaned]
    return cleaned
