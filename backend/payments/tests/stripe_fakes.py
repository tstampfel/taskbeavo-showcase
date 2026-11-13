"""Test doubles at the Stripe boundary. Tests never call the Stripe API.

- `load_event` reads a test-mode event from `fixtures/`, shaped like the events
  `stripe listen` forwards from the sandbox.
- `sign` builds a real `Stripe-Signature` header (HMAC-SHA256 over `{t}.{payload}`),
  so the webhook's signature check runs unmodified against a test secret.
- `RecordingHttpClient` replaces the SDK's HTTP transport: it records every request,
  headers included, and answers with a canned JSON object (and status: 400 with an
  `{"error": ...}` body makes the SDK raise, as Stripe's refusals do).
"""

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import stripe

FIXTURES = Path(__file__).parent / "fixtures"


def load_event(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    signed = f"{timestamp}.".encode() + payload
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    params: dict[str, list[str]]


class RecordingHttpClient(stripe.HTTPClient):
    name = "recording"

    def __init__(self, response: dict[str, Any] | None = None, status: int = 200) -> None:
        super().__init__()
        self.response = response or {"id": "obj_test_1", "object": "test"}
        self.status = status
        self.requests: list[RecordedRequest] = []

    def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        post_data: Any = None,
        *,
        _usage: Any = None,
    ) -> tuple[str, int, Mapping[str, str]]:
        body = post_data.decode() if isinstance(post_data, bytes) else post_data or ""
        self.requests.append(
            RecordedRequest(
                method, url, dict(headers or {}), parse_qs(body, keep_blank_values=True)
            )
        )
        return json.dumps(self.response), self.status, {"request-id": "req_test"}

    def close(self) -> None:
        pass
