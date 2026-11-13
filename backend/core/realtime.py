"""Live events: from an outbox handler to the subscriptions of the users it names.

Publishing: an outbox handler decides who may receive a row (participants only) and calls
`send_to_users(row, user_ids)`. Each user has one Channels group, `user.{id}`; there are
no groups named after tasks, bookings or other guessable resources. The message carries
the row's IDs and trace context, never the object or any personal data.

Receiving: a Strawberry subscription resolver returns `viewer_events(info, topics, load)`.
It listens on the connected user's group only and, for every message:

1. drops topics the subscription didn't ask for and outbox rows it already delivered
   (the publisher is at-least-once, so a row can arrive twice);
2. ends the subscription if the user has since been deactivated or the connection's
   session revoked (its socket is closed too, by `force_disconnect`; this covers a
   message lost on the way);
3. calls `load(user, event)`, which reloads the current object and re-checks that this
   user may see it now, returning None if not (hard rule d). Only what `load` returns is
   sent, so subscriptions yield the full current object with its `id`.

The WebSocket itself authenticates on `connection_init` (config/ws.py).
"""

from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Iterable
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from graphql import GraphQLError
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from strawberry.types import Info

from core.errors import ErrorCode
from core.models import Outbox
from core.tracing import context_from_traceparent

# Channels message type of a live event; the consumer routes it to `viewer_events`.
EVENT_TYPE = "outbox.event"
# Sent to `session_group(id)` when a session is revoked (accounts/sessions.py): the
# WebSockets of that session close (config/ws.py).
FORCE_DISCONNECT = "force_disconnect"
UNAUTHENTICATED = ErrorCode.UNAUTHENTICATED.name
# Outbox IDs remembered per subscription to drop redeliveries.
_SEEN_LIMIT = 256

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class LiveEvent:
    outbox_id: int
    topic: str
    aggregate_type: str
    aggregate_id: str


def user_group(user_id: int) -> str:
    """The Channels group of one user's open subscriptions."""
    return f"user.{int(user_id)}"


def session_group(session_id: object) -> str:
    """The Channels group of one session's open WebSockets, for `force_disconnect`.

    Named by the session's random UUID, which never leaves the server."""
    return f"session.{session_id}"


def send_to_users(row: Outbox, user_ids: Iterable[int]) -> None:
    """Send an outbox row to the live subscriptions of each user. Call it from a handler.

    Users without an open subscription simply get nothing here; push and email cover them.
    """
    message = {
        "type": EVENT_TYPE,
        "outbox_id": row.pk,
        "topic": row.topic,
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "traceparent": row.traceparent,
        "tracestate": row.tracestate,
    }
    layer = get_channel_layer()
    if layer is None:
        raise RuntimeError("CHANNEL_LAYERS is not configured.")
    for user_id in sorted(set(user_ids)):
        async_to_sync(layer.group_send)(user_group(user_id), message)


async def viewer_events[T](
    info: Info,
    topics: Collection[str],
    load: Callable[[AbstractBaseUser, LiveEvent], Awaitable[T | None]],
) -> AsyncGenerator[T, None]:
    """Yield `load(user, event)` for each event of `topics` sent to the connected user."""
    user = getattr(info.context, "user", None)
    if user is None or not user.is_authenticated:
        raise GraphQLError("Sign in to subscribe.", extensions={"code": UNAUTHENTICATED})
    ws = info.context.ws
    session = getattr(info.context, "session", None)
    seen: deque[int] = deque(maxlen=_SEEN_LIMIT)
    async with ws.listen_to_channel(EVENT_TYPE, groups=[user_group(user.pk)]) as messages:
        async for message in messages:
            event = _event(message)
            if event is None or event.topic not in topics or event.outbox_id in seen:
                continue
            seen.append(event.outbox_id)
            if not await _is_active(user.pk) or not await _is_live(session):
                return
            parent = context_from_traceparent(
                message.get("traceparent", ""), message.get("tracestate", "")
            )
            with tracer.start_as_current_span(
                "subscription.event", context=parent, kind=SpanKind.CONSUMER
            ) as span:
                span.set_attribute("outbox.id", str(event.outbox_id))
                span.set_attribute("notification.topic", event.topic)
                item = await load(user, event)
                span.set_attribute("subscription.delivered", item is not None)
            if item is not None:
                yield item


def _event(message: dict[str, Any]) -> LiveEvent | None:
    try:
        return LiveEvent(
            outbox_id=int(message["outbox_id"]),
            topic=str(message["topic"]),
            aggregate_type=str(message["aggregate_type"]),
            aggregate_id=str(message["aggregate_id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _is_active(user_id: int) -> bool:
    return await get_user_model().objects.filter(pk=user_id, is_active=True).aexists()


async def _is_live(session: Any) -> bool:
    """False once the connection's session is revoked; True for contexts without one."""
    if session is None:
        return True
    return await type(session).objects.filter(pk=session.pk, revoked_at__isnull=True).aexists()
