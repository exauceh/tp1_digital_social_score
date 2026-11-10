"""BiLSTM toxicity classifier and the code that loads and runs the trained artifacts.

The architecture is the one trained in ``train.ipynb`` (section 3); the layer names must stay
identical, otherwise the saved ``state_dict`` cannot be loaded. Artifacts expected in the model
directory:

- ``model.pt``: ``state_dict`` written by ``torch.save``
- ``vocab.json``: word -> id mapping
- ``metrics.json``: training run; its ``config`` gives ``max_len``, ``embed_dim``, ``hidden_dim``
  and ``dropout``
"""

import json
import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import torch
from numpy.typing import NDArray
from torch import Tensor, nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from tp1_digital_social_score.scoring import LABELS

logger = logging.getLogger(__name__)

FloatArray = NDArray[np.float32]

PAD_ID, UNK_ID = 0, 1


class BiLstmClassifier(nn.Module):
    """Embedding -> BiLSTM -> max + mean pooling over real tokens -> one logit per label."""

    def __init__(
        self, vocab_size: int, embed_dim: int, hidden_dim: int, num_labels: int, dropout: float
    ) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=PAD_ID)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, batch_first=True, bidirectional=True)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(hidden_dim * 4, num_labels)

    def forward(self, ids: Tensor, lengths: Tensor) -> Tensor:
        embedded = self.dropout(self.embedding(ids))
        packed = pack_padded_sequence(
            embedded, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        output, _ = self.lstm(packed)
        output, _ = pad_packed_sequence(output, batch_first=True, total_length=ids.size(1))
        positions = torch.arange(ids.size(1), device=ids.device).unsqueeze(0)
        mask = (positions < lengths.unsqueeze(1)).unsqueeze(-1)
        max_pool = output.masked_fill(~mask, float("-inf")).max(dim=1).values
        mean_pool = (output * mask).sum(dim=1) / lengths.unsqueeze(1)
        logits: Tensor = self.classifier(self.dropout(torch.cat([max_pool, mean_pool], dim=1)))
        return logits


def encode_texts(
    texts: Sequence[str], vocab: dict[str, int], max_len: int
) -> tuple[Tensor, Tensor]:
    """Padded token ids and true lengths (an empty text counts as one unknown token)."""
    ids = torch.zeros((len(texts), max_len), dtype=torch.long)
    lengths = torch.ones(len(texts), dtype=torch.long)
    for row, text in enumerate(texts):
        tokens = [vocab.get(token, UNK_ID) for token in text.split()[:max_len]] or [UNK_ID]
        ids[row, : len(tokens)] = torch.tensor(tokens, dtype=torch.long)
        lengths[row] = len(tokens)
    return ids, lengths


class LstmPredictor:
    """Loads the trained BiLSTM on CPU and returns one probability per label for each text."""

    def __init__(self, model_dir: Path) -> None:
        metrics = json.loads((model_dir / "metrics.json").read_text(encoding="utf-8"))
        config = metrics["config"]
        self.vocab: dict[str, int] = json.loads(
            (model_dir / "vocab.json").read_text(encoding="utf-8")
        )
        self.max_len = int(config["max_len"])
        model = BiLstmClassifier(
            vocab_size=len(self.vocab),
            embed_dim=int(config["embed_dim"]),
            hidden_dim=int(config["hidden_dim"]),
            num_labels=len(LABELS),
            dropout=float(config["dropout"]),
        )
        # weights_only=True: the file can only contain tensors, never executable code.
        state = torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        self.model = model
        logger.info("LSTM loaded from %s (vocabulary: %d words)", model_dir, len(self.vocab))

    def predict(self, texts: Sequence[str]) -> FloatArray:
        """Probabilities of shape (n_texts, 6), in the order of ``LABELS``."""
        ids, lengths = encode_texts(texts, self.vocab, self.max_len)
        with torch.no_grad():
            logits = self.model(ids, lengths)
        return np.asarray(torch.sigmoid(logits).numpy(), dtype=np.float32)
