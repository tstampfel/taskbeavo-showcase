"""One outbox event → push and email per the rules, in each recipient's language.

The handler below stands in for a booking topic's handler: it names the recipients and
the acting user, and `notify` applies the rules. Celery runs eagerly in tests, so a
committed row is published and its deliveries are sent before `publish` returns.
"""

from typing import Any

import pytest
from django.core import mail
from django.db import transaction
from django.utils import timezone

from accounts import sessions
from accounts.models import RefreshToken, Session, User
from accounts.tests.factories import make_user, sign_in
from core import outbox
from core.errors import ApiError
from core.models import Outbox
from notifications import expo
from notifications.dispatch import notify
from notifications.models import Category, Channel, Delivery, NotificationPreference, PushToken
from notifications.tests.conftest import FakeExpo, register_device

pytestmark = pytest.mark.django_db(transaction=True)

TOPIC = "test.notifications.event"


@outbox.register_handler(TOPIC)
def _publish(row: Outbox) -> None:
    notify(row, row.payload["kind"], row.payload["recipient_ids"], actor_id=row.payload["actor_id"])


def publish(kind: str, recipients: list[User], actor: User, **payload: Any) -> Outbox:
    with transaction.atomic():
        return outbox.enqueue(
            TOPIC,
            actor,
            {
                "kind": kind,
                "recipient_ids": [user.pk for user in recipients],
                "actor_id": actor.pk,
                **payload,
            },
        )


@pytest.fixture
def helper() -> User:
    return make_user("marko", locale="hr")


def test_one_event_pushes_and_emails_each_recipient_in_their_own_language(
    fake_expo: FakeExpo, helper: User
) -> None:
    ana = make_user("ana", locale="de")
    luc = make_user("luc", locale="fr")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")
    register_device(luc, "ExponentPushToken[luc-phone-0001]")

    row = publish("booking.payment_requested", [ana, luc], helper)

    pushes = {message.to: message for message in fake_expo.sent}
    assert pushes["ExponentPushToken[ana-phone-0001]"].title == "Zahlung angefordert"
    assert pushes["ExponentPushToken[luc-phone-0001]"].title == "Paiement demandé"
    route = f"/bookings/{row.aggregate_id}/release"
    assert {message.data["route"] for message in fake_expo.sent} == {route}

    emails = {message.to[0]: message for message in mail.outbox}
    assert emails["ana@example.com"].subject == "Zahlung angefordert"
    assert f"http://localhost:3000/de{route}" in emails["ana@example.com"].body
    assert emails["luc@example.com"].subject == "Paiement demandé"
    assert f"http://localhost:3000/fr{route}" in emails["luc@example.com"].body
    assert set(Delivery.objects.values_list("status", flat=True)) == {Delivery.Status.SENT}


def test_the_actor_and_inactive_users_are_not_notified(fake_expo: FakeExpo, helper: User) -> None:
    gone = make_user("gone", is_active=False)
    register_device(helper, "ExponentPushToken[marko-phone-001]")
    register_device(gone, "ExponentPushToken[gone-phone-0001]")

    publish("booking.payment_requested", [helper, gone], helper)

    assert fake_expo.sent == []
    assert mail.outbox == []
    assert not Delivery.objects.exists()


def test_push_only_kinds_send_no_email(fake_expo: FakeExpo, helper: User) -> None:
    ana = make_user("ana", locale="en")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")

    publish("message.added", [ana], helper)

    assert [message.title for message in fake_expo.sent] == ["New message"]
    assert mail.outbox == []


def test_a_user_without_a_device_still_gets_the_email_and_no_push(
    fake_expo: FakeExpo, helper: User
) -> None:
    ana = make_user("ana", locale="de")

    publish("booking.cancelled", [ana], helper)

    assert fake_expo.sent == []
    assert [message.subject for message in mail.outbox] == ["Buchung storniert"]
    push = Delivery.objects.get(channel=Channel.PUSH)
    assert push.status == Delivery.Status.SKIPPED


def test_no_push_device_rule_emails_only_users_without_a_device(
    fake_expo: FakeExpo, helper: User
) -> None:
    web_only = make_user("ana", locale="en")
    with_phone = make_user("luc", locale="en")
    register_device(with_phone, "ExponentPushToken[luc-phone-0001]")

    publish("booking.change_requested", [web_only, with_phone], helper)

    assert [message.to for message in mail.outbox] == [["ana@example.com"]]
    assert [message.to for message in fake_expo.sent] == ["ExponentPushToken[luc-phone-0001]"]


def test_sign_out_deletes_the_devices_push_tokens(fake_expo: FakeExpo, helper: User) -> None:
    ana = make_user("ana", locale="en")
    issued = sign_in(ana)
    PushToken.objects.create(
        user=ana,
        session=issued.session,
        token="ExponentPushToken[ana-phone-0001]",
        platform=PushToken.Platform.IOS,
    )
    other = register_device(ana, "ExponentPushToken[ana-tablet-001]")

    sessions.revoke_session(issued.refresh_token)

    assert list(PushToken.objects.all()) == [other]


def test_refresh_token_reuse_also_deletes_the_devices_tokens(settings: Any) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 0
    ana = make_user("ana")
    issued = sign_in(ana)
    PushToken.objects.create(
        user=ana,
        session=issued.session,
        token="ExponentPushToken[ana-phone-0001]",
        platform=PushToken.Platform.IOS,
    )
    sessions.refresh_session(issued.refresh_token)

    with pytest.raises(ApiError):
        sessions.refresh_session(issued.refresh_token)

    assert not PushToken.objects.exists()


@pytest.mark.parametrize("ended", ["revoked", "expired"])
def test_a_device_of_an_ended_session_doesnt_count_for_the_email_fallback(
    fake_expo: FakeExpo, helper: User, ended: str
) -> None:
    ana = make_user("ana", locale="en")
    device = register_device(ana, "ExponentPushToken[ana-phone-0001]")
    # Bypasses sign-out's cleanup: a row left behind must still not count.
    if ended == "revoked":
        Session.objects.filter(pk=device.session.pk).update(revoked_at=timezone.now())
    else:
        RefreshToken.objects.filter(session=device.session).update(expires_at=timezone.now())

    publish("booking.change_requested", [ana], helper)

    assert fake_expo.sent == []
    assert [message.subject for message in mail.outbox] == ["Change requested"]
    assert not PushToken.objects.exists()


def test_a_push_that_finds_only_dead_devices_falls_back_to_email(
    fake_expo: FakeExpo, helper: User
) -> None:
    ana = make_user("ana", locale="de")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")
    fake_expo.tickets_for["ExponentPushToken[ana-phone-0001]"] = expo.Result(
        ok=False, error=expo.DEVICE_NOT_REGISTERED
    )

    row = publish("booking.change_requested", [ana], helper)
    # A redelivery doesn't add a second email.
    Outbox.objects.filter(pk=row.pk).update(status=Outbox.Status.PENDING)
    outbox.publish_row(row.pk)

    assert [message.subject for message in mail.outbox] == ["Änderung angefragt"]
    assert dict(Delivery.objects.values_list("channel", "status")) == {
        Channel.PUSH: Delivery.Status.SKIPPED,
        Channel.EMAIL: Delivery.Status.SENT,
    }


def test_switched_off_categories_are_respected(fake_expo: FakeExpo, helper: User) -> None:
    ana = make_user("ana", locale="en")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")
    NotificationPreference.objects.create(
        user=ana, category=Category.MESSAGES, channel=Channel.PUSH, enabled=False
    )

    publish("message.added", [ana], helper)

    assert fake_expo.sent == []
    assert not Delivery.objects.exists()


def test_booking_and_payment_messages_ignore_a_stored_opt_out(
    fake_expo: FakeExpo, helper: User
) -> None:
    ana = make_user("ana", locale="en")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")
    # Never written by the API (it refuses), but a stale row must not silence payments.
    for channel in Channel:
        NotificationPreference.objects.create(
            user=ana, category=Category.PAYMENT, channel=channel, enabled=False
        )

    publish("booking.payment_released", [ana], helper)

    assert [message.title for message in fake_expo.sent] == ["Payment released"]
    assert [message.subject for message in mail.outbox] == ["Payment released"]


def test_a_redelivered_row_sends_nothing_twice(fake_expo: FakeExpo, helper: User) -> None:
    ana = make_user("ana", locale="en")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")
    row = publish("booking.payment_requested", [ana], helper)

    # The worker died after the handler ran but before the outbox row was marked.
    Outbox.objects.filter(pk=row.pk).update(status=Outbox.Status.PENDING)
    outbox.publish_row(row.pk)

    assert len(fake_expo.sent) == 1
    assert len(mail.outbox) == 1
    assert Delivery.objects.count() == 2


def test_push_payload_holds_only_the_route(fake_expo: FakeExpo, helper: User) -> None:
    ana = make_user("ana", locale="hr")
    register_device(ana, "ExponentPushToken[ana-phone-0001]")

    publish("offer.received", [ana], helper, task_id=42)

    (message,) = fake_expo.sent
    assert message.data == {"route": "/tasks/42"}
    assert message.title == "Nova ponuda za Vaš zadatak"
    assert "Marko" not in message.body


def test_notify_outside_a_transaction_is_refused(helper: User) -> None:
    row = publish("message.added", [], helper)
    with pytest.raises(RuntimeError):
        notify(row, "message.added", [helper.pk])
