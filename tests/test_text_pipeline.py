"""The serving text pipeline must match the one that produced the training data."""

import pandas as pd
import pytest
import spacy

from tp1_digital_social_score.clean_data import clean_series
from tp1_digital_social_score.text_pipeline import clean_text, prepare_texts

SAMPLES = [
    "Hello!!! You're SO dumb \U0001f602\U0001f44d... see [PERSON] at [URL]?",
    "ＦＵＬＬ width, don't   stop",
    "\U0001f602\U0001f602",
    "line one\nline two\t\ttabbed",
    "It’s a `quoted` ‘word’ with [IP] and [EMAIL], [PHONE] [LOC] [GPE] [FAC]",
    "Numbers 21:51, January 11, 2016 (UTC)",
    "",
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (SAMPLES[0], "hello youre so dumb see tag_person at tag_url"),
        (SAMPLES[1], "full width dont stop"),
        (SAMPLES[2], ""),
        (SAMPLES[3], "line one line two tabbed"),
    ],
)
def test_clean_text_examples(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


@pytest.mark.parametrize("raw", SAMPLES)
def test_clean_text_matches_the_training_cleaning(raw: str) -> None:
    """Serving and training must clean text identically, otherwise the model sees other inputs."""
    assert clean_text(raw) == clean_series(pd.Series([raw])).iloc[0]


def test_prepare_texts_removes_direct_identifiers(nlp: spacy.language.Language) -> None:
    raw = "Mail me at jane.doe@example.com or visit https://example.org from 192.168.0.12"
    (prepared,) = prepare_texts([raw], nlp)
    for leaked in ("jane", "example", "192"):
        assert leaked not in prepared
    for placeholder in ("tag_email", "tag_url", "tag_ip"):
        assert placeholder in prepared


def test_prepare_texts_replaces_names(nlp: spacy.language.Language) -> None:
    raw = "Your vandalism to the Matt Shirvington article has been reverted."
    (prepared,) = prepare_texts([raw], nlp)
    assert "shirvington" not in prepared
    assert "tag_person" in prepared


def test_prepare_texts_keeps_order_and_count(nlp: spacy.language.Language) -> None:
    prepared = prepare_texts(["First text", "Second text", "Third text"], nlp)
    assert prepared == ["first text", "second text", "third text"]
