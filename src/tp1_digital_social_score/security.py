"""API key authentication for the scoring API.

Each client of the API gets its own key, sent in the ``X-API-Key`` header. The server never stores
the keys themselves, only their SHA-256 hash: a key is a random 256-bit value, so a fast hash is
enough (slow password hashes are only needed for low-entropy secrets). A client is identified by a
short ``client id``, which is the only identifier written to the logs (a pseudonym, never the key
or the caller's IP address).

The entries ``client_id:sha256hex``, separated by commas, come from one of two places:

- ``API_KEYS_SECRET``: the name of a Google Secret Manager secret version
  (``projects/<project>/secrets/<name>/versions/<number or latest>``). This is the production
  setup: the pod authenticates with Workload Identity and may read that single secret.
- ``API_KEYS``: the entries themselves, for local development and tests.

``API_KEYS_SECRET`` wins when both are set. The API refuses to start when no key can be loaded
(it fails closed instead of running unprotected).

Generate a key and its entry with::

    uv run python -I -m tp1_digital_social_score.security generate <client_id>
"""

import argparse
import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
API_KEYS_ENV_VAR = "API_KEYS"
API_KEYS_SECRET_ENV_VAR = "API_KEYS_SECRET"
# Separate from the client API keys: /metrics is machine-to-machine (scraped by Prometheus), not a
# client of the scoring service, so it must not share a key with any client nor unlock /score.
METRICS_TOKEN_ENV_VAR = "METRICS_TOKEN"
METRICS_TOKEN_HEADER_PREFIX = "Bearer "
SECRET_VERSION_PATTERN = re.compile(
    r"^projects/[^/\s]+/secrets/[A-Za-z0-9_-]+/versions/(?:latest|\d+)$"
)
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def generate_key() -> str:
    """A new random API key (256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """SHA-256 of an API key, as lowercase hexadecimal."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def make_entry(client_id: str, key: str) -> str:
    """The ``client_id:sha256hex`` entry to put in ``API_KEYS`` for a key."""
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ValueError("A client id has 1 to 32 characters: letters, digits, '-' or '_'")
    return f"{client_id}:{hash_key(key)}"


@dataclass(frozen=True)
class ApiKeyStore:
    """The known clients and the hash of their API key."""

    hashes: dict[str, str]

    @classmethod
    def parse(cls, raw: str) -> ApiKeyStore:
        """Parse ``client_id:sha256hex`` entries separated by commas, spaces or new lines."""
        hashes: dict[str, str] = {}
        for entry in re.split(r"[,\s]+", raw.strip()):
            if not entry:
                continue
            client_id, separator, digest = entry.partition(":")
            if not separator or not CLIENT_ID_PATTERN.match(client_id):
                raise ValueError("Invalid API key entry: expected 'client_id:sha256hex'")
            if not SHA256_HEX_PATTERN.match(digest):
                raise ValueError(f"Invalid hash for client '{client_id}': expected 64 hex digits")
            if client_id in hashes:
                raise ValueError(f"Client '{client_id}' appears twice in the API keys")
            hashes[client_id] = digest
        if not hashes:
            raise ValueError("No API key configured")
        return cls(hashes)

    def authenticate(self, presented_key: str) -> str | None:
        """Return the client id owning this key, or None when the key is unknown.

        Every stored hash is compared, with a constant-time comparison, so the time taken does not
        reveal which client matched, or whether one did.
        """
        digest = hash_key(presented_key)
        matched: str | None = None
        for client_id, expected in self.hashes.items():
            if hmac.compare_digest(digest, expected):
                matched = client_id
        return matched


def load_metrics_token() -> str:
    """The token required on ``/metrics`` (``Authorization: Bearer <token>``), or ``""``.

    Unlike the API keys, an unset token leaves ``/metrics`` open: it has no client-facing feature
    to fail closed on, and that keeps local development and the tests working without any extra
    setup. Set ``METRICS_TOKEN`` to protect it once the API is reachable from outside the cluster.
    """
    return os.environ.get(METRICS_TOKEN_ENV_VAR, "").strip()


def metrics_token_matches(expected: str, authorization_header: str | None) -> bool:
    """Whether ``authorization_header`` carries ``expected`` as a ``Bearer`` token.

    Constant-time comparison. ``expected`` must be non-empty (checked by the caller): comparing
    against an empty token would make every empty/missing header match.
    """
    presented = authorization_header or ""
    token = (
        presented[len(METRICS_TOKEN_HEADER_PREFIX) :]
        if presented.startswith(METRICS_TOKEN_HEADER_PREFIX)
        else ""
    )
    return hmac.compare_digest(expected, token)


def fetch_secret(version_name: str) -> str:
    """Read one secret version from Google Secret Manager and return its text.

    No key file is involved: on GKE the pod authenticates through Workload Identity and the Google
    client library finds the credentials by itself. That identity only needs
    ``roles/secretmanager.secretAccessor`` on this one secret.
    """
    from google.cloud import secretmanager  # only needed in production, not for local development

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": version_name})
    payload: bytes = response.payload.data
    return payload.decode("utf-8")


def load_api_keys() -> ApiKeyStore:
    """Load the keys from Secret Manager (``API_KEYS_SECRET``) or from ``API_KEYS``.

    Raises when no key can be loaded, so that the API never starts unprotected.
    """
    secret_name = os.environ.get(API_KEYS_SECRET_ENV_VAR, "").strip()
    if secret_name:
        if not SECRET_VERSION_PATTERN.match(secret_name):
            raise RuntimeError(
                f"{API_KEYS_SECRET_ENV_VAR} must look like "
                "projects/<project>/secrets/<name>/versions/<number or latest>"
            )
        try:
            raw = fetch_secret(secret_name)
        except Exception as error:
            # Permission denied, secret not found, no credentials...: the cause is kept in the
            # exception chain, the message only names the secret (never its content).
            raise RuntimeError(
                f"Could not read the API keys from Secret Manager ({secret_name}): "
                f"{type(error).__name__}"
            ) from error
        source = f"Secret Manager ({secret_name})"
    else:
        raw = os.environ.get(API_KEYS_ENV_VAR, "")
        if not raw.strip():
            raise RuntimeError(
                f"Neither {API_KEYS_SECRET_ENV_VAR} nor {API_KEYS_ENV_VAR} is set: the API refuses "
                "to start without any API key. Generate one with "
                "`python -m tp1_digital_social_score.security generate <client_id>`."
            )
        source = f"the {API_KEYS_ENV_VAR} variable"
    store = ApiKeyStore.parse(raw)
    logger.info(
        "API keys loaded for %d client(s) from %s: %s",
        len(store.hashes),
        source,
        ", ".join(store.hashes),
    )
    return store


def main(argv: list[str] | None = None) -> int:
    """Command line: ``generate <client_id>`` prints a new key and its ``API_KEYS`` entry."""
    parser = argparse.ArgumentParser(description="Manage API keys of the scoring API.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    generate = subcommands.add_parser("generate", help="create a key for a client")
    generate.add_argument("client_id", help="short name of the client, e.g. 'grafana' or 'alice'")
    args = parser.parse_args(argv)

    key = generate_key()
    try:
        entry = make_entry(args.client_id, key)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    # stdout: the two values, one per line, so that a script can capture them.
    print(key)
    print(entry)
    print(
        "Line 1 is the API key: give it to the client and keep it nowhere else, it cannot be "
        "shown again.\nLine 2 is the entry to add to API_KEYS (several entries are separated "
        "by commas).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
