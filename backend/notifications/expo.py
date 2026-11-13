"""A small client for Expo's push API: send messages, fetch receipts.

https://docs.expo.dev/push-notifications/sending-notifications/
Sending returns one ticket per message, in order. A ticket `ok` only means Expo took the
message; its receipt, fetched later, says whether APNs or FCM accepted it. Either can
carry `DeviceNotRegistered`, which means the token is dead and must be deleted.

With `EXPO_ACCESS_TOKEN` set (Expo's "enhanced security for push"), every request is
authenticated; without it, anyone holding a token could push to it. Every call is bounded
by `EXPO_TIMEOUT_SECONDS`. Errors carry Expo's error codes only, never tokens.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings

SEND_URL = "https://exp.host/--/api/v2/push/send"
RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
# Expo's limits per request.
SEND_BATCH = 100
RECEIPTS_BATCH = 1000

DEVICE_NOT_REGISTERED = "DeviceNotRegistered"


class ExpoError(Exception):
    """Expo refused a whole request. The message is Expo's error code."""


@dataclass(frozen=True)
class PushMessage:
    to: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "to": self.to,
            "title": self.title,
            "body": self.body,
            "data": self.data,
            "sound": "default",
            "priority": "high",
        }


@dataclass(frozen=True)
class Result:
    """A ticket or a receipt: `ok`, or `error` with Expo's code in `error`."""

    ok: bool
    # The ticket's ID, for fetching its receipt. Receipts have none.
    id: str | None = None
    error: str | None = None


def send(messages: Sequence[PushMessage]) -> list[Result]:
    """Send messages; returns their tickets in the same order."""
    tickets: list[Result] = []
    for batch in _chunks(messages, SEND_BATCH):
        data = _post(SEND_URL, [message.as_json() for message in batch])
        if not isinstance(data, list) or len(data) != len(batch):
            raise ExpoError("UnexpectedResponse")
        tickets.extend(_result(item) for item in data)
    return tickets


def receipts(ticket_ids: Sequence[str]) -> dict[str, Result]:
    """Receipts by ticket ID. A receipt Expo doesn't have yet is simply missing."""
    found: dict[str, Result] = {}
    for batch in _chunks(ticket_ids, RECEIPTS_BATCH):
        data = _post(RECEIPTS_URL, {"ids": list(batch)})
        if not isinstance(data, dict):
            raise ExpoError("UnexpectedResponse")
        found.update({str(key): _result(value) for key, value in data.items()})
    return found


def _post(url: str, payload: Any) -> Any:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {settings.EXPO_ACCESS_TOKEN}"
    response = requests.post(
        url, json=payload, headers=headers, timeout=settings.EXPO_TIMEOUT_SECONDS
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        raise ExpoError(str(errors[0].get("code", "UnknownError"))[:100])
    response.raise_for_status()
    if not isinstance(body, dict) or "data" not in body:
        raise ExpoError("UnexpectedResponse")
    return body["data"]


def _result(item: Any) -> Result:
    if not isinstance(item, dict):
        return Result(ok=False, error="UnexpectedResponse")
    if item.get("status") == "ok":
        ticket_id = item.get("id")
        return Result(ok=True, id=str(ticket_id) if ticket_id else None)
    details = item.get("details") or {}
    error = details.get("error") if isinstance(details, dict) else None
    return Result(ok=False, error=str(error or "UnknownError")[:100])


def _chunks[T](items: Iterable[T], size: int) -> Iterator[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
