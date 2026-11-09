"""Clean the anonymised ``comment_text`` column before model training (step 2).

Cleaning steps, in this order:

1. Unicode NFKC normalisation (full-width letters, ligatures, ... become plain characters).
2. Anonymisation placeholders such as ``[PERSON]`` are turned into word tokens
   (``tag_person``) so that the punctuation removal does not destroy them.
3. Apostrophes are dropped (``don't`` -> ``dont``), so contractions stay one token.
4. Every remaining character that is neither a letter, a digit, an underscore nor a
   whitespace is replaced by a space. This removes punctuation, emojis, pictographs,
   symbols and invisible characters (variation selectors, zero-width joiners).
5. Lower-casing.
6. Whitespace (including new lines) is collapsed and trimmed.
"""

import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DATA_DIR = PROJECT_ROOT / "processed_data"

TEXT_COLUMN = "comment_text"
DATASETS = ("train", "test")

PLACEHOLDER_PATTERN = re.compile(r"\[(EMAIL|URL|IP|PHONE|PERSON|GPE|LOC|FAC)\]")
APOSTROPHE_PATTERN = re.compile(r"['’‘`´]")
NON_WORD_PATTERN = re.compile(r"[^\w\s]")
WHITESPACE_PATTERN = re.compile(r"\s+")


def clean_series(texts: pd.Series[str]) -> pd.Series[str]:
    """Apply the cleaning pipeline to a column of comments."""
    cleaned = texts.fillna("").astype(str)
    cleaned = cleaned.map(lambda t: unicodedata.normalize("NFKC", t))
    cleaned = cleaned.str.replace(PLACEHOLDER_PATTERN, r"tag_\1", regex=True)
    cleaned = cleaned.str.replace(APOSTROPHE_PATTERN, "", regex=True)
    cleaned = cleaned.str.replace(NON_WORD_PATTERN, " ", regex=True)
    cleaned = cleaned.str.lower()
    cleaned = cleaned.str.replace(WHITESPACE_PATTERN, " ", regex=True).str.strip()
    return cleaned


def clean_dataset(name: str) -> int:
    """Clean one anonymised dataset and write it. Return the number of empty comments."""
    source = PROCESSED_DATA_DIR / f"{name}_anonymized.csv"
    target = PROCESSED_DATA_DIR / f"{name}_clean.csv"
    logger.info("Reading %s", source)
    df = pd.read_csv(source)
    df[TEXT_COLUMN] = clean_series(df[TEXT_COLUMN])
    empty = int((df[TEXT_COLUMN] == "").sum())
    df.to_csv(target, index=False)
    logger.info("Wrote %s (%d rows, %d empty after cleaning)", target, len(df), empty)
    if empty:
        logger.warning(
            "%d '%s' comments are empty after cleaning (they only contained symbols or emojis)",
            empty,
            name,
        )
    return empty


def main() -> int:
    for name in DATASETS:
        clean_dataset(name)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
