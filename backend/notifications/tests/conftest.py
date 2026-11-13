"""A fake Expo push service, and users with registered devices."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from django.contrib.auth.models import AbstractBaseUser

from accounts.tests.factories import sign_in
from notifications import expo
from notifications.models import PushToken


@dataclass
class FakeExpo:
    """Records what would be sent; answers with `tickets_for` and `receipts_by_id`."""

    sent: list[expo.PushMessage] = field(default_factory=list)
    # Token → the ticket Expo returns for it. Unlisted tokens get an `ok` ticket.
    tickets_for: dict[str, expo.Result] = field(default_factory=dict)
    receipts_by_id: dict[str, expo.Result] = field(default_factory=dict)
    receipt_requests: list[list[str]] = field(default_factory=list)
    fail_with: Exception | None = None

    def send(self, messages: list[expo.PushMessage]) -> list[expo.Result]:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.extend(messages)
        return [
            self.tickets_for.get(m.to, expo.Result(ok=True, id=f"ticket-{len(self.sent)}-{i}"))
            for i, m in enumerate(messages)
        ]

    def receipts(self, ticket_ids: list[str]) -> dict[str, expo.Result]:
        self.receipt_requests.append(list(ticket_ids))
        return {i: self.receipts_by_id[i] for i in ticket_ids if i in self.receipts_by_id}


@pytest.fixture
def fake_expo(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeExpo]:
    fake = FakeExpo()
    monkeypatch.setattr(expo, "send", fake.send)
    monkeypatch.setattr(expo, "receipts", fake.receipts)
    yield fake


def register_device(user: AbstractBaseUser, token: str, **fields: Any) -> PushToken:
    session = sign_in(user).session
    return PushToken.objects.create(
        user=user, session=session, token=token, platform=PushToken.Platform.IOS, **fields
    )
