"""`messageAdded` over the served WebSocket: only the conversation's participants receive
message events, each re-checked against the current conversation (hard rule d)."""

import asyncio
import importlib
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.apps import apps as django_apps
from django.contrib.auth.models import AbstractBaseUser

from accounts.tests.factories import access_token
from chat import services
from chat.models import Conversation, Message
from chat.tests.test_services import offer_conversation, offer_on_flexible_task
from core.models import Outbox
from core.realtime import send_to_users, user_group
from offers.tests.factories import make_helper
from tasks.models import Task

pytestmark = pytest.mark.django_db(transaction=True)

QUERY = """
subscription($conversationId: ID!) {
  messageAdded(conversationId: $conversationId) { id conversationId body senderRole isMine }
}
"""


@pytest.fixture(autouse=True)
def _dubrovnik() -> None:
    # Transactional tests flush the database after each run, seeded service area included.
    seed = importlib.import_module("areas.migrations.0002_seed_dubrovnik").seed
    seed(django_apps, None)


async def subscribe(user: AbstractBaseUser, conversation_id: str) -> WebsocketCommunicator:
    """A signed-in socket that has sent the subscribe message."""
    from config.asgi import application

    token = await sync_to_async(access_token)(user)
    ws = WebsocketCommunicator(application, "/graphql", subprotocols=["graphql-transport-ws"])
    assert (await ws.connect())[0]
    await ws.send_json_to({"type": "connection_init", "payload": {"authToken": token}})
    assert await ws.receive_json_from(timeout=5) == {"type": "connection_ack"}
    variables = {"conversationId": conversation_id}
    await ws.send_json_to(
        {"id": "1", "type": "subscribe", "payload": {"query": QUERY, "variables": variables}}
    )
    return ws


async def refusal_codes(ws: WebsocketCommunicator) -> list[str]:
    """The error codes of a subscription refused at connect, which then completes."""
    refusal = await ws.receive_json_from(timeout=5)
    assert refusal["payload"]["data"] is None, refusal
    assert (await ws.receive_json_from(timeout=5))["type"] == "complete"
    return [error["extensions"]["code"] for error in refusal["payload"]["errors"]]


async def subscribed(user: AbstractBaseUser, conversation: Conversation) -> WebsocketCommunicator:
    ws = await subscribe(user, str(conversation.pk))
    layer = get_channel_layer()
    assert layer is not None
    for _ in range(100):
        if layer.groups.get(user_group(user.pk)):  # pyright: ignore[reportAttributeAccessIssue]
            return ws
        await asyncio.sleep(0.02)
    raise AssertionError("The subscription never joined the user's group.")


def event(message: Message, *, mine: bool) -> dict[str, Any]:
    return {
        "id": "1",
        "type": "next",
        "payload": {
            "data": {
                "messageAdded": {
                    "id": str(message.pk),
                    "conversationId": str(message.conversation_id),
                    "body": message.body,
                    "senderRole": message.sender_role,
                    "isMine": mine,
                }
            }
        },
    }


def test_both_participants_receive_the_message_and_nobody_else() -> None:
    offer, conversation = offer_conversation()
    ivan = make_helper("ivan")

    async def run() -> None:
        poster_ws = await subscribed(offer.task.poster, conversation)
        helper_ws = await subscribed(offer.helper, conversation)
        # Another helper is refused when subscribing with the same conversation ID
        # (the connect-time check, hard rule d).
        ivan_ws = await subscribe(ivan, str(conversation.pk))
        assert await refusal_codes(ivan_ws) == ["CONVERSATION_NOT_FOUND"]

        message = await sync_to_async(services.send_message)(
            offer.helper, str(conversation.pk), "Call me on 091 234 5678"
        )

        assert await poster_ws.receive_json_from(timeout=5) == event(message, mine=False)
        assert await helper_ws.receive_json_from(timeout=5) == event(message, mine=True)
        assert "5678" not in message.body
        assert await ivan_ws.receive_nothing(timeout=0.3)
        for ws in (poster_ws, helper_ws, ivan_ws):
            await ws.disconnect()

    async_to_sync(run)()


def test_a_misaddressed_event_is_dropped_by_the_per_event_check() -> None:
    """Even if a handler named the wrong user, the message is re-checked for each event:
    Ivan, subscribed to his own conversation, never gets Marko's."""
    offer, conversation = offer_conversation()
    ivan_offer = offer_on_flexible_task(make_helper("ivan"))
    ivan = ivan_offer.helper
    own = services.open_conversation(ivan, str(ivan_offer.pk))
    services.send_message(offer.helper, str(conversation.pk), "Hi")
    row = Outbox.objects.get(topic=services.ADDED_TOPIC)

    async def run() -> None:
        ivan_ws = await subscribed(ivan, own)

        await sync_to_async(send_to_users)(row, [ivan.pk])

        assert await ivan_ws.receive_nothing(timeout=0.3)
        await ivan_ws.disconnect()

    async_to_sync(run)()


def test_messages_of_another_conversation_are_not_delivered() -> None:
    offer, first = offer_conversation()
    # The same poster talks to a second helper on another task.
    other = offer_on_flexible_task(make_helper("ivan"))
    Task.objects.filter(pk=other.task_id).update(poster=offer.task.poster)
    second = services.open_conversation(other.helper, str(other.pk))

    async def run() -> None:
        poster_ws = await subscribed(offer.task.poster, first)

        await sync_to_async(services.send_message)(other.helper, str(second.pk), "Hello")

        assert await poster_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()

    async_to_sync(run)()


def test_a_malformed_conversation_id_is_refused_at_connect() -> None:
    offer, _conversation = offer_conversation()

    async def run() -> None:
        ws = await subscribe(offer.helper, "not-a-uuid")
        assert await refusal_codes(ws) == ["CONVERSATION_NOT_FOUND"]
        await ws.disconnect()

    async_to_sync(run)()
