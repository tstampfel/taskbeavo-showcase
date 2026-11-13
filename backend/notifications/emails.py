"""Kinds whose email is a template of its own instead of the generic notification email.

Most kinds email their `title` and `body` with a link (core/templates/email/notification).
A kind that must say more, like the contract confirmation with its attached cancellation
policy, registers a renderer from its own app:

    @register_email("booking.contract_confirmation", on_sent=record_sent)
    def render(delivery: Delivery) -> CustomEmail: ...

The renderer runs when the delivery is sent (notifications/delivery.py), in the
recipient's language, and may raise: the delivery is retried, then FAILED and logged.
`on_sent` runs in the delivery's transaction after the send, e.g. to record proof of it,
with the provider's message ID ("" when the backend gives none). `to` sends to another
address of the recipient's own than their account email (a withdrawal statement names
the address its confirmation goes to).

`on_failed` runs once, in the delivery's transaction, when the delivery has used its
NOTIFICATION_MAX_ATTEMPTS (e.g. to flag the booking for support; IDs only). A kind the
law requires to arrive (the withdrawal confirmation) passes `slow_retry`: from then on it
keeps being tried every `slow_retry.every` until `slow_retry.for_` after the delivery was
created, instead of FAILED, and stops as soon as it is sent.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from core.email import Attachment, RenderedEmail
from notifications.models import Delivery


@dataclass(frozen=True)
class CustomEmail:
    email: RenderedEmail
    attachments: list[Attachment] = field(default_factory=list)
    to: str = ""


Renderer = Callable[[Delivery], CustomEmail]
SentHook = Callable[[Delivery, CustomEmail, str], None]
FailedHook = Callable[[Delivery], None]


@dataclass(frozen=True)
class SlowRetry:
    every: timedelta
    for_: timedelta


@dataclass(frozen=True)
class _Registration:
    render: Renderer
    on_sent: SentHook | None
    on_failed: FailedHook | None = None
    slow_retry: SlowRetry | None = None


_registry: dict[str, _Registration] = {}


def register_email(
    kind_key: str,
    *,
    on_sent: SentHook | None = None,
    on_failed: FailedHook | None = None,
    slow_retry: SlowRetry | None = None,
) -> Callable[[Renderer], Renderer]:
    def register(render: Renderer) -> Renderer:
        if kind_key in _registry:
            raise ValueError(f"An email renderer for {kind_key} is already registered.")
        _registry[kind_key] = _Registration(render, on_sent, on_failed, slow_retry)
        return render

    return register


def custom_email(kind_key: str) -> _Registration | None:
    return _registry.get(kind_key)
