"""Anonymise the ``comment_text`` column of the Jigsaw dataset.

Two layers are applied, in this order:

1. Regex rules for direct identifiers that NER handles poorly
   (e-mail addresses, URLs, IP addresses, phone numbers).
2. spaCy NER for names and places (PERSON, GPE, LOC, FAC).

Each detected span is replaced by a typed placeholder such as ``[PERSON]``
(pseudonymisation of the *type*, not of the value: the mapping back is not kept,
so the result is anonymised). A processing register (counts per category, the
rules used and their justification) is written next to the outputs.
"""

import argparse
import importlib.metadata
import json
import logging
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import spacy

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
PROCESSED_DATA_DIR = PROJECT_ROOT / "processed_data"
REGISTER_FILE = PROCESSED_DATA_DIR / "processing_register.json"
COMPARISON_FILE = PROCESSED_DATA_DIR / "comparison_sample.csv"

TEXT_COLUMN = "comment_text"
SPACY_MODEL = "en_core_web_sm"
DATASETS = ("train", "test")
COMPARISON_SAMPLE_SIZE = 100
BATCH_SIZE = 256

# Regex rules, applied in order. IPv4 must come before phone numbers.
REGEX_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("URL", re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")),
)

# NER labels that are replaced, with the justification kept in the register.
NER_LABELS_REPLACED: dict[str, str] = {
    "PERSON": "Names identify natural persons directly (RGPD art. 4).",
    "GPE": "Cities, countries and regions can reveal where someone lives.",
    "LOC": "Other locations can reveal where someone lives or works.",
    "FAC": "Buildings and addresses can identify a person indirectly.",
}
NER_LABELS_KEPT: dict[str, str] = {
    "NORP": (
        "Nationalities and religious or political groups are the target of toxic "
        "comments; removing them would destroy the signal the model must learn. "
        "They designate groups, not an identifiable individual."
    ),
    "ORG": "Organisations are not personal data.",
}


def regex_anonymize(text: str, counts: Counter[str]) -> str:
    """Replace direct identifiers matched by regex rules with typed placeholders."""
    for label, pattern in REGEX_RULES:
        text, n = pattern.subn(f"[{label}]", text)
        counts[label] += n
    return text


def anonymize_texts(texts: Iterable[str], nlp: spacy.language.Language,
                    n_process: int) -> tuple[list[str], Counter[str]]:
    """Anonymise texts with regex rules followed by spaCy NER."""
    counts: Counter[str] = Counter()
    pre_cleaned = [regex_anonymize(t, counts) for t in texts]
    results: list[str] = []
    for doc in nlp.pipe(pre_cleaned, batch_size=BATCH_SIZE, n_process=n_process):
        parts: list[str] = []
        cursor = 0
        for ent in doc.ents:
            if ent.label_ not in NER_LABELS_REPLACED:
                continue
            parts.append(doc.text[cursor:ent.start_char])
            parts.append(f"[{ent.label_}]")
            cursor = ent.end_char
            counts[ent.label_] += 1
        parts.append(doc.text[cursor:])
        results.append("".join(parts))
    return results, counts


def process_dataset(name: str, nlp: spacy.language.Language, limit: int | None,
                    n_process: int) -> tuple[pd.DataFrame, pd.DataFrame, Counter[str]]:
    """Load one raw dataset and return (anonymised, original sample, counts)."""
    source = RAW_DATA_DIR / f"{name}.csv"
    if not source.exists():
        source = RAW_DATA_DIR / f"{name}.csv.zip"
    logger.info("Reading %s", source)
    df = pd.read_csv(source)
    if limit is not None:
        df = df.head(limit)
    originals = df[TEXT_COLUMN].fillna("").astype(str).tolist()
    logger.info("Anonymising %d comments of '%s' (n_process=%d)", len(df), name, n_process)
    anonymized, counts = anonymize_texts(originals, nlp, n_process)
    out = df.copy()
    out[TEXT_COLUMN] = anonymized
    sample = pd.DataFrame({
        "dataset": name,
        "id": df["id"].head(COMPARISON_SAMPLE_SIZE),
        "original": originals[:COMPARISON_SAMPLE_SIZE],
        "anonymized": anonymized[:COMPARISON_SAMPLE_SIZE],
    })
    return out, sample, counts


def write_register(row_counts: dict[str, int], entity_counts: dict[str, Counter[str]]) -> None:
    """Write the processing register (counts only, never the texts)."""
    register = {
        "processed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": "Train a toxicity-scoring model without keeping personal data in clear.",
        "legal_basis_note": (
            "Data minimisation (RGPD art. 5.1.c): identifiers removed before training."
        ),
        "source": (
            "Kaggle Jigsaw Toxic Comment Classification Challenge (public Wikipedia comments)"
        ),
        "column_processed": TEXT_COLUMN,
        "spacy_model": SPACY_MODEL,
        "spacy_version": importlib.metadata.version("spacy"),
        "regex_rules": [label for label, _ in REGEX_RULES],
        "ner_labels_replaced": NER_LABELS_REPLACED,
        "ner_labels_kept": NER_LABELS_KEPT,
        "rows_processed": row_counts,
        "replacements": {name: dict(counts) for name, counts in entity_counts.items()},
        "limitations": [
            "NER is statistical: some names (misspelled, lowercase, nicknames) are missed.",
            "Wikipedia usernames written without context are not detected.",
            "Placeholders keep sentence structure, which is enough for toxicity scoring.",
        ],
    }
    REGISTER_FILE.write_text(json.dumps(register, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Register written to %s", REGISTER_FILE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N rows of each dataset (for testing).")
    parser.add_argument("--n-process", type=int, default=min(4, os.cpu_count() or 1),
                        help="Number of spaCy worker processes.")
    args = parser.parse_args(argv)

    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Loading spaCy model %s", SPACY_MODEL)
    nlp = spacy.load(SPACY_MODEL, exclude=["parser", "lemmatizer", "senter", "attribute_ruler"])

    samples: list[pd.DataFrame] = []
    row_counts: dict[str, int] = {}
    entity_counts: dict[str, Counter[str]] = {}
    for name in DATASETS:
        out, sample, counts = process_dataset(name, nlp, args.limit, args.n_process)
        target = PROCESSED_DATA_DIR / f"{name}_anonymized.csv"
        out.to_csv(target, index=False)
        logger.info("Wrote %s (%d rows, replacements: %s)", target, len(out), dict(counts))
        samples.append(sample)
        row_counts[name] = len(out)
        entity_counts[name] = counts

    pd.concat(samples).to_csv(COMPARISON_FILE, index=False)
    logger.info("Before/after sample written to %s", COMPARISON_FILE)
    write_register(row_counts, entity_counts)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
