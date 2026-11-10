"""HTTP behaviour of the scoring API, including the RGPD guarantees (no text in the logs)."""

import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from tp1_digital_social_score.api import (
    DEFAULT_TORCH_THREADS,
    MAX_TEXT_CHARS,
    ScoringService,
    create_app,
)
from tp1_digital_social_score.lstm_model import FloatArray
from tp1_digital_social_score.scoring import LABELS


@pytest.fixture(scope="module")
def client(artifacts_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(artifacts_dir)) as test_client:
        yield test_client


def service_of(client: TestClient) -> ScoringService:
    service: ScoringService = client.app.state.service  # type: ignore[attr-defined]
    return service


def test_torch_threads_are_limited_and_configurable(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def threads_used_by_a_request() -> int:
        """Torch thread count seen by the worker thread that runs the model."""
        seen: list[int] = []
        with TestClient(create_app(artifacts_dir)) as test_client:
            predictor = service_of(test_client).predictor
            original_predict = predictor.predict

            def probe(texts: Sequence[str]) -> FloatArray:
                seen.append(torch.get_num_threads())
                return original_predict(texts)

            monkeypatch.setattr(predictor, "predict", probe)
            assert test_client.post("/score", json={"text": "hello"}).status_code == 200
        return seen[0]

    original = torch.get_num_threads()
    try:
        monkeypatch.delenv("TORCH_NUM_THREADS", raising=False)
        assert threads_used_by_a_request() == DEFAULT_TORCH_THREADS
        monkeypatch.setenv("TORCH_NUM_THREADS", "1")
        assert threads_used_by_a_request() == 1
    finally:
        torch.set_num_threads(original)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_score(client: TestClient) -> None:
    response = client.post("/score", json={"text": "You are an idiot"})
    assert response.status_code == 200
    body = response.json()
    assert 0 <= body["score"] <= 100
    assert set(body["labels"]) == set(LABELS)
    assert all(0 <= value <= 1 for value in body["labels"].values())
    assert body["request_id"]


def test_request_id_is_reused_when_safe_and_replaced_when_not(client: TestClient) -> None:
    safe = client.post("/score", json={"text": "hello"}, headers={"X-Request-ID": "abc-123"})
    assert safe.json()["request_id"] == "abc-123"
    unsafe = client.post(
        "/score", json={"text": "hello"}, headers={"X-Request-ID": "bad id\nforged log line"}
    )
    assert unsafe.json()["request_id"] != "bad id\nforged log line"
    assert len(unsafe.json()["request_id"]) == 32


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "x" * (MAX_TEXT_CHARS + 1)},
        {"text": 123},
    ],
)
def test_score_rejects_invalid_input(client: TestClient, payload: dict[str, object]) -> None:
    assert client.post("/score", json=payload).status_code == 422


def test_the_maximum_allowed_input_is_accepted(client: TestClient) -> None:
    assert client.post("/score", json={"text": "x" * MAX_TEXT_CHARS}).status_code == 200


def test_logs_never_contain_the_text(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    text = "Contact Zebulon Quixote at zebulon@example.com from 192.168.1.1 you idiot"
    with caplog.at_level(logging.DEBUG):
        assert client.post("/score", json={"text": text}).status_code == 200
    assert "request_id=" in caplog.text
    for secret in ("Zebulon", "Quixote", "zebulon@example.com", "192.168.1.1", "idiot"):
        assert secret not in caplog.text


def test_the_model_only_sees_anonymised_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_predict(texts: Sequence[str]) -> FloatArray:
        seen.extend(texts)
        return np.zeros((len(texts), len(LABELS)), dtype=np.float32)

    monkeypatch.setattr(service_of(client).predictor, "predict", fake_predict)
    raw = "Write to jane.doe@example.com or see https://example.org, IP 10.0.0.7"
    assert client.post("/score", json={"text": raw}).status_code == 200
    assert len(seen) == 1
    for leaked in ("jane", "example", "10.0.0.7"):
        assert leaked not in seen[0]
    assert "tag_email" in seen[0]
