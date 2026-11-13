"""Payment webhooks (BEAV-110): FUNDS_HELD only from the webhook, with the helpers told and
the contract confirmed to the poster; a declined card leaving the checkout open for
another card; the 30-minute expiry cancelling abandoned and orphaned PaymentIntents;
support alerts for what Stripe refuses; and payout.failed telling the helper."""

import contextlib
import copy
import datetime as dt
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from django.conf import settings
from django.core import mail
from django.db import transaction
from django.test import Client
from django.utils import timezone, translation

from bookings import checkout, contract_email, payouts, state_machine
from bookings.alerts import money_alert
from bookings.checkout import FUNDS_HELD_TOPIC, LATE_PAYMENT_TOPIC, PAYMENT_ATTEMPT_FAILED_TOPIC
from bookings.models import Booking, BookingEvent
from bookings.payouts import PAYOUT_FAILED_TOPIC
from bookings.tests.factories import stripe_event
from bookings.tests.fakes import FakeGateway, intent_id
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    CANCELLATION,
    _fresh_rate_limits,
    accept,
    age,
    legal_documents,
    make_trader,
    post_event,
    publish,
    refused,
    succeeded_event,
    two_offers,
    webhook_secret,
    withdraw,
)
from config import telemetry
from config.tests.test_telemetry import MANGLED_UUIDS, RecordingTransport
from core.models import Outbox
from helpers.models import HelperProfile
from legal.checkout_consent import without_markers
from legal.models import LegalDocument
from notifications.models import Delivery
from notifications.tests.conftest import register_device
from offers.models import Offer
from payments.models import ConnectedAccount
from payments.stripe_client import StripeRequestError
from payments.tests.stripe_fakes import load_event

pytestmark = pytest.mark.django_db

S = Booking.Status


def paid_booking(client: Client, gateway: FakeGateway) -> tuple[Booking, Offer]:
    chosen, other = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    assert post_event(client, succeeded_event(booking)) == 200
    booking.refresh_from_db()
    return booking, other


def failed_event(booking: Booking, event_id: str = "evt_test_declined", **changes: Any) -> dict:
    event = copy.deepcopy(load_event("payment_intent.payment_failed"))
    event["id"] = event_id
    event["data"]["object"].update(
        {
            "id": booking.payment_intent_id,
            "amount": booking.amount_cents + booking.fee_cents,
            "metadata": {"booking_id": str(booking.pk)},
            **changes,
        }
    )
    return event


@pytest.fixture
def commit(django_capture_on_commit_callbacks: Any) -> Any:
    """Runs what commits inside it, then its on-commit callbacks: money alerts go out
    only once their transaction commits (review R1-3)."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


def deliveries(user_id: int, kind: str) -> set[str]:
    return set(
        Delivery.objects.filter(user_id=user_id, kind=kind).values_list("channel", flat=True)
    )


def alerts(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "bookings.support" and r.levelname == "ERROR"]


def open_for(booking: Booking, minutes: int) -> None:
    """The checkout was opened `minutes` ago."""
    Booking.objects.filter(pk=booking.pk).update(
        status_changed_at=timezone.now() - dt.timedelta(minutes=minutes)
    )


# FUNDS_HELD: the helpers and the poster are told (rule a: only the webhook gets here).


def test_funds_held_tells_the_helper_the_declined_helpers_and_the_poster(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, declined = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    for user in (booking.helper, declined.helper, booking.poster):
        register_device(user, f"ExponentPushToken[{user.pk}]")
    # Declined at acceptance, but no push until the payment is held: a failed checkout
    # would reopen the offer.
    assert not Delivery.objects.filter(kind="booking.offer_declined").exists()

    assert post_event(client, succeeded_event(booking)) == 200
    with django_capture_on_commit_callbacks(execute=True):
        publish(FUNDS_HELD_TOPIC, booking)

    assert deliveries(booking.helper_id, "booking.offer_accepted") == {"push", "email"}
    assert deliveries(declined.helper_id, "booking.offer_declined") == {"push"}
    # Email only: the confirmation is a durable medium, not a lock-screen message.
    assert deliveries(booking.poster_id, "booking.contract_confirmation") == {"email"}
    assert not Delivery.objects.filter(user=booking.poster, channel="push").exists()


def test_a_replayed_payment_tells_everyone_once(client: Client, gateway: FakeGateway) -> None:
    booking, _ = paid_booking(client, gateway)

    assert post_event(client, succeeded_event(booking, "evt_test_again")) == 200

    assert Outbox.objects.filter(topic=FUNDS_HELD_TOPIC, aggregate_id=str(booking.pk)).count() == 1


def test_a_helper_whose_offer_reopened_isnt_told_it_was_declined(
    client: Client, gateway: FakeGateway
) -> None:
    booking, declined = paid_booking(client, gateway)
    Offer.objects.filter(pk=declined.pk).update(status=Offer.Status.SENT)

    publish(FUNDS_HELD_TOPIC, booking)

    assert not Delivery.objects.filter(kind="booking.offer_declined").exists()


# The contract confirmation (CRD Art. 8(7)).


def send_confirmation(client: Client, gateway: FakeGateway, capture: Any, locale: str) -> Booking:
    chosen, _ = two_offers()
    poster = chosen.task.poster
    poster.locale = locale
    poster.save(update_fields=["locale"])
    booking = accept(chosen).booking
    booking.refresh_from_db()
    with capture(execute=True):
        assert post_event(client, succeeded_event(booking)) == 200
        publish(FUNDS_HELD_TOPIC, booking)
    return booking


def test_the_poster_gets_the_contract_confirmation_in_their_language(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    booking = send_confirmation(client, gateway, django_capture_on_commit_callbacks, "de")

    [email] = [m for m in mail.outbox if m.to == [booking.poster.email]]
    assert email.subject == "Ihre Buchung ist bestätigt: Ihre Vertragsdetails"
    assert email.extra_headers["Content-Language"] == "de"
    body = email.body
    assert booking.task.title in body
    assert booking.helper.first_name in body
    assert "Privatperson" in body
    # €30 task, €3.60 fee, €33.60 paid, in German formatting.
    for amount in ("30,00 €", "3,60 €", "33,60 €"):
        assert amount in body
    assert CANCELLATION in body
    assert "Widerrufsrecht" in body
    # The legal text comes from the LEGAL_DOCUMENT, in the language accepted at checkout.
    [(filename, content, mimetype)] = email.attachments
    document = LegalDocument.objects.get(kind="CANCELLATION", version=CANCELLATION, language="de")
    assert (filename, content, mimetype) == (
        f"taskbeavo-cancellation-policy-{CANCELLATION}-de.txt",
        without_markers(document.body),
        "text/plain",
    )
    # The poster keeps the policy as it reads, without the checkout markers, but with the
    # tick-box sentences they agreed to.
    assert "<!--" not in content
    assert "Start early, please (private, de)." in content
    [sent] = BookingEvent.objects.filter(booking=booking, type="contract_confirmation_sent")
    assert sent.data["cancellation_version"] == CANCELLATION
    assert sent.data["legal_documents"] == [str(document.pk)]


def test_a_poster_reading_another_language_also_gets_the_policy_they_accepted(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """The checkout showed German (test_checkout.consent); the poster reads French."""
    booking = send_confirmation(client, gateway, django_capture_on_commit_callbacks, "fr")

    [email] = [m for m in mail.outbox if m.to == [booking.poster.email]]
    assert email.extra_headers["Content-Language"] == "fr"
    assert [name for name, _, _ in email.attachments] == [
        f"taskbeavo-cancellation-policy-{CANCELLATION}-de.txt",
        f"taskbeavo-cancellation-policy-{CANCELLATION}-fr.txt",
    ]


PRIVATE_SCOPE = (
    "Your right of withdrawal covers only your service fee contract with TaskBeavo. The task "
    "is agreed with a private individual, so the task itself has no statutory right of "
    "withdrawal."
)
TRADER_SCOPE = (
    "Your right of withdrawal covers both your service fee contract with TaskBeavo and the "
    "task contract with the helper's registered business."
)
HOME_ADDRESS = "Put Republike 3"  # offers.tests.factories.make_helper


def confirmation_for(
    client: Client, capture: Any, locale: str, *, trader: bool, edit: Any = None
) -> tuple[str, HelperProfile]:
    """The poster's confirmation, in `locale`; `edit` runs between checkout and payment."""
    chosen, _ = two_offers()
    profile = make_trader(chosen) if trader else HelperProfile.objects.get(user=chosen.helper)
    poster = chosen.task.poster
    poster.locale = locale
    poster.save(update_fields=["locale"])
    booking = accept(chosen).booking
    booking.refresh_from_db()
    if edit is not None:
        edit(profile)
    with capture(execute=True):
        assert post_event(client, succeeded_event(booking)) == 200
        publish(FUNDS_HELD_TOPIC, booking)
    [email] = [m for m in mail.outbox if m.to == [poster.email]]
    return email.body, profile


def translated(locale: str, message: str, **values: str) -> str:
    with translation.override(locale):
        text = translation.gettext(message)
    if locale != "en":
        assert text != message, f"{message!r} has no {locale} translation"
    return text % values if values else text


def taskbeavo_identity(locale: str) -> str:
    return translated(
        locale,
        "Your service fee contract is with %(taskbeavo_name)s, OIB %(taskbeavo_oib)s, "
        "%(taskbeavo_address)s, %(taskbeavo_email)s.",
        taskbeavo_name=settings.TASKBEAVO_LEGAL_NAME,
        taskbeavo_oib=settings.TASKBEAVO_OIB,
        taskbeavo_address=settings.TASKBEAVO_ADDRESS,
        taskbeavo_email=settings.TASKBEAVO_CONTACT_EMAIL,
    )


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_with_a_private_helper_the_withdrawal_right_covers_only_the_fee(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any, locale: str
) -> None:
    """User decision 2026-11-04 (PLAN.md "Consumer withdrawal law"): consumer to consumer,
    the task has no statutory withdrawal right; the fee contract with TaskBeavo does."""
    body, _ = confirmation_for(client, django_capture_on_commit_callbacks, locale, trader=False)

    assert translated(locale, "Private individual") in body
    assert translated(locale, PRIVATE_SCOPE) in body
    assert translated(locale, TRADER_SCOPE) not in body
    assert taskbeavo_identity(locale) in body
    # A private helper's address never appears, nor a business identity.
    assert HOME_ADDRESS not in body
    assert "OIB" not in body.replace(f"OIB {settings.TASKBEAVO_OIB}", "")


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_with_a_trader_helper_the_withdrawal_right_covers_the_fee_and_the_task(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any, locale: str
) -> None:
    """CRD Art. 6(1)(b)-(c): the trader's full name, OIB and registered business address."""
    body, profile = confirmation_for(
        client, django_capture_on_commit_callbacks, locale, trader=True
    )

    assert translated(locale, TRADER_SCOPE) in body
    assert translated(locale, PRIVATE_SCOPE) not in body
    assert (
        translated(
            locale,
            "Registered business: %(trader_name)s, OIB %(trader_oib)s",
            trader_name="Vrtlarski obrt Horvat",
            trader_oib=profile.oib,
        )
        in body
    )
    assert (
        translated(
            locale,
            "Registered address: %(trader_address)s",
            trader_address="Obala Stjepana Radića 12, 20000 Dubrovnik, HR",
        )
        in body
    )
    assert taskbeavo_identity(locale) in body
    # Never the home address, even for a trader (user decision 2026-11-04).
    assert HOME_ADDRESS not in body


def test_the_confirmation_shows_the_trader_as_at_checkout(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Review R1-4: profile edits after checkout don't change what was confirmed."""

    def rename(profile: HelperProfile) -> None:
        HelperProfile.objects.filter(pk=profile.pk).update(
            business_name="Renamed obrt", business_address_street="Nova ulica 1"
        )

    body, profile = confirmation_for(
        client, django_capture_on_commit_callbacks, "en", trader=True, edit=rename
    )

    assert f"Registered business: Vrtlarski obrt Horvat, OIB {profile.oib}" in body
    assert "Registered address: Obala Stjepana Radića 12, 20000 Dubrovnik, HR" in body
    assert "Renamed obrt" not in body
    assert "Nova ulica" not in body
    assert "€33.60" in body


def test_no_confirmation_goes_out_without_its_legal_text(
    client: Client,
    gateway: FakeGateway,
    django_capture_on_commit_callbacks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accepted CANCELLATION version can't be found (a broken publish): the email
    fails and is retried, instead of going out without its legal text."""
    booking, _ = paid_booking(client, gateway)
    load = contract_email._booking

    def unpublished(delivery: Delivery) -> Booking:
        loaded = load(delivery)
        loaded.consent.withdrawal_notice_version = "never-published"
        return loaded

    monkeypatch.setattr(contract_email, "_booking", unpublished)

    with django_capture_on_commit_callbacks(execute=True):
        publish(FUNDS_HELD_TOPIC, booking)

    assert not [m for m in mail.outbox if m.to == [booking.poster.email]]
    assert not BookingEvent.objects.filter(
        booking=booking, type="contract_confirmation_sent"
    ).exists()


# payment_intent.payment_failed: the checkout stays open for another card (decision B).


def test_a_declined_card_keeps_the_checkout_open(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, other = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()

    assert post_event(client, failed_event(booking)) == 200

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    [attempt] = BookingEvent.objects.filter(booking=booking, type="payment_attempt_failed")
    # Stripe's code only: its message can quote what the card holder typed.
    assert attempt.data == {
        "payment_intent": booking.payment_intent_id,
        "decline_code": "insufficient_funds",
        "stripe_event": "evt_test_declined",
    }
    [row] = Outbox.objects.filter(topic=PAYMENT_ATTEMPT_FAILED_TOPIC)
    assert row.payload["decline_code"] == "insufficient_funds"
    # The offers stay as accepted: the poster is still paying.
    other.refresh_from_db()
    assert other.status == Offer.Status.DECLINED
    assert gateway.cancels == []


def test_another_card_after_a_decline_holds_the_funds(client: Client, gateway: FakeGateway) -> None:
    """Acceptance criterion: retry with another card works, on the same PaymentIntent."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    assert post_event(client, failed_event(booking)) == 200
    assert post_event(client, failed_event(booking, "evt_test_3ds_failed")) == 200

    assert post_event(client, succeeded_event(booking)) == 200

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert BookingEvent.objects.filter(booking=booking, type="payment_attempt_failed").count() == 2
    assert len(gateway.charges) == 1


def test_a_replayed_decline_is_recorded_once(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    event = failed_event(booking)

    assert post_event(client, event) == 200
    assert post_event(client, event) == 200

    assert BookingEvent.objects.filter(booking=booking, type="payment_attempt_failed").count() == 1


@pytest.mark.parametrize("paid_first", [True, False])
def test_a_decline_on_a_closed_checkout_changes_nothing(
    client: Client, gateway: FakeGateway, paid_first: bool
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    if paid_first:
        assert post_event(client, succeeded_event(booking)) == 200
    else:
        withdraw(chosen)
    booking.refresh_from_db()
    status = booking.status

    assert post_event(client, failed_event(booking)) == 200

    booking.refresh_from_db()
    assert booking.status == status
    assert not BookingEvent.objects.filter(type="payment_attempt_failed").exists()


def test_a_decline_of_another_payment_intent_changes_nothing(
    client: Client, gateway: FakeGateway
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()

    assert post_event(client, failed_event(booking, id="pi_3SomebodyElses")) == 200

    assert not BookingEvent.objects.filter(type="payment_attempt_failed").exists()


def test_an_odd_decline_code_is_kept_as_unknown(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    error = {"code": "Card declined: call Ana on +385 91 000", "message": "anything"}

    assert post_event(client, failed_event(booking, last_payment_error=error)) == 200

    [attempt] = BookingEvent.objects.filter(type="payment_attempt_failed")
    assert attempt.data["decline_code"] == "unknown"


# The 30-minute checkout.


def test_a_checkout_open_for_30_minutes_is_cancelled_and_the_task_reopens(
    gateway: FakeGateway,
) -> None:
    chosen, other = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 31)

    assert checkout.expire_abandoned_checkouts() == 1

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"booking:{booking.pk}:abandon:v1"
    ]
    assert BookingEvent.objects.get(booking=booking, type="payment_failed").data["reason"] == (
        "checkout_abandoned"
    )
    assert BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").exists()
    chosen.refresh_from_db()
    other.refresh_from_db()
    assert (chosen.status, other.status) == (Offer.Status.SENT, Offer.Status.SENT)
    # TASK_ALREADY_BOOKED is lifted: another offer can be accepted.
    assert accept(other).booking.status == S.AWAITING_PAYMENT
    # Settled: the failed-checkout sweep leaves it alone.
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 0


def test_a_checkout_younger_than_30_minutes_is_left_open(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 29)

    assert checkout.expire_abandoned_checkouts() == 0
    assert checkout.expire_checkout(str(booking.pk)) is None

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert gateway.cancels == []


def test_a_payment_confirmed_at_the_last_moment_is_kept(
    client: Client, gateway: FakeGateway
) -> None:
    """Cancelling first: Stripe refuses the cancel, and the webhook holds the funds,
    instead of the poster being charged and refunded."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    open_for(booking, 31)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="succeeded")

    assert checkout.expire_checkout(str(booking.pk)) is None

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert post_event(client, succeeded_event(booking)) == 200
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert gateway.refunds == []


def test_a_processing_payment_waits_and_is_tried_again_with_a_new_key(
    gateway: FakeGateway,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 31)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="processing")

    assert checkout.expire_checkout(str(booking.pk)) is None
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    # A SEPA debit can process for days: not polled every 5 minutes.
    assert checkout.expire_abandoned_checkouts() == 0

    gateway.cancel_error = None
    BookingEvent.objects.filter(booking=booking, type=checkout.CANCEL_DEFERRED).update(
        created_at=timezone.now() - dt.timedelta(hours=7)
    )
    assert checkout.expire_abandoned_checkouts() == 1

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"booking:{booking.pk}:abandon:v1",
        f"booking:{booking.pk}:abandon:v2",
    ]


def test_a_stripe_outage_is_retried_on_the_next_run_with_a_new_key(
    gateway: FakeGateway,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 31)
    gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=500)

    assert checkout.expire_checkout(str(booking.pk)) is None
    gateway.cancel_error = None
    # No 6-hour wait after a Stripe-side error.
    assert checkout.expire_abandoned_checkouts() == 1

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert [call["idempotency_key"] for call in gateway.cancels][-1] == (
        f"booking:{booking.pk}:abandon:v2"
    )


def test_a_cancel_stripe_refuses_for_good_alerts_support_once(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 31)
    gateway.cancel_error = StripeRequestError("test", code="resource_missing", http_status=404)

    with commit():
        assert checkout.expire_checkout(str(booking.pk)) is None

    assert BookingEvent.objects.filter(booking=booking, type=checkout.CANCEL_REFUSED).exists()
    [alert] = alerts(caplog)
    assert getattr(alert, "booking.id") == str(booking.pk)
    assert getattr(alert, "stripe.error_code") == "resource_missing"
    # Support takes over; the expiry stops trying.
    assert checkout.expire_abandoned_checkouts() == 0


def test_an_orphaned_payment_intent_is_found_and_cancelled(gateway: FakeGateway) -> None:
    """BEAV-109 "L2": acceptOffer's answer was lost after Stripe created the PaymentIntent,
    so the booking never stored its ID."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    Booking.objects.filter(pk=booking.pk).update(payment_intent_id="")
    open_for(booking, 31)
    orphan = intent_id(str(booking.pk))
    gateway.intents = [{"id": orphan, "status": "requires_payment_method"}]

    assert checkout.expire_checkout(str(booking.pk)) is not None

    assert gateway.searches == [str(booking.pk)]
    assert [call["payment_intent"] for call in gateway.cancels] == [orphan]
    booking.refresh_from_db()
    assert (booking.status, booking.payment_intent_id) == (S.PAYMENT_FAILED, orphan)


def test_a_checkout_stripe_holds_nothing_for_just_ends(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    Booking.objects.filter(pk=booking.pk).update(payment_intent_id="")
    open_for(booking, 31)

    checkout.expire_checkout(str(booking.pk))

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert gateway.cancels == []
    assert BookingEvent.objects.filter(booking=booking, type=checkout.INTENT_NOT_FOUND).exists()
    # Nothing left for the sweep to look up.
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 0
    assert gateway.searches == [str(booking.pk)]


def test_a_failed_checkout_whose_stripe_call_timed_out_is_looked_up(
    gateway: FakeGateway,
) -> None:
    """The PaymentIntent request failed on our side (a timeout) after Stripe created it:
    the checkout failed without an ID, and the sweep finds and cancels the orphan."""
    chosen, _ = two_offers()
    gateway.charge_error = StripeRequestError("test", code="", http_status=None)
    with pytest.raises(Exception):  # noqa: B017 (PAYMENT_UNAVAILABLE)
        accept(chosen)
    booking = Booking.objects.get()
    assert (booking.status, booking.payment_intent_id) == (S.PAYMENT_FAILED, "")
    orphan = intent_id(str(booking.pk))
    gateway.intents = [{"id": orphan, "status": "requires_payment_method"}]
    age(booking, 45)

    assert checkout.sweep_unsettled_checkouts() == 1

    assert [call["payment_intent"] for call in gateway.cancels] == [orphan]
    assert BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").exists()
    assert checkout.sweep_unsettled_checkouts() == 0


def test_the_expiry_runs_on_beat() -> None:
    from django.conf import settings

    entries = {entry["task"]: entry for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert entries["bookings.checkout.expire_abandoned"]["schedule"] == 5 * 60.0
    assert settings.CHECKOUT_ABANDON_AFTER_MINUTES == 30


# Failed-checkout settling: alerts instead of silence (BEAV-109 review r2).


def test_a_cancel_that_failed_on_stripes_side_doesnt_wait_six_hours(
    gateway: FakeGateway,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=502)
    with pytest.raises(StripeRequestError):
        checkout.settle_failed_checkout(str(booking.pk))
    gateway.cancel_error = None
    age(booking, 45)

    assert checkout.sweep_unsettled_checkouts() == 1

    assert BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").exists()


def test_a_cancel_stripe_refuses_with_a_4xx_isnt_retried_and_alerts(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = StripeRequestError(
        "test", code="payment_intent_unexpected_state", http_status=400
    )

    # No exception: Celery would only retry the same refusal.
    with commit():
        checkout.settle_failed_checkout(str(booking.pk))

    assert len(alerts(caplog)) == 1
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 0


def test_a_refund_stripe_refuses_with_a_4xx_alerts_support(
    client: Client, gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    booking.refresh_from_db()
    gateway.refund_error = StripeRequestError("test", code="charge_disputed", http_status=400)

    # On commit, the late payment's outbox row asks for the refund, which Stripe refuses.
    with commit():
        assert post_event(client, succeeded_event(booking)) == 200

    assert BookingEvent.objects.filter(booking=booking, type=checkout.REFUND_REFUSED).exists()
    # The late payment itself alerted when it arrived; the refusal is the second alert.
    assert [getattr(r, "support.reason") for r in alerts(caplog)] == [
        "late_payment",
        checkout.REFUND_REFUSED,
    ]
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 0


def test_a_failed_checkout_past_the_lookback_alerts_support_once(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    age(booking, 60 * 24 * 31)

    with commit():
        assert checkout.sweep_unsettled_checkouts() == 0
        assert checkout.sweep_unsettled_checkouts() == 0

    [flag] = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert flag.data["reason"] == checkout.SETTLE_GIVEN_UP
    assert len(alerts(caplog)) == 1


# payout.failed (RELEASED or PAID_OUT; the retry is BEAV-120).


def payout_failed(
    booking: Booking, event_id: str = "evt_test_payout_failed", **changes: Any
) -> dict:
    event = copy.deepcopy(load_event("payout.failed"))
    event["id"] = event_id
    event["account"] = ConnectedAccount.objects.get(
        helper__user_id=booking.helper_id
    ).stripe_account_id
    event["data"]["object"]["metadata"] = {"booking_id": str(booking.pk)}
    event.update(changes)
    return event


@pytest.mark.parametrize("status", [S.RELEASED, S.PAID_OUT])
def test_a_failed_payout_tells_the_helper_and_support(
    client: Client,
    gateway: FakeGateway,
    status: S,
    caplog: pytest.LogCaptureFixture,
    commit: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking, _ = paid_booking(client, gateway)
    Booking.objects.filter(pk=booking.pk).update(status=status)
    register_device(booking.helper, "ExponentPushToken[helper]")
    before = BookingEvent.objects.filter(booking=booking).count()

    with commit():
        assert post_event(client, payout_failed(booking)) == 200

    booking.refresh_from_db()
    # The money is the helper's either way: the status stays (never apply_stripe_transition).
    assert booking.status == status
    [failure] = BookingEvent.objects.filter(booking=booking, type=payouts.PAYOUT_FAILED)
    assert failure.data == {
        "payout": "po_1TestPayout",
        "stripe_event": "evt_test_payout_failed",
        "failure_code": "account_closed",
    }
    assert BookingEvent.objects.filter(booking=booking).count() == before + 1
    [alert] = alerts(caplog)
    assert getattr(alert, "stripe.object_id") == "po_1TestPayout"
    with django_capture_on_commit_callbacks(execute=True):
        publish(PAYOUT_FAILED_TOPIC, booking)
    assert deliveries(booking.helper_id, "payouts.failed") == {"push", "email"}


def test_the_same_payout_failure_under_another_event_id_counts_once(
    client: Client, gateway: FakeGateway
) -> None:
    booking, _ = paid_booking(client, gateway)
    Booking.objects.filter(pk=booking.pk).update(status=S.RELEASED)

    assert post_event(client, payout_failed(booking)) == 200
    assert post_event(client, payout_failed(booking, "evt_test_payout_again")) == 200

    assert BookingEvent.objects.filter(type=payouts.PAYOUT_FAILED).count() == 1
    assert Outbox.objects.filter(topic=PAYOUT_FAILED_TOPIC).count() == 1


@pytest.mark.parametrize(
    ("status", "account", "reason"),
    [
        (S.FUNDS_HELD, None, "payout_failed_before_release"),
        (S.RELEASED, "acct_1SomebodyElse", "payout_on_another_account"),
    ],
)
def test_a_payout_failure_that_doesnt_fit_is_flagged_and_acknowledged(
    client: Client, gateway: FakeGateway, status: S, account: str | None, reason: str
) -> None:
    booking, _ = paid_booking(client, gateway)
    Booking.objects.filter(pk=booking.pk).update(status=status)
    event = payout_failed(booking)
    if account:
        event["account"] = account

    assert post_event(client, event) == 200

    [flag] = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert flag.data["reason"] == reason
    assert not Outbox.objects.filter(topic=PAYOUT_FAILED_TOPIC).exists()


def test_a_payout_that_isnt_a_bookings_is_acknowledged(client: Client) -> None:
    event = copy.deepcopy(load_event("payout.failed"))
    event["data"]["object"]["metadata"] = {}

    assert post_event(client, event) == 200

    assert not BookingEvent.objects.filter(type=payouts.PAYOUT_FAILED).exists()


# Every money alert reaches Sentry the same way (user decision 2026-11-04).


@pytest.fixture
def sentry_events() -> Iterator[list[dict[str, Any]]]:
    transport = RecordingTransport()
    assert telemetry.setup_sentry(
        dsn="https://public@o1.ingest.de.sentry.io/1", transport=transport
    )
    yield transport.events
    sentry_sdk.get_client().close()
    sentry_sdk.init(auto_enabling_integrations=False, default_integrations=False)


@pytest.fixture(params=MANGLED_UUIDS)
def pinned_booking_id(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """The next booking gets a UUID the PII patterns used to match (BEAV-186)."""
    ids = iter([uuid.UUID(request.param)])
    monkeypatch.setattr(
        Booking._meta.get_field("id"), "_get_default", lambda: next(ids, None) or uuid.uuid4()
    )
    return request.param


def money_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if (e.get("tags") or {}).get("support_flag") == "money"]


def test_a_failed_payout_reaches_sentry_tagged_with_ids_only(
    client: Client,
    gateway: FakeGateway,
    sentry_events: list[dict[str, Any]],
    commit: Any,
    pinned_booking_id: str,
) -> None:
    booking, _ = paid_booking(client, gateway)
    assert str(booking.pk) == pinned_booking_id
    Booking.objects.filter(pk=booking.pk).update(status=S.PAID_OUT)

    with commit():
        assert post_event(client, payout_failed(booking)) == 200

    [event] = money_events(sentry_events)
    assert event["level"] == "error"
    assert {k: v for k, v in event["tags"].items() if k != "trace_id"} == {
        "support_flag": "money",
        "booking.id": str(booking.pk),
        "support.reason": "payout_failed",
        "stripe.event_type": "payout.failed",
        "stripe.event_id": "evt_test_payout_failed",
        "stripe.object_id": "po_1TestPayout",
        "stripe.error_code": "account_closed",
    }
    text = repr(event)
    for personal in (booking.helper.email, booking.helper.first_name, booking.task.title):
        assert personal not in text


def test_a_chargeback_after_the_payout_is_flagged_tagged_and_acknowledged(
    client: Client, gateway: FakeGateway, sentry_events: list[dict[str, Any]], commit: Any
) -> None:
    """A Stripe move that doesn't fit (TODO(BEAV-127)): flag, alert, answer 200."""
    booking, _ = paid_booking(client, gateway)
    Booking.objects.filter(pk=booking.pk).update(status=S.PAID_OUT)

    with commit(), transaction.atomic():
        event_id = stripe_event("charge.dispute.created")
        assert (
            state_machine.apply_stripe_transition(booking.pk, S.DISPUTED, stripe_event_id=event_id)
            is None
        )

    booking.refresh_from_db()
    assert booking.status == S.PAID_OUT
    [flag] = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert flag.data["reason"] == "stripe_transition_refused"
    [alert] = money_events(sentry_events)
    assert alert["tags"]["stripe.event_type"] == "charge.dispute.created"
    assert alert["tags"]["stripe.event_id"] == event_id


@pytest.mark.parametrize(
    "trigger",
    ["late_payment", "cancel_refused", "refund_refused", "stripe_unavailable", "given_up"],
)
def test_every_money_problem_carries_the_same_tag(
    client: Client,
    gateway: FakeGateway,
    sentry_events: list[dict[str, Any]],
    trigger: str,
    commit: Any,
    pinned_booking_id: str,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    assert str(booking.pk) == pinned_booking_id
    withdraw(chosen)
    booking.refresh_from_db()
    sentry_events.clear()
    if trigger == "late_payment":
        with commit():
            assert post_event(client, succeeded_event(booking)) == 200
    elif trigger == "cancel_refused":
        gateway.cancel_error = StripeRequestError("test", code="resource_missing", http_status=404)
        with commit():
            checkout.settle_failed_checkout(str(booking.pk))
    elif trigger == "refund_refused":
        assert post_event(client, succeeded_event(booking)) == 200
        gateway.refund_error = StripeRequestError("test", code="charge_disputed", http_status=400)
        with commit():
            checkout.settle_failed_checkout(str(booking.pk))
    elif trigger == "stripe_unavailable":
        gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=503)
        with commit():
            for _ in range(checkout.STRIPE_ERRORS_BEFORE_ALERT):
                with pytest.raises(StripeRequestError):
                    checkout.settle_failed_checkout(str(booking.pk))
    else:
        age(booking, 60 * 24 * 31)
        with commit():
            checkout.sweep_unsettled_checkouts()

    [event] = money_events(sentry_events)
    assert event["level"] == "error"
    assert event["tags"]["booking.id"] == pinned_booking_id
    assert event["extra"]["booking.id"] == pinned_booking_id
    assert event["tags"]["support.reason"]


def test_stripe_failing_on_and_on_alerts_once_even_past_the_threshold(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    """Overlapping retries can step past the threshold count: still exactly one alert."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=503)
    with commit():
        for _ in range(checkout.STRIPE_ERRORS_BEFORE_ALERT + 2):
            with pytest.raises(StripeRequestError):
                checkout.settle_failed_checkout(str(booking.pk))

    assert [getattr(r, "support.reason") for r in alerts(caplog)] == ["stripe_unavailable"]


def test_an_abandoned_checkout_paid_at_stripe_but_never_held_alerts_once(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    """The payment_intent.succeeded webhook keeps failing: the expiry doesn't loop quietly."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    open_for(booking, 31)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="succeeded")

    with commit():
        for _ in range(4):
            checkout.expire_checkout(str(booking.pk))

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert [getattr(r, "support.reason") for r in alerts(caplog)] == ["abandoned_checkout_paid"]


def test_several_payment_intents_for_one_booking_alert_once(
    gateway: FakeGateway, caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    Booking.objects.filter(pk=booking.pk).update(payment_intent_id="")
    open_for(booking, 31)
    gateway.intents = [
        {"id": "pi_3First", "status": "requires_payment_method"},
        {"id": "pi_3Second", "status": "requires_payment_method"},
    ]
    gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=503)

    with commit():
        checkout.expire_checkout(str(booking.pk))
        checkout.expire_checkout(str(booking.pk))

    assert [getattr(r, "support.reason") for r in alerts(caplog)].count(
        "several_payment_intents"
    ) == 1


# Alerts go out once their transaction commits (review R1-3).


def test_an_alert_waits_for_its_transaction_to_commit(
    caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    with commit(), transaction.atomic():
        money_alert("bookings: test alert", booking_id="b1", reason="test")
        assert alerts(caplog) == []

    assert [getattr(r, "support.reason") for r in alerts(caplog)] == ["test"]


def test_no_alert_fires_when_its_transaction_rolls_back(
    caplog: pytest.LogCaptureFixture, commit: Any
) -> None:
    with commit(), contextlib.suppress(RuntimeError), transaction.atomic():
        money_alert("bookings: test alert", booking_id="b1", reason="test")
        raise RuntimeError("rolled back")

    assert alerts(caplog) == []


# A paid PaymentIntent that doesn't match its booking (review R1-2).


@pytest.mark.parametrize(
    ("changes", "mismatch"),
    [
        ({"amount": 100}, "amount"),
        ({"currency": "usd"}, "amount"),
        ({"id": "pi_test_someone_elses"}, "payment_intent"),
        ({"transfer_data": {"destination": "acct_test_other"}}, "destination"),
        ({"on_behalf_of": "acct_test_other"}, "destination"),
    ],
)
def test_a_payment_that_doesnt_match_is_a_tagged_support_flag(
    client: Client,
    gateway: FakeGateway,
    sentry_events: list[dict[str, Any]],
    commit: Any,
    changes: dict[str, Any],
    mismatch: str,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    sentry_events.clear()
    event = succeeded_event(booking, **changes)

    with commit():
        assert post_event(client, event) == 200

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    [flag] = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert (flag.data["reason"], flag.data["mismatch"]) == (checkout.PAYMENT_MISMATCH, mismatch)
    [sent] = money_events(sentry_events)
    assert sent["tags"]["support.reason"] == checkout.PAYMENT_MISMATCH
    assert sent["tags"]["stripe.event_type"] == "payment_intent.succeeded"
    assert sent["tags"]["stripe.object_id"] == event["data"]["object"]["id"]
    assert not Outbox.objects.filter(topic=FUNDS_HELD_TOPIC).exists()


def test_a_mismatched_payment_on_a_failed_checkout_is_flagged_not_refunded(
    client: Client, gateway: FakeGateway, sentry_events: list[dict[str, Any]], commit: Any
) -> None:
    """The checkout expired; a payment that isn't the booking's own isn't refunded blindly."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    open_for(booking, 31)
    assert checkout.expire_checkout(str(booking.pk)) is not None
    sentry_events.clear()

    with commit():
        assert post_event(client, succeeded_event(booking, amount=100)) == 200

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert gateway.refunds == []
    assert [e["tags"]["support.reason"] for e in money_events(sentry_events)] == [
        checkout.PAYMENT_MISMATCH
    ]


# The checkout clock: 30 minutes from the last card attempt, 60 at most (decision A).


def action_required_event(booking: Booking, event_id: str = "evt_test_3ds") -> dict:
    event = copy.deepcopy(load_event("payment_intent.requires_action"))
    event["id"] = event_id
    event["data"]["object"].update(
        {"id": booking.payment_intent_id, "metadata": {"booking_id": str(booking.pk)}}
    )
    return event


def attempt_at(booking: Booking, minutes_ago: int) -> None:
    """The checkout's card attempts happened `minutes_ago`."""
    BookingEvent.objects.filter(booking=booking, type__in=checkout.ATTEMPTS).update(
        created_at=timezone.now() - dt.timedelta(minutes=minutes_ago)
    )


@pytest.mark.parametrize("attempt", ["decline", "3ds"])
def test_a_card_attempt_at_minute_29_keeps_the_checkout_open(
    client: Client, gateway: FakeGateway, attempt: str
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    event = failed_event(booking) if attempt == "decline" else action_required_event(booking)
    assert post_event(client, event) == 200
    open_for(booking, 40)
    # The attempt was at minute 29 of the checkout, 11 minutes ago.
    attempt_at(booking, 11)

    assert checkout.expire_abandoned_checkouts() == 0
    assert checkout.expire_checkout(str(booking.pk)) is None

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert gateway.cancels == []
    # 30 minutes after that attempt, it ends.
    attempt_at(booking, 31)
    assert checkout.expire_abandoned_checkouts() == 1
    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED


@pytest.mark.parametrize("attempt", ["none", "decline", "3ds"])
def test_the_60_minute_cap_ends_every_checkout(
    client: Client, gateway: FakeGateway, attempt: str
) -> None:
    chosen, other = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    if attempt != "none":
        event = failed_event(booking) if attempt == "decline" else action_required_event(booking)
        assert post_event(client, event) == 200
        attempt_at(booking, 1)
    open_for(booking, 61)

    assert checkout.expire_abandoned_checkouts() == 1

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"booking:{booking.pk}:abandon:v1"
    ]
    other.refresh_from_db()
    assert other.status == Offer.Status.SENT


def test_a_3ds_start_is_recorded_once_and_tells_the_poster(
    client: Client, gateway: FakeGateway
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    event = action_required_event(booking)

    assert post_event(client, event) == 200
    assert post_event(client, event) == 200

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    [started] = BookingEvent.objects.filter(booking=booking, type=checkout.PAYMENT_ACTION_REQUIRED)
    assert started.data == {
        "payment_intent": booking.payment_intent_id,
        "stripe_event": "evt_test_3ds",
    }
    assert Outbox.objects.filter(topic=checkout.ACTION_REQUIRED_TOPIC).count() == 1


def test_a_3ds_start_on_a_closed_checkout_changes_nothing(
    client: Client, gateway: FakeGateway
) -> None:
    booking, _ = paid_booking(client, gateway)

    assert post_event(client, action_required_event(booking)) == 200

    assert not BookingEvent.objects.filter(type=checkout.PAYMENT_ACTION_REQUIRED).exists()


# A payment that succeeds after the expiry (review R1-7).


@pytest.mark.parametrize("minutes", [31, 61])
def test_a_payment_after_the_expiry_is_refunded_and_never_held(
    client: Client,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    commit: Any,
    minutes: int,
) -> None:
    chosen, other = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    open_for(booking, minutes)
    assert checkout.expire_checkout(str(booking.pk)) is not None
    # Another offer is accepted on the reopened task meanwhile.
    second = accept(other).booking

    with commit():
        assert post_event(client, succeeded_event(booking)) == 200
    publish(LATE_PAYMENT_TOPIC, booking)

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert not BookingEvent.objects.filter(booking=booking, type="funds_held").exists()
    assert not Outbox.objects.filter(topic=FUNDS_HELD_TOPIC).exists()
    assert [refund["idempotency_key"] for refund in gateway.refunds] == [
        f"booking:{booking.pk}:refund:withdrawn:v1"
    ]
    assert [getattr(r, "support.reason") for r in alerts(caplog)] == ["late_payment"]
    second.refresh_from_db()
    assert second.status == S.AWAITING_PAYMENT
