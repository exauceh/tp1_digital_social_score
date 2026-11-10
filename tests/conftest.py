"""Shared fixtures: tiny random model artifacts, so the tests never depend on the trained files."""

import json
from pathlib import Path

import pytest
import spacy
import torch

from tp1_digital_social_score.lstm_model import BiLstmClassifier
from tp1_digital_social_score.scoring import LABELS
from tp1_digital_social_score.text_pipeline import load_nlp

TINY_VOCAB = {
    "<pad>": 0,
    "<unk>": 1,
    "you": 2,
    "are": 3,
    "an": 4,
    "idiot": 5,
    "thanks": 6,
    "tag_person": 7,
}
MAX_LEN = 20
EMBED_DIM = 8
HIDDEN_DIM = 8
DROPOUT = 0.0


@pytest.fixture(scope="session")
def artifacts_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A model directory with the same files as ``models/lstm``, but a tiny random network."""
    directory = tmp_path_factory.mktemp("lstm")
    torch.manual_seed(0)
    model = BiLstmClassifier(len(TINY_VOCAB), EMBED_DIM, HIDDEN_DIM, len(LABELS), DROPOUT)
    torch.save(model.state_dict(), directory / "model.pt")
    (directory / "vocab.json").write_text(json.dumps(TINY_VOCAB), encoding="utf-8")
    config = {
        "max_len": MAX_LEN,
        "embed_dim": EMBED_DIM,
        "hidden_dim": HIDDEN_DIM,
        "dropout": DROPOUT,
    }
    (directory / "metrics.json").write_text(json.dumps({"config": config}), encoding="utf-8")
    return directory


@pytest.fixture(scope="session")
def nlp() -> spacy.language.Language:
    """The spaCy pipeline used for anonymisation (loaded once for the whole test session)."""
    return load_nlp()
