"""The offers app's outbox topics: a new offer, the poster's expiry reminder and a
withdrawal, each a push (and live event) to the task's poster only. The withdrawal is also
the seam checkout (BEAV-109) builds on. An edit or renewal is a live event only."""

import datetime as dt

import pytest
from django.core import mail
from django.utils import timezone

from accounts.models import User
from core import outbox
from core.models import Outbox
from notifications.delivery import DeliveryResult, deliver
from notifications.models import Delivery
from notifications.tests.conftest import register_device
from offers import services
from offers.models import Offer
from offers.tests.factories import make_helper, open_task
from tasks import services as tasks

pytestmark = pytest.mark.django_db


def flexible_offer(helper: str = "marko") -> Offer:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    return services.send_offer(make_helper(helper), str(task.pk), 3_000)


def queue_reminder(offer: Offer) -> Outbox:
    # Queued "now" for an offer sent six days ago that expires in under 24 hours.
    now = timezone.now()
    Offer.objects.filter(pk=offer.pk).update(
        expires_at=now + dt.timedelta(hours=20), lifetime_started_at=now - dt.timedelta(days=6)
    )
    assert services.queue_expiry_reminders() == 1
    return Outbox.objects.get(topic=services.EXPIRING_TOPIC, aggregate_id=str(offer.pk))


@pytest.fixture
def live_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[int]]]:
    """(topic, user IDs) of every live event the offer handlers send."""
    sent: list[tuple[str, list[int]]] = []
    monkeypatch.setattr(
        "offers.outbox_handlers.send_to_users",
        lambda row, user_ids: sent.append((row.topic, sorted(user_ids))),
    )
    return sent


def row_of(topic: str, offer: Offer) -> Outbox:
    return Outbox.objects.get(topic=topic, aggregate_id=str(offer.pk))


def test_every_topic_has_a_handler() -> None:
    assert services.RECEIVED_TOPIC in outbox._handlers
    assert services.EXPIRING_TOPIC in outbox._handlers
    assert services.WITHDRAWN_TOPIC in outbox._handlers
    assert services.UPDATED_TOPIC in outbox._handlers


def test_a_new_offer_reaches_the_poster_live_and_by_push(live_events) -> None:
    offer = flexible_offer()
    register_device(offer.task.poster, "ExponentPushToken[poster]")
    row = row_of(services.RECEIVED_TOPIC, offer)
    assert row.payload == {"task_id": str(offer.task_id)}

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert live_events == [(services.RECEIVED_TOPIC, [offer.task.poster_id])]
    deliveries = Delivery.objects.filter(outbox=row)
    assert {(d.user_id, d.kind, d.channel) for d in deliveries} == {
        (offer.task.poster_id, "offer.received", "push")
    }


def test_a_poster_without_the_app_gets_one_email_per_offer(live_events) -> None:
    """Web-only posters (no push device) hear of each new offer by email until the
    daily digest (BEAV-178) replaces it."""
    offer = flexible_offer()
    row = row_of(services.RECEIVED_TOPIC, offer)

    outbox.publish_row(row.pk)

    deliveries = Delivery.objects.filter(outbox=row, user=offer.task.poster)
    assert sorted(d.channel for d in deliveries) == ["email", "push"]


def test_no_new_offer_notice_once_the_offer_was_withdrawn(live_events) -> None:
    offer = flexible_offer()
    services.withdraw_offer(offer.helper, str(offer.pk))

    outbox.publish_row(row_of(services.RECEIVED_TOPIC, offer).pk)

    assert not Delivery.objects.filter(kind="offer.received").exists()
    assert (services.RECEIVED_TOPIC, [offer.task.poster_id]) not in live_events


def test_the_helper_is_never_notified_of_their_own_offer(live_events) -> None:
    offer = flexible_offer()
    outbox.publish_row(row_of(services.RECEIVED_TOPIC, offer).pk)

    assert not Delivery.objects.filter(user=offer.helper).exists()
    assert all(offer.helper_id not in users for _, users in live_events)


def test_the_reminder_notifies_the_poster_only(live_events) -> None:
    offer = flexible_offer()
    register_device(offer.task.poster, "ExponentPushToken[poster]")
    row = queue_reminder(offer)

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert live_events[-1] == (services.EXPIRING_TOPIC, [offer.task.poster_id])
    deliveries = Delivery.objects.filter(outbox=row)
    assert {d.user_id for d in deliveries} == {offer.task.poster_id}
    assert {d.kind for d in deliveries} == {"offer.expiring"}
    # The poster has the app: push only.
    assert {d.channel for d in deliveries} == {"push"}


def test_a_poster_without_the_app_gets_the_reminder_by_email(live_events) -> None:
    offer = flexible_offer()
    row = queue_reminder(offer)

    outbox.publish_row(row.pk)

    deliveries = Delivery.objects.filter(outbox=row, user=offer.task.poster)
    assert sorted(d.channel for d in deliveries) == ["email", "push"]
    assert {d.kind for d in deliveries} == {"offer.expiring"}


SUBJECTS = {
    services.RECEIVED_TOPIC: {
        "hr": "Nova ponuda za Vaš zadatak",
        "en": "New offer on your task",
        "de": "Neues Angebot für Ihre Aufgabe",
        "fr": "Nouvelle offre pour votre tâche",
    },
    services.EXPIRING_TOPIC: {
        "hr": "Ponuda uskoro istječe",
        "en": "An offer expires soon",
        "de": "Ein Angebot läuft bald ab",
        "fr": "Une offre expire bientôt",
    },
}


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
@pytest.mark.parametrize("topic", sorted(SUBJECTS))
def test_offer_emails_are_in_the_posters_language_without_offer_details(
    live_events, topic: str, locale: str
) -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    User.objects.filter(pk=task.poster_id).update(locale=locale)
    offer = services.send_offer(make_helper(), str(task.pk), 4_321, "Gladly, tomorrow at nine")
    row = queue_reminder(offer) if topic == services.EXPIRING_TOPIC else row_of(topic, offer)
    outbox.publish_row(row.pk)
    email = Delivery.objects.get(outbox=row, channel="email")

    assert deliver(email.pk)[0] == DeliveryResult.SENT

    (message,) = mail.outbox
    assert SUBJECTS[topic][locale] in message.subject
    text = message.subject + message.body
    for detail in ("Gladly", "43,21", "43.21", "4321", "Marko", "Horvat"):
        assert detail not in text


def test_no_reminder_once_the_offer_was_withdrawn_or_the_task_closed() -> None:
    offer = flexible_offer()
    row = queue_reminder(offer)
    services.withdraw_offer(offer.helper, str(offer.pk))

    outbox.publish_row(row.pk)
    assert not Delivery.objects.filter(outbox=row).exists()

    other = flexible_offer("ivan")
    second = queue_reminder(other)
    tasks.close_task(other.task)
    outbox.publish_row(second.pk)
    assert not Delivery.objects.filter(outbox=second).exists()


def test_a_withdrawal_reaches_the_poster_live_and_by_push(live_events) -> None:
    offer = flexible_offer()
    services.withdraw_offer(offer.helper, str(offer.pk))
    row = Outbox.objects.get(topic=services.WITHDRAWN_TOPIC)

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert live_events == [(services.WITHDRAWN_TOPIC, [offer.task.poster_id])]
    deliveries = Delivery.objects.filter(outbox=row)
    assert {(d.user_id, d.kind, d.channel) for d in deliveries} == {
        (offer.task.poster_id, "offer.withdrawn", "push")
    }


def test_no_withdrawal_notice_once_the_task_closed(live_events) -> None:
    offer = flexible_offer()
    services.withdraw_offer(offer.helper, str(offer.pk))
    tasks.close_task(offer.task)

    outbox.publish_row(Outbox.objects.get(topic=services.WITHDRAWN_TOPIC).pk)

    assert not Delivery.objects.filter(kind="offer.withdrawn").exists()
    assert live_events == []


def test_the_reminder_is_dropped_when_the_offer_was_deleted(live_events) -> None:
    offer = flexible_offer()
    row = queue_reminder(offer)
    offer.task.delete()

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert not Delivery.objects.filter(outbox=row).exists()


@pytest.mark.parametrize("change", ["update", "renew"])
def test_an_edit_or_renewal_reaches_the_poster_live_only(live_events, change: str) -> None:
    """offer.updated keeps an open offers list current; it is never a push or an email."""
    offer = flexible_offer()
    register_device(offer.task.poster, "ExponentPushToken[poster]")
    if change == "update":
        services.update_offer(offer.helper, str(offer.pk), price_cents=2_500)
    else:
        services.renew_offer(offer.helper, str(offer.pk))
    row = row_of(services.UPDATED_TOPIC, offer)
    assert row.payload == {"task_id": str(offer.task_id)}

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert live_events == [(services.UPDATED_TOPIC, [offer.task.poster_id])]
    assert not Delivery.objects.filter(outbox=row).exists()
    assert mail.outbox == []


def test_no_update_event_once_the_offer_was_withdrawn_or_the_task_closed(live_events) -> None:
    offer = flexible_offer()
    services.update_offer(offer.helper, str(offer.pk), price_cents=2_500)
    services.withdraw_offer(offer.helper, str(offer.pk))
    second = flexible_offer("ivan")
    services.renew_offer(second.helper, str(second.pk))
    tasks.close_task(second.task)

    outbox.publish_row(row_of(services.UPDATED_TOPIC, offer).pk)
    outbox.publish_row(row_of(services.UPDATED_TOPIC, second).pk)

    assert live_events == []
