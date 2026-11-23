"""`message.added`: live to both participants only, and a push to the other side that says
nothing about the message."""

import pytest

from chat import services
from chat.models import Message
from chat.tests.test_services import offer_conversation
from core import outbox
from core.models import Outbox
from notifications.kinds import KINDS
from notifications.models import Delivery
from notifications.tests.conftest import register_device

pytestmark = pytest.mark.django_db


@pytest.fixture
def live_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[int]]]:
    sent: list[tuple[str, list[int]]] = []
    monkeypatch.setattr(
        "chat.outbox_handlers.send_to_users",
        lambda row, user_ids: sent.append((row.topic, sorted(user_ids))),
    )
    return sent


def test_the_topic_has_a_handler() -> None:
    assert services.ADDED_TOPIC in outbox._handlers


def test_a_message_goes_live_to_both_participants_and_by_push_to_the_other(live_events) -> None:
    offer, conversation = offer_conversation()
    poster = offer.task.poster
    register_device(poster, "ExponentPushToken[poster]")
    message = services.send_message(offer.helper, str(conversation.pk), "Secret plan for Mama")
    row = Outbox.objects.get(topic=services.ADDED_TOPIC)

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert live_events == [(services.ADDED_TOPIC, sorted([poster.pk, offer.helper_id]))]
    deliveries = Delivery.objects.filter(outbox=row)
    # No email for chat, and never a push to the sender.
    assert {(d.user_id, d.kind, d.channel) for d in deliveries} == {
        (poster.pk, "message.added", "push")
    }
    kind = KINDS["message.added"]
    assert kind.route_for(row) == f"/conversations/{conversation.pk}"
    assert message.body not in f"{kind.title} {kind.body}"


def test_a_deleted_message_sends_nothing(live_events) -> None:
    offer, conversation = offer_conversation()
    services.send_message(offer.helper, str(conversation.pk), "Hi")
    row = Outbox.objects.get(topic=services.ADDED_TOPIC)
    Message.objects.all().delete()

    outbox.publish_row(row.pk)

    assert live_events == []
    assert not Delivery.objects.exists()
