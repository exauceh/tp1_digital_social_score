"""HTTP behaviour of the scoring API: authentication, validation and the RGPD guarantees."""

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
from tp1_digital_social_score.security import API_KEY_HEADER, ApiKeyStore, make_entry

# Test-only values, not secrets.
CLIENT_ID = "tester"
API_KEY = "test-api-key-not-a-secret-0123456789abcdef"
OTHER_CLIENT_ID = "other"
OTHER_KEY = "another-test-key-not-a-secret-9876543210"


def make_store() -> ApiKeyStore:
    entries = f"{make_entry(CLIENT_ID, API_KEY)},{make_entry(OTHER_CLIENT_ID, OTHER_KEY)}"
    return ApiKeyStore.parse(entries)


def make_client(artifacts_dir: Path) -> TestClient:
    """A test client that sends the API key by default."""
    app = create_app(artifacts_dir, api_keys=make_store())
    return TestClient(app, headers={API_KEY_HEADER: API_KEY})


@pytest.fixture(scope="module")
def client(artifacts_dir: Path) -> Iterator[TestClient]:
    with make_client(artifacts_dir) as test_client:
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
        with make_client(artifacts_dir) as test_client:
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


def test_health_needs_no_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(client.headers, API_KEY_HEADER)
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


# --- authentication -----------------------------------------------------------------------------


def test_score_without_a_key_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(client.headers, API_KEY_HEADER)
    response = client.post("/score", json={"text": "hello"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert response.json() == {"detail": "Invalid or missing API key"}


@pytest.mark.parametrize("key", ["", "wrong-key", API_KEY + "x", API_KEY.upper(), API_KEY[:-1]])
def test_score_with_a_wrong_key_is_refused(client: TestClient, key: str) -> None:
    response = client.post("/score", json={"text": "hello"}, headers={API_KEY_HEADER: key})
    assert response.status_code == 401


def test_a_refused_caller_learns_nothing_about_the_input(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a valid key even an invalid body gets 401, never a 422 describing the format."""
    monkeypatch.delitem(client.headers, API_KEY_HEADER)
    assert client.post("/score", json={"text": ""}).status_code == 401
    assert client.post("/score", json={}).status_code == 401


def test_each_client_has_its_own_key(client: TestClient) -> None:
    response = client.post(
        "/score", json={"text": "hello"}, headers={API_KEY_HEADER: OTHER_KEY}
    )
    assert response.status_code == 200


def test_the_api_refuses_to_start_without_any_key(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(create_app(artifacts_dir)):
        pass


def test_keys_are_read_from_the_environment(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEYS", make_entry("from-env", "key-from-the-environment"))
    with TestClient(create_app(artifacts_dir)) as env_client:
        good = env_client.post(
            "/score", json={"text": "hello"}, headers={API_KEY_HEADER: "key-from-the-environment"}
        )
        assert good.status_code == 200
        bad = env_client.post("/score", json={"text": "hello"}, headers={API_KEY_HEADER: API_KEY})
        assert bad.status_code == 401


# --- request ids, validation ---------------------------------------------------------------------


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


# --- RGPD ----------------------------------------------------------------------------------------


def test_logs_never_contain_the_text(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    text = "Contact Zebulon Quixote at zebulon@example.com from 192.168.1.1 you idiot"
    with caplog.at_level(logging.DEBUG):
        assert client.post("/score", json={"text": text}).status_code == 200
    assert "request_id=" in caplog.text
    for secret in ("Zebulon", "Quixote", "zebulon@example.com", "192.168.1.1", "idiot"):
        assert secret not in caplog.text


def test_logs_identify_the_client_but_never_show_a_key(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    rejected_key = "rejected-key-that-must-not-be-logged"
    with caplog.at_level(logging.DEBUG):
        assert client.post("/score", json={"text": "hello"}).status_code == 200
        refused = client.post(
            "/score", json={"text": "hello"}, headers={API_KEY_HEADER: rejected_key}
        )
        assert refused.status_code == 401
    assert f"client={CLIENT_ID}" in caplog.text
    assert "authentication failed" in caplog.text
    for secret in (API_KEY, OTHER_KEY, rejected_key):
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
