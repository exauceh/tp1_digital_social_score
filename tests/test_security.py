"""API key storage, verification and generation."""

import hmac
import logging
import re

import pytest
from google.cloud import secretmanager

from tp1_digital_social_score import security
from tp1_digital_social_score.security import (
    ApiKeyStore,
    generate_key,
    hash_key,
    load_api_keys,
    main,
    make_entry,
)

ALICE_KEY = "alice-test-key-not-a-secret"
BOB_KEY = "bob-test-key-not-a-secret"


def two_clients() -> ApiKeyStore:
    return ApiKeyStore.parse(f"{make_entry('alice', ALICE_KEY)},{make_entry('bob', BOB_KEY)}")


def test_generated_keys_are_long_random_and_url_safe() -> None:
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50
    for key in keys:
        assert len(key) >= 43  # 32 random bytes
        assert re.fullmatch(r"[A-Za-z0-9_-]+", key)


def test_hash_key_is_sha256() -> None:
    assert hash_key("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_an_entry_never_contains_the_key() -> None:
    entry = make_entry("alice", ALICE_KEY)
    assert entry == f"alice:{hash_key(ALICE_KEY)}"
    assert ALICE_KEY not in entry


@pytest.mark.parametrize("client_id", ["", "has space", "semi;colon", "x" * 33, "é"])
def test_invalid_client_ids_are_rejected(client_id: str) -> None:
    with pytest.raises(ValueError, match="client id"):
        make_entry(client_id, ALICE_KEY)


def test_parse_accepts_several_separators() -> None:
    alice, bob = make_entry("alice", ALICE_KEY), make_entry("bob", BOB_KEY)
    for raw in (f"{alice},{bob}", f"{alice}\n{bob}\n", f"  {alice} , {bob}  "):
        assert set(ApiKeyStore.parse(raw).hashes) == {"alice", "bob"}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "alice",
        "alice:",
        "alice:not-a-hash",
        f"alice:{'A' * 64}",  # hexadecimal must be lowercase
        f"alice:{'a' * 63}",
        f"bad id:{'a' * 64}",
        f"alice:{'a' * 64},alice:{'b' * 64}",  # same client twice
    ],
)
def test_malformed_key_lists_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        ApiKeyStore.parse(raw)


def test_authenticate_returns_the_owner_of_the_key() -> None:
    store = two_clients()
    assert store.authenticate(ALICE_KEY) == "alice"
    assert store.authenticate(BOB_KEY) == "bob"


@pytest.mark.parametrize(
    "key", ["", "unknown", ALICE_KEY + " ", ALICE_KEY.upper(), hash_key(ALICE_KEY)]
)
def test_authenticate_refuses_unknown_keys(key: str) -> None:
    """Even the stored hash is not a valid key: knowing the Secret does not give access."""
    assert two_clients().authenticate(key) is None


def test_every_stored_hash_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison must not stop at the first match, or the timing would reveal the position."""
    calls: list[int] = []
    real_compare = hmac.compare_digest

    def counting_compare(left: str, right: str) -> bool:
        calls.append(1)
        return real_compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", counting_compare)
    store = two_clients()
    assert store.authenticate(ALICE_KEY) == "alice"  # the first entry matches...
    assert len(calls) == 2  # ...yet both hashes were compared
    calls.clear()
    assert store.authenticate("unknown") is None
    assert len(calls) == 2


def test_load_api_keys_fails_closed_when_the_variable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="API_KEYS"):
        load_api_keys()
    monkeypatch.setenv("API_KEYS", "   ")
    with pytest.raises(RuntimeError, match="API_KEYS"):
        load_api_keys()


def test_load_api_keys_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", make_entry("alice", ALICE_KEY))
    assert load_api_keys().authenticate(ALICE_KEY) == "alice"


def test_load_api_keys_rejects_a_malformed_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", "alice:not-a-hash")
    with pytest.raises(ValueError):
        load_api_keys()


# --- Google Secret Manager ----------------------------------------------------------------------

SECRET_NAME = "projects/my-project/secrets/dss-api-keys/versions/latest"


def fake_secret_manager(
    monkeypatch: pytest.MonkeyPatch, content: str, requested: list[str] | None = None
) -> None:
    """Replace the network read by a function returning ``content``."""

    def fetch(version_name: str) -> str:
        if requested is not None:
            requested.append(version_name)
        return content

    monkeypatch.setattr(security, "fetch_secret", fetch)


def test_keys_are_read_from_secret_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []
    fake_secret_manager(monkeypatch, make_entry("alice", ALICE_KEY) + "\r\n", requested)
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("API_KEYS_SECRET", SECRET_NAME)
    assert load_api_keys().authenticate(ALICE_KEY) == "alice"
    assert requested == [SECRET_NAME]


def test_secret_manager_wins_over_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_secret_manager(monkeypatch, make_entry("alice", ALICE_KEY))
    monkeypatch.setenv("API_KEYS", make_entry("bob", BOB_KEY))
    monkeypatch.setenv("API_KEYS_SECRET", SECRET_NAME)
    store = load_api_keys()
    assert store.authenticate(ALICE_KEY) == "alice"
    assert store.authenticate(BOB_KEY) is None


@pytest.mark.parametrize(
    "name",
    [
        "dss-api-keys",
        "projects/p/secrets/s",
        "projects/p/secrets/s/versions/",
        "projects/p/secrets/s/versions/newest",
        "projects/p/secrets/bad name/versions/latest",
        "other/p/secrets/s/versions/latest",
    ],
)
def test_a_malformed_secret_name_is_refused_without_calling_google(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    requested: list[str] = []
    fake_secret_manager(monkeypatch, make_entry("alice", ALICE_KEY), requested)
    monkeypatch.setenv("API_KEYS_SECRET", name)
    with pytest.raises(RuntimeError, match="API_KEYS_SECRET"):
        load_api_keys()
    assert requested == []


def test_the_api_does_not_fall_back_to_the_variable_when_secret_manager_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A denied or unreachable secret must stop the start-up, not silently use other keys."""

    def denied(version_name: str) -> str:
        raise PermissionError("403 caller lacks secretmanager.versions.access")

    monkeypatch.setattr(security, "fetch_secret", denied)
    monkeypatch.setenv("API_KEYS", make_entry("bob", BOB_KEY))
    monkeypatch.setenv("API_KEYS_SECRET", SECRET_NAME)
    with pytest.raises(RuntimeError, match=SECRET_NAME) as error:
        load_api_keys()
    assert "PermissionError" in str(error.value)
    assert isinstance(error.value.__cause__, PermissionError)


def test_malformed_secret_content_stops_the_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_secret_manager(monkeypatch, "alice:not-a-hash")
    monkeypatch.setenv("API_KEYS_SECRET", SECRET_NAME)
    with pytest.raises(ValueError):
        load_api_keys()


def test_the_secret_content_is_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    content = make_entry("alice", ALICE_KEY)
    fake_secret_manager(monkeypatch, content)
    monkeypatch.setenv("API_KEYS_SECRET", SECRET_NAME)
    with caplog.at_level(logging.DEBUG):
        load_api_keys()
    assert SECRET_NAME in caplog.text
    assert "alice" in caplog.text
    assert content not in caplog.text
    assert hash_key(ALICE_KEY) not in caplog.text


def test_fetch_secret_asks_google_for_the_version_and_decodes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real client is replaced by a stub: no network, no credentials."""
    calls: list[dict[str, str]] = []

    class StubPayload:
        data = "alice:abc é".encode()

    class StubResponse:
        payload = StubPayload()

    class StubClient:
        def access_secret_version(self, request: dict[str, str]) -> StubResponse:
            calls.append(request)
            return StubResponse()

    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", StubClient)
    assert security.fetch_secret(SECRET_NAME) == "alice:abc é"
    assert calls == [{"name": SECRET_NAME}]


def test_command_line_generates_a_key_and_its_entry(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", "alice"]) == 0
    captured = capsys.readouterr()
    key, entry = captured.out.splitlines()
    assert entry == make_entry("alice", key)
    assert ApiKeyStore.parse(entry).authenticate(key) == "alice"
    assert key not in captured.err  # the human instructions never repeat the key


def test_command_line_refuses_an_invalid_client_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", "bad id"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "client id" in captured.err
