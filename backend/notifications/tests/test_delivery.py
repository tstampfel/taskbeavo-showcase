"""Sending one delivery: Expo tickets and receipts, dead tokens, retries and telemetry."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from accounts.models import RefreshToken, User
from accounts.tests.factories import make_user
from core import email as core_email
from core.models import Outbox
from core.outbox import outbox_key
from notifications import expo
from notifications.delivery import DeliveryResult, check_receipts, deliver, purge_finished
from notifications.models import Channel, Delivery, PushTicket, PushToken
from notifications.tests.conftest import FakeExpo, register_device

pytestmark = pytest.mark.django_db

TOKEN = "ExponentPushToken[ana-phone-0001]"
OTHER_TOKEN = "ExponentPushToken[ana-tablet-001]"


@pytest.fixture
def ana() -> User:
    return make_user("ana", locale="de")


def make_delivery(user: User, channel: str, kind: str = "booking.payment_requested") -> Delivery:
    with transaction.atomic():
        row = Outbox.objects.create(
            topic="test.delivery", aggregate_type="bookings.Booking", aggregate_id="7"
        )
    return Delivery.objects.create(outbox=row, user=user, kind=kind, channel=channel)


def test_ok_tickets_are_kept_for_their_receipts(fake_expo: FakeExpo, ana: User) -> None:
    register_device(ana, TOKEN)
    delivery = make_delivery(ana, Channel.PUSH)

    assert deliver(delivery.pk) == (DeliveryResult.SENT, None)

    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SENT
    assert delivery.sent_at is not None
    assert PushTicket.objects.filter(delivery=delivery).count() == 1


def test_device_not_registered_on_a_ticket_deletes_the_token(
    fake_expo: FakeExpo, ana: User
) -> None:
    register_device(ana, TOKEN)
    register_device(ana, OTHER_TOKEN)
    fake_expo.tickets_for[TOKEN] = expo.Result(ok=False, error=expo.DEVICE_NOT_REGISTERED)

    assert deliver(make_delivery(ana, Channel.PUSH).pk)[0] == DeliveryResult.SENT

    assert list(PushToken.objects.values_list("token", flat=True)) == [OTHER_TOKEN]


def test_only_dead_devices_means_skipped_not_retried(fake_expo: FakeExpo, ana: User) -> None:
    register_device(ana, TOKEN)
    fake_expo.tickets_for[TOKEN] = expo.Result(ok=False, error=expo.DEVICE_NOT_REGISTERED)

    assert deliver(make_delivery(ana, Channel.PUSH).pk)[0] == DeliveryResult.SKIPPED
    assert not PushToken.objects.exists()


def test_tokens_of_signed_out_sessions_are_deleted_not_used(fake_expo: FakeExpo, ana: User) -> None:
    device = register_device(ana, TOKEN)
    device.session.revoked_at = timezone.now()
    device.session.save()

    assert deliver(make_delivery(ana, Channel.PUSH).pk)[0] == DeliveryResult.SKIPPED
    assert fake_expo.sent == []
    assert not PushToken.objects.exists()


def test_tokens_of_expired_sessions_are_deleted_not_used(fake_expo: FakeExpo, ana: User) -> None:
    device = register_device(ana, TOKEN)
    RefreshToken.objects.filter(session=device.session).update(expires_at=timezone.now())

    assert deliver(make_delivery(ana, Channel.PUSH).pk)[0] == DeliveryResult.SKIPPED
    assert fake_expo.sent == []
    assert not PushToken.objects.exists()


def test_the_daily_purge_deletes_tokens_of_expired_sessions(ana: User) -> None:
    dead = register_device(ana, TOKEN)
    live = register_device(ana, OTHER_TOKEN)
    RefreshToken.objects.filter(session=dead.session).update(expires_at=timezone.now())

    assert purge_finished() == 1
    assert list(PushToken.objects.all()) == [live]


def test_a_failed_send_is_retried_with_backoff_then_given_up(
    fake_expo: FakeExpo, ana: User
) -> None:
    register_device(ana, TOKEN)
    fake_expo.fail_with = expo.ExpoError("PUSH_TOO_MANY_EXPERIENCE_IDS")
    delivery = make_delivery(ana, Channel.PUSH)

    with override_settings(NOTIFICATION_MAX_ATTEMPTS=2):
        result, retry_at = deliver(delivery.pk)
        assert result == DeliveryResult.RETRY_SCHEDULED
        assert retry_at is not None and retry_at > timezone.now()
        # Not due yet: a second worker does nothing.
        assert deliver(delivery.pk) == (DeliveryResult.NOT_DUE, None)

        Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
        assert deliver(delivery.pk) == (DeliveryResult.FAILED, None)

    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.attempts == 2
    assert delivery.last_error == "ExpoError"


def test_rate_limited_tickets_are_retried(fake_expo: FakeExpo, ana: User) -> None:
    register_device(ana, TOKEN)
    fake_expo.tickets_for[TOKEN] = expo.Result(ok=False, error="MessageRateExceeded")

    assert deliver(make_delivery(ana, Channel.PUSH).pk)[0] == DeliveryResult.RETRY_SCHEDULED
    assert PushToken.objects.exists()


def test_users_deactivated_since_the_event_get_nothing(fake_expo: FakeExpo, ana: User) -> None:
    register_device(ana, TOKEN)
    push, email = make_delivery(ana, Channel.PUSH), make_delivery(ana, Channel.EMAIL)
    ana.is_active = False
    ana.save()

    assert deliver(push.pk)[0] == DeliveryResult.SKIPPED
    assert deliver(email.pk)[0] == DeliveryResult.SKIPPED
    assert fake_expo.sent == []
    assert mail.outbox == []


def test_a_sent_delivery_is_never_sent_again(fake_expo: FakeExpo, ana: User) -> None:
    register_device(ana, TOKEN)
    delivery = make_delivery(ana, Channel.PUSH)
    deliver(delivery.pk)

    assert deliver(delivery.pk) == (DeliveryResult.NOT_DUE, None)
    assert len(fake_expo.sent) == 1


def test_email_passes_a_stable_idempotency_key(monkeypatch: pytest.MonkeyPatch, ana: User) -> None:
    keys: list[str | None] = []
    original = core_email.send_email

    def recording_send_email(to: str, email: Any, *, idempotency_key: str | None = None) -> None:
        keys.append(idempotency_key)
        original(to, email, idempotency_key=idempotency_key)

    monkeypatch.setattr("notifications.delivery.send_email", recording_send_email)
    delivery = make_delivery(ana, Channel.EMAIL)

    assert deliver(delivery.pk)[0] == DeliveryResult.SENT

    assert keys == [f"{outbox_key(delivery.outbox, 'email')}:user:{ana.pk}"]
    (message,) = mail.outbox
    assert message.extra_headers["Content-Language"] == "de"
    assert "Öffnen Sie TaskBeavo" in message.body


def test_receipts_delete_dead_tokens_and_finish_tickets(fake_expo: FakeExpo, ana: User) -> None:
    dead = register_device(ana, TOKEN)
    alive = register_device(ana, OTHER_TOKEN)
    delivery = make_delivery(ana, Channel.PUSH)
    old = timezone.now() - timedelta(minutes=20)
    for token, ticket_id, created_at in [
        (dead, "t-dead", old),
        (alive, "t-ok", old),
        (alive, "t-pending", old),
        (alive, "t-new", timezone.now()),
    ]:
        PushTicket.objects.create(
            delivery=delivery, push_token=token, ticket_id=ticket_id, created_at=created_at
        )
    fake_expo.receipts_by_id = {
        "t-dead": expo.Result(ok=False, error=expo.DEVICE_NOT_REGISTERED),
        "t-ok": expo.Result(ok=True),
    }

    assert check_receipts() == 1

    assert fake_expo.receipt_requests == [["t-dead", "t-ok", "t-pending"]]
    assert list(PushToken.objects.values_list("token", flat=True)) == [OTHER_TOKEN]
    # No receipt yet, or too new to check: kept for the next run.
    assert set(PushTicket.objects.values_list("ticket_id", flat=True)) == {"t-pending", "t-new"}


def test_tickets_past_expos_retention_are_dropped(fake_expo: FakeExpo, ana: User) -> None:
    device = register_device(ana, TOKEN)
    PushTicket.objects.create(
        delivery=make_delivery(ana, Channel.PUSH),
        push_token=device,
        ticket_id="t-stale",
        created_at=timezone.now() - timedelta(days=2),
    )

    assert check_receipts() == 0
    assert fake_expo.receipt_requests == []
    assert not PushTicket.objects.exists()


def test_finished_deliveries_are_purged_after_their_retention(ana: User) -> None:
    old = timezone.now() - timedelta(days=91)
    sent = make_delivery(ana, Channel.EMAIL)
    pending = make_delivery(ana, Channel.PUSH)
    recent = make_delivery(ana, Channel.EMAIL)
    Delivery.objects.filter(pk=sent.pk).update(status=Delivery.Status.SENT, created_at=old)
    Delivery.objects.filter(pk=pending.pk).update(created_at=old)
    Delivery.objects.filter(pk=recent.pk).update(status=Delivery.Status.SENT)

    assert purge_finished() == 1
    assert set(Delivery.objects.values_list("pk", flat=True)) == {pending.pk, recent.pk}


def test_spans_carry_ids_and_kinds_but_no_token_or_address(
    fake_expo: FakeExpo, ana: User, spans: InMemorySpanExporter
) -> None:
    register_device(ana, TOKEN)
    deliver(make_delivery(ana, Channel.PUSH).pk)
    deliver(make_delivery(ana, Channel.EMAIL).pk)

    delivered = [span for span in spans.get_finished_spans() if span.name == "notification.deliver"]
    attributes = [dict(span.attributes or {}) for span in delivered]
    assert sorted(str(a["notification.channel"]) for a in attributes) == ["email", "push"]
    assert {str(a["notification.kind"]) for a in attributes} == {"booking.payment_requested"}
    exported = repr([dict(span.attributes or {}) for span in spans.get_finished_spans()])
    assert "ExponentPushToken" not in exported
    assert "ana@example.com" not in exported
