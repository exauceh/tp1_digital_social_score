"""HTTP behaviour of the scoring API: authentication, validation and the RGPD guarantees."""

import logging
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
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
from tp1_digital_social_score.body_limit import MAX_BODY_BYTES
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


# --- test page (static/) ------------------------------------------------------------------------

UI_ASSETS = {
    "/": "text/html",
    "/ui/app.js": "text/javascript",
    "/ui/style.css": "text/css",
}


@pytest.mark.parametrize(("path", "content_type"), UI_ASSETS.items())
def test_ui_is_served_without_a_key_and_with_a_strict_policy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, content_type: str
) -> None:
    monkeypatch.delitem(client.headers, API_KEY_HEADER)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    policy = response.headers["Content-Security-Policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'"):
        assert directive in policy
    assert "unsafe-inline" not in policy
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/ui/index.html", "/ui/unknown.js", "/ui/..%2Fapi.py"])
def test_ui_serves_only_its_own_files(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_ui_page_runs_no_inline_code(client: TestClient) -> None:
    """The CSP forbids inline code: an inline script or handler would silently not run."""
    page = client.get("/").text
    assert "<script>" not in page
    assert "onclick=" not in page and "onload=" not in page and "style=" not in page


def test_ui_never_injects_html(client: TestClient) -> None:
    script = client.get("/ui/app.js").text
    assert "innerHTML" not in script and "outerHTML" not in script
    assert "insertAdjacentHTML" not in script and "document.write" not in script


def test_ui_shows_the_verdict_only(client: TestClient) -> None:
    """The page must not ask for, or draw, anything the API no longer returns."""
    page = client.get("/").text
    script = client.get("/ui/app.js").text
    assert "<details" not in page and 'id="labels"' not in page and 'id="gauge"' not in page
    for leaked in ("/thresholds", "body.labels", "body.score"):
        assert leaked not in script


@pytest.mark.parametrize("with_key", [True, False])
def test_the_decision_thresholds_are_not_exposed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, with_key: bool
) -> None:
    """Thresholds tell an attacker exactly how far to push a text to slip under the boundary."""
    if not with_key:
        monkeypatch.delitem(client.headers, API_KEY_HEADER)
    assert client.get("/thresholds").status_code == 404


def test_ui_routes_are_not_in_the_api_documentation(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/health", "/score"}
    example = schema["components"]["schemas"]["ScoreRequest"]["examples"][0]
    assert example["text"]


def test_score(client: TestClient) -> None:
    response = client.post("/score", json={"text": "You are an idiot"})
    assert response.status_code == 200
    body = response.json()
    # Only a coarse verdict: no score, no label probabilities, nothing to copy the model from.
    assert set(body) == {"verdict", "request_id"}
    assert body["verdict"] in {"clean", "slightly_toxic", "toxic"}
    assert body["request_id"]


@pytest.mark.parametrize("send_key", [True, False])
def test_every_json_answer_carries_security_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, send_key: bool
) -> None:
    """Also the refusals (401): the headers come from the outermost layer."""
    if not send_key:
        monkeypatch.delitem(client.headers, API_KEY_HEADER)
    response = client.post("/score", json={"text": "hello"})
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "max-age" in response.headers["Strict-Transport-Security"]
    policy = response.headers["Content-Security-Policy"]
    assert policy == "default-src 'none'; frame-ancestors 'none'"


def test_the_test_page_keeps_its_own_content_security_policy(client: TestClient) -> None:
    policy = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy


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


# --- Request body size (denial of service) --------------------------------------------------------


def test_a_body_declared_too_large_by_content_length_is_rejected(client: TestClient) -> None:
    """Rejected from the declared Content-Length alone: the oversized body is never read."""
    response = client.post(
        "/score",
        content=b"{}",  # irrelevant: the Content-Length header below is what gets checked
        headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_a_body_actually_larger_than_the_limit_is_rejected(client: TestClient) -> None:
    """A lying or absent Content-Length does not help: the bytes received are counted too."""
    oversized = b'{"text": "' + b"a" * MAX_BODY_BYTES + b'"}'
    # Content-Length below: understated on purpose, the byte count must not be trusted blindly.
    response = client.post(
        "/score",
        content=oversized,
        headers={"Content-Length": "2", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_a_body_right_at_the_limit_still_reaches_the_application(client: TestClient) -> None:
    """The limit does not shrink what a legitimate maximum-length request is allowed to send."""
    # MAX_TEXT_CHARS (5000) stays far below MAX_BODY_BYTES (body_limit.py): still 422, not 413.
    response = client.post("/score", json={"text": "x" * (MAX_TEXT_CHARS + 1)})
    assert response.status_code == 422


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


# --- Prometheus metrics (course n°3) -----------------------------------------------------------


def series_value(exposition: str, series: str) -> float | None:
    """Value of one series of the Prometheus text format, or None when it is absent."""
    for line in exposition.splitlines():
        if line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    return None


def test_metrics_are_exposed_without_a_key(client: TestClient) -> None:
    response = client.get("/metrics", headers={API_KEY_HEADER: ""})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE api_requests_total counter" in response.text
    assert "# TYPE api_latency_seconds histogram" in response.text


def test_metrics_has_no_token_check_when_metrics_token_is_unset(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    with make_client(artifacts_dir) as fresh:
        assert fresh.get("/metrics").status_code == 200


def test_metrics_requires_the_token_once_one_is_configured(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METRICS_TOKEN", "the-metrics-token")
    with make_client(artifacts_dir) as fresh:
        no_header = fresh.get("/metrics")
        wrong_token = fresh.get("/metrics", headers={"Authorization": "Bearer wrong"})
        not_bearer = fresh.get("/metrics", headers={"Authorization": "the-metrics-token"})
        right_token = fresh.get("/metrics", headers={"Authorization": "Bearer the-metrics-token"})
    assert no_header.status_code == 401
    assert wrong_token.status_code == 401
    assert not_bearer.status_code == 401
    assert right_token.status_code == 200


def test_metrics_token_does_not_unlock_score(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two secrets are independent: the metrics token is not a client API key."""
    monkeypatch.setenv("METRICS_TOKEN", API_KEY)  # even if it happens to equal a real API key
    with make_client(artifacts_dir) as fresh:
        response = fresh.post(
            "/score",
            json={"text": "hello"},
            headers={API_KEY_HEADER: "", "Authorization": f"Bearer {API_KEY}"},
        )
    assert response.status_code == 401  # /score only looks at X-API-Key, never Authorization


def test_expected_series_exist_at_zero_before_any_request(artifacts_dir: Path) -> None:
    """Prometheus ignores the first sample of a new series, so these must exist from the start."""
    with make_client(artifacts_dir) as fresh:
        exposition = fresh.get("/metrics").text
    for status in ("200", "401", "422", "500"):
        series = f'api_requests_total{{method="POST",route="/score",status="{status}"}}'
        assert series_value(exposition, series) == 0
    assert series_value(exposition, 'api_latency_seconds_count{route="/score"}') == 0


def test_requests_are_counted_by_route_and_status(artifacts_dir: Path) -> None:
    with make_client(artifacts_dir) as fresh:
        for _ in range(3):
            assert fresh.post("/score", json={"text": "hello"}).status_code == 200
        assert fresh.post("/score", json={"text": ""}).status_code == 422
        refused = fresh.post("/score", json={"text": "hi"}, headers={API_KEY_HEADER: "wrong"})
        assert refused.status_code == 401
        exposition = fresh.get("/metrics").text

    ok = 'api_requests_total{method="POST",route="/score",status="200"}'
    assert series_value(exposition, ok) == 3
    assert series_value(exposition, ok.replace("200", "422")) == 1
    assert series_value(exposition, ok.replace("200", "401")) == 1
    assert series_value(exposition, "api_auth_failures_total") == 1
    # The latency histogram sees all five requests; inference and score only the 3 scored ones.
    assert series_value(exposition, 'api_latency_seconds_count{route="/score"}') == 5
    assert series_value(exposition, "inference_latency_seconds_count") == 3
    assert series_value(exposition, "toxicity_score_count") == 3
    assert series_value(exposition, "api_requests_in_progress") == 0


def test_probes_and_scraping_are_not_counted(artifacts_dir: Path) -> None:
    with make_client(artifacts_dir) as fresh:
        for _ in range(5):
            fresh.get("/health")
            fresh.get("/metrics")
        exposition = fresh.get("/metrics").text
    assert 'route="/health"' not in exposition
    assert 'route="/metrics"' not in exposition


def test_unknown_paths_share_one_label_value(artifacts_dir: Path) -> None:
    """A scanner trying thousands of paths must not create thousands of series."""
    with make_client(artifacts_dir) as fresh:
        for path in ("/a", "/b/c", "/admin", "/.env"):
            assert fresh.get(path).status_code == 404
        exposition = fresh.get("/metrics").text
    unmatched = 'api_requests_total{method="GET",route="unmatched",status="404"}'
    assert series_value(exposition, unmatched) == 4
    for path in ("/a", "/b/c", "/admin", "/.env"):
        assert path not in exposition


def test_metrics_never_contain_personal_data(artifacts_dir: Path) -> None:
    text = "Contact Zebulon Quixote at zebulon@example.com from 192.168.1.1 you idiot"
    with make_client(artifacts_dir) as fresh:
        assert fresh.post("/score", json={"text": text}).status_code == 200
        exposition = fresh.get("/metrics").text
    for secret in ("Zebulon", "Quixote", "zebulon@example.com", "192.168.1.1", "idiot"):
        assert secret not in exposition
    for identifier in (CLIENT_ID, OTHER_CLIENT_ID, API_KEY, OTHER_KEY):
        assert identifier not in exposition


def test_each_application_has_its_own_registry(artifacts_dir: Path) -> None:
    with make_client(artifacts_dir) as first, make_client(artifacts_dir) as second:
        assert first.post("/score", json={"text": "hello"}).status_code == 200
        counted = 'api_requests_total{method="POST",route="/score",status="200"}'
        assert series_value(first.get("/metrics").text, counted) == 1
        assert series_value(second.get("/metrics").text, counted) == 0


# --- probes must stay responsive under load (found by the load test) -----------------------------


def test_health_and_metrics_answer_while_every_worker_thread_is_busy(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every thread of the pool waits for the model, the probes must not wait with them.

    The Kubernetes probes call /health with a 2 s timeout. When /health ran in the same thread
    pool as /score, a saturated pod failed its probes, left the Service and was then killed by the
    liveness probe, which removed even more capacity at the worst moment (seen during the ramp and
    spike load tests: pods restarted, no pod ready).
    """
    release = threading.Event()
    in_flight = 60  # more than the 40 threads of the pool
    # The load-shedding cap (30) is below the pool size, so it would refuse these requests before
    # they could saturate the pool: lift it to test the pool itself.
    monkeypatch.setattr("tp1_digital_social_score.api.MAX_IN_FLIGHT_SCORES", 1000)
    with make_client(artifacts_dir) as busy:
        predictor = service_of(busy).predictor
        original_predict = predictor.predict

        def blocked_predict(texts: Sequence[str]) -> FloatArray:
            release.wait(timeout=30)
            return original_predict(texts)

        monkeypatch.setattr(predictor, "predict", blocked_predict)
        registry = busy.app.state.metrics.registry  # type: ignore[attr-defined]
        with ThreadPoolExecutor(max_workers=in_flight + 2) as pool:
            scoring = [
                pool.submit(busy.post, "/score", json={"text": "hello"}) for _ in range(in_flight)
            ]
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if (registry.get_sample_value("api_requests_in_progress") or 0) >= in_flight:
                    break
                time.sleep(0.05)
            try:
                health = pool.submit(busy.get, "/health").result(timeout=3)
                scraped = pool.submit(busy.get, "/metrics").result(timeout=3)
            finally:
                release.set()
            assert all(future.result(timeout=60).status_code == 200 for future in scoring)
    assert health.status_code == 200
    assert scraped.status_code == 200


def test_score_sheds_load_with_503_while_probes_keep_answering(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the in-flight cap /score answers 503 at once; /health is never refused."""
    release = threading.Event()
    cap, sent = 2, 6
    monkeypatch.setattr("tp1_digital_social_score.api.MAX_IN_FLIGHT_SCORES", cap)
    with make_client(artifacts_dir) as busy:
        predictor = service_of(busy).predictor
        original_predict = predictor.predict

        def blocked_predict(texts: Sequence[str]) -> FloatArray:
            release.wait(timeout=30)
            return original_predict(texts)

        monkeypatch.setattr(predictor, "predict", blocked_predict)
        with ThreadPoolExecutor(max_workers=sent + 2) as pool:
            scoring = [
                pool.submit(busy.post, "/score", json={"text": "hello"}) for _ in range(sent)
            ]
            time.sleep(1.0)
            try:
                health = pool.submit(busy.get, "/health").result(timeout=3)
            finally:
                release.set()
            codes = sorted(future.result(timeout=60).status_code for future in scoring)
        shed = busy.app.state.metrics.registry.get_sample_value("api_requests_shed_total")  # type: ignore[attr-defined]
    assert health.status_code == 200
    assert codes == [200] * cap + [503] * (sent - cap)
    assert shed == sent - cap
