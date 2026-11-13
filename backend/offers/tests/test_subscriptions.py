"""Live offer events over the served WebSocket: offerReceived, offerWithdrawn and
offerExpiring reach the task's poster only, with the full current offer (hard rule d)."""

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
from core.models import Outbox
from core.realtime import send_to_users, user_group
from offers import services
from offers.models import Offer
from offers.tests.factories import make_helper, open_task
from offers.tests.test_outbox_handlers import queue_reminder
from tasks.models import Task

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _dubrovnik() -> None:
    # Transactional tests flush the database after each run, seeded service area included.
    seed = importlib.import_module("areas.migrations.0002_seed_dubrovnik").seed
    seed(django_apps, None)


FIELDS = "id taskId status priceCents message isMine"


def subscription(field: str) -> str:
    return f"subscription($taskId: ID!) {{ {field}(taskId: $taskId) {{ {FIELDS} }} }}"


async def subscribed(user: AbstractBaseUser, field: str, task: Task) -> WebsocketCommunicator:
    from config.asgi import application

    token = await sync_to_async(access_token)(user)
    ws = WebsocketCommunicator(application, "/graphql", subprotocols=["graphql-transport-ws"])
    assert (await ws.connect())[0]
    await ws.send_json_to({"type": "connection_init", "payload": {"authToken": token}})
    assert await ws.receive_json_from(timeout=5) == {"type": "connection_ack"}
    await ws.send_json_to(
        {
            "id": "1",
            "type": "subscribe",
            "payload": {"query": subscription(field), "variables": {"taskId": str(task.pk)}},
        }
    )
    layer = get_channel_layer()
    assert layer is not None
    for _ in range(100):
        if layer.groups.get(user_group(user.pk)):  # pyright: ignore[reportAttributeAccessIssue]
            return ws
        await asyncio.sleep(0.02)
    raise AssertionError("The subscription never joined the user's group.")


def event(field: str, offer: Offer, status: str) -> dict[str, Any]:
    return {
        "id": "1",
        "type": "next",
        "payload": {
            "data": {
                field: {
                    "id": str(offer.pk),
                    "taskId": str(offer.task_id),
                    "status": status,
                    "priceCents": offer.price_cents,
                    "message": offer.message,
                    "isMine": False,
                }
            }
        },
    }


def flexible_task() -> Task:
    return open_task(window_start=None, window_end=None, flexible_date=True)


def test_the_poster_sees_a_new_offer_live_and_the_helper_does_not() -> None:
    task = flexible_task()
    marko = make_helper()
    ivan = make_helper("ivan")

    async def run() -> None:
        poster_ws = await subscribed(task.poster, "offerReceived", task)
        # Another helper watching the same task ID gets nothing (no task-named groups).
        ivan_ws = await subscribed(ivan, "offerReceived", task)

        offer = await sync_to_async(services.send_offer)(marko, str(task.pk), 3_000, "Hi")

        assert await poster_ws.receive_json_from(timeout=5) == event("offerReceived", offer, "SENT")
        assert await ivan_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()
        await ivan_ws.disconnect()

    async_to_sync(run)()


def test_a_misaddressed_event_is_dropped_by_the_per_event_check() -> None:
    """Even if a handler named the wrong user, the offer is re-checked for each event."""
    task = flexible_task()
    marko, ivan = make_helper(), make_helper("ivan")
    offer = services.send_offer(marko, str(task.pk), 3_000)
    row = Outbox.objects.get(topic=services.RECEIVED_TOPIC)

    async def run() -> None:
        ivan_ws = await subscribed(ivan, "offerReceived", task)

        await sync_to_async(send_to_users)(row, [ivan.pk])

        assert await ivan_ws.receive_nothing(timeout=0.3)
        await ivan_ws.disconnect()

    assert offer.task.poster_id != ivan.pk
    async_to_sync(run)()


def test_events_of_another_task_are_not_delivered() -> None:
    first, second = flexible_task(), flexible_task()
    Task.objects.filter(pk=first.pk).update(poster=second.poster)
    marko = make_helper()

    async def run() -> None:
        poster_ws = await subscribed(second.poster, "offerReceived", first)

        await sync_to_async(services.send_offer)(marko, str(second.pk), 3_000)

        assert await poster_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()

    async_to_sync(run)()


def test_a_redelivered_row_reaches_the_poster_once() -> None:
    task = flexible_task()
    marko = make_helper()

    async def run() -> None:
        poster_ws = await subscribed(task.poster, "offerReceived", task)
        await sync_to_async(services.send_offer)(marko, str(task.pk), 3_000)
        assert (await poster_ws.receive_json_from(timeout=5))["type"] == "next"
        row = await Outbox.objects.aget(topic=services.RECEIVED_TOPIC)

        await sync_to_async(send_to_users)(row, [task.poster_id])

        assert await poster_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()

    async_to_sync(run)()


def test_the_poster_sees_a_withdrawal_live() -> None:
    task = flexible_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    async def run() -> None:
        poster_ws = await subscribed(task.poster, "offerWithdrawn", task)

        await sync_to_async(services.withdraw_offer)(marko, str(offer.pk))

        assert await poster_ws.receive_json_from(timeout=5) == event(
            "offerWithdrawn", offer, "WITHDRAWN"
        )
        await poster_ws.disconnect()

    async_to_sync(run)()


def test_the_poster_sees_an_expiring_offer_live() -> None:
    task = flexible_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)

    async def run() -> None:
        poster_ws = await subscribed(task.poster, "offerExpiring", task)

        # queue_expiry_reminders writes the row; Celery (eager) publishes it on commit.
        await sync_to_async(queue_reminder)(offer)

        assert await poster_ws.receive_json_from(timeout=5) == event("offerExpiring", offer, "SENT")
        await poster_ws.disconnect()

    async_to_sync(run)()
    assert Outbox.objects.get(topic=services.EXPIRING_TOPIC).status == Outbox.Status.PUBLISHED


def test_the_poster_sees_an_edit_and_a_renewal_live_and_the_helper_does_not() -> None:
    task = flexible_task()
    marko = make_helper()
    ivan = make_helper("ivan")
    offer = services.send_offer(marko, str(task.pk), 3_000)

    async def run() -> None:
        poster_ws = await subscribed(task.poster, "offerUpdated", task)
        ivan_ws = await subscribed(ivan, "offerUpdated", task)

        edited = await sync_to_async(services.update_offer)(marko, str(offer.pk), price_cents=2_500)
        assert await poster_ws.receive_json_from(timeout=5) == event("offerUpdated", edited, "SENT")
        renewed = await sync_to_async(services.renew_offer)(marko, str(offer.pk))
        assert await poster_ws.receive_json_from(timeout=5) == event(
            "offerUpdated", renewed, "SENT"
        )
        assert await ivan_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()
        await ivan_ws.disconnect()

    async_to_sync(run)()


def test_a_misaddressed_update_is_dropped_by_the_per_event_check() -> None:
    task = flexible_task()
    marko, ivan = make_helper(), make_helper("ivan")
    offer = services.send_offer(marko, str(task.pk), 3_000)
    services.update_offer(marko, str(offer.pk), price_cents=2_500)
    row = Outbox.objects.get(topic=services.UPDATED_TOPIC)

    async def run() -> None:
        ivan_ws = await subscribed(ivan, "offerUpdated", task)

        await sync_to_async(send_to_users)(row, [ivan.pk])

        assert await ivan_ws.receive_nothing(timeout=0.3)
        await ivan_ws.disconnect()

    async_to_sync(run)()
