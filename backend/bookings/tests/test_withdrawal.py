"""A helper withdraws the accepted offer while the poster is in checkout (PLAN.md, "A
helper can withdraw an offer"; review r2: R2-1): the checkout fails and the declined
offers reopen. Once the payment is held, withdrawing is refused (helper cancel, BEAV-123).
A payment confirmed in the same moment is refunded (BEAV-109)."""

import threading
import time
from collections.abc import Callable

import pytest
from django.db import connection, transaction

from accounts.models import User
from bookings import state_machine
from bookings.models import Booking, BookingEvent
from bookings.tests.factories import book, make_booking, stripe_event
from bookings.tests.fakes import FakeGateway
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from offers import services as offers
from offers.models import Offer
from offers.outbox_handlers import WITHDRAWN_TOPIC
from offers.tests.factories import make_helper, open_task
from payments import webhooks
from payments.models import ConnectedAccount, StripeEvent
from payments.stripe_client import StripeRequestError

pytestmark = pytest.mark.django_db

S = Booking.Status


def in_checkout() -> tuple[Booking, Offer, Offer]:
    """An AWAITING_PAYMENT booking, its accepted offer and one offer it declined."""
    task = open_task()
    chosen = offers.send_offer(make_helper("marko"), str(task.pk), 3_000)
    other = offers.send_offer(make_helper("ivan"), str(task.pk), 2_800)
    return book(task.poster, chosen), chosen, other


def withdraw(booking: Booking) -> Offer:
    helper = User.objects.get(pk=booking.helper_id)
    return offers.withdraw_offer(helper, str(booking.offer_id))


def test_withdrawing_during_checkout_ends_the_checkout() -> None:
    booking, chosen, other = in_checkout()

    withdraw(booking)

    booking.refresh_from_db()
    chosen.refresh_from_db()
    other.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert chosen.status == Offer.Status.WITHDRAWN
    assert chosen.closed_at is not None
    assert other.status == Offer.Status.SENT
    [event] = BookingEvent.objects.filter(booking=booking, type="payment_failed")
    assert event.actor_kind == ActorKind.SYSTEM
    assert event.actor_id is None
    assert (
        Outbox.objects.filter(
            topic=state_machine.UPDATED_TOPIC, aggregate_id=str(booking.pk)
        ).count()
        == 2
    )
    assert Outbox.objects.filter(
        topic=state_machine.OFFER_REOPENED_TOPIC, aggregate_id=str(other.pk)
    ).exists()
    assert not Outbox.objects.filter(
        topic=state_machine.OFFER_REOPENED_TOPIC, aggregate_id=str(chosen.pk)
    ).exists()
    [row] = Outbox.objects.filter(topic=WITHDRAWN_TOPIC, aggregate_id=str(chosen.pk))
    assert row.payload == {
        "offer_id": str(chosen.pk),
        "task_id": str(booking.task_id),
        "booking_id": str(booking.pk),
    }


def test_the_poster_can_book_another_offer_afterwards() -> None:
    booking, _, other = in_checkout()
    withdraw(booking)

    again = book(booking.poster, other)

    assert again.status == S.AWAITING_PAYMENT


def test_a_withdrawal_before_acceptance_has_no_booking() -> None:
    task = open_task()
    helper = make_helper()
    offer = offers.send_offer(helper, str(task.pk), 3_000)

    offers.withdraw_offer(helper, str(offer.pk))

    [row] = Outbox.objects.filter(topic=WITHDRAWN_TOPIC, aggregate_id=str(offer.pk))
    assert "booking_id" not in row.payload
    assert not Booking.objects.exists()


@pytest.mark.parametrize("status", [s for s in S if s not in (S.AWAITING_PAYMENT,)])
def test_withdrawing_is_refused_once_the_checkout_is_over(status: S) -> None:
    booking = make_booking(status)

    with pytest.raises(ApiError) as exc:
        withdraw(booking)

    assert exc.value.extensions["code"] == ErrorCode.OFFER_NOT_EDITABLE.name
    booking.refresh_from_db()
    assert booking.status == status
    assert not Outbox.objects.filter(topic=WITHDRAWN_TOPIC).exists()


def test_another_helper_cant_withdraw_it() -> None:
    booking, _, _ = in_checkout()

    with pytest.raises(ApiError) as exc:
        offers.withdraw_offer(make_helper("luka"), str(booking.offer_id))

    assert exc.value.extensions["code"] == ErrorCode.OFFER_NOT_FOUND.name
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def wait_until_a_connection_waits_on_a_lock() -> None:
    """Block until another connection is waiting for a row lock, so the test proves the
    order instead of hoping for it."""
    deadline = time.monotonic() + 10
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
            row = cursor.fetchone()
            if row is not None and row[0] > 0:
                return
            time.sleep(0.02)
    raise AssertionError("Nothing waited on the lock.")


def hold_open(work: Callable[[], object], locked: threading.Event, release: threading.Event):
    """A thread that runs `work` in a transaction and keeps it open until `release`."""

    def run() -> None:
        try:
            with transaction.atomic():
                work()
                locked.set()
                assert release.wait(10)
        finally:
            connection.close()

    return threading.Thread(target=run)


def in_thread(action: Callable[[], object], results: list[str]) -> threading.Thread:
    def run() -> None:
        try:
            action()
            results.append("ok")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    return threading.Thread(target=run)


@pytest.mark.django_db(transaction=True)
def test_withdrawing_while_the_payment_is_confirmed(gateway: FakeGateway) -> None:
    """The webhook confirming the payment holds the booking lock when the helper
    withdraws: the withdrawal waits on that lock, then sees FUNDS_HELD and is refused."""
    booking, chosen, other = in_checkout()
    locked, release = threading.Event(), threading.Event()
    results: list[str] = []

    webhook = hold_open(
        lambda: state_machine.apply_stripe_transition(
            booking.pk, S.FUNDS_HELD, stripe_event_id=stripe_event()
        ),
        locked,
        release,
    )
    webhook.start()
    assert locked.wait(10)
    withdrawal = in_thread(lambda: withdraw(booking), results)
    withdrawal.start()
    wait_until_a_connection_waits_on_a_lock()
    release.set()
    webhook.join()
    withdrawal.join()

    assert results == [ErrorCode.OFFER_NOT_EDITABLE.name]
    booking.refresh_from_db()
    chosen.refresh_from_db()
    other.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert (chosen.status, other.status) == (Offer.Status.ACCEPTED, Offer.Status.DECLINED)
    assert not Outbox.objects.filter(topic=WITHDRAWN_TOPIC).exists()
    assert gateway.cancels == []


@pytest.mark.django_db(transaction=True)
def test_a_payment_confirmed_while_the_offer_is_withdrawn_is_refunded(
    gateway: FakeGateway,
) -> None:
    """The other order: the withdrawal holds the lock, payment_intent.succeeded waits on
    it, then finds the checkout failed. The webhook succeeds (no retry storm) and the
    payment is refunded in full with the shared key, once."""
    booking, chosen, _ = in_checkout()
    Booking.objects.filter(pk=booking.pk).update(payment_intent_id="pi_testRace")
    account = ConnectedAccount.objects.get(helper__user_id=booking.helper_id).stripe_account_id
    gateway.cancel_error = StripeRequestError(
        "test", code="payment_intent_unexpected_state", payment_intent_status="succeeded"
    )
    event = {
        "id": "evt_test_race",
        "object": "event",
        "type": "payment_intent.succeeded",
        "livemode": False,
        "data": {
            "object": {
                "id": "pi_testRace",
                "object": "payment_intent",
                "amount": booking.amount_cents + booking.fee_cents,
                "currency": "eur",
                "metadata": {"booking_id": str(booking.pk)},
                "transfer_data": {"destination": account},
                "on_behalf_of": account,
            }
        },
    }
    locked, release = threading.Event(), threading.Event()
    results: list[str] = []

    withdrawal = hold_open(lambda: withdraw(booking), locked, release)
    withdrawal.start()
    assert locked.wait(10)
    webhook = in_thread(lambda: webhooks.process_event(event), results)
    webhook.start()
    wait_until_a_connection_waits_on_a_lock()
    release.set()
    withdrawal.join()
    webhook.join()

    assert results == ["ok"]
    booking.refresh_from_db()
    chosen.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert chosen.status == Offer.Status.WITHDRAWN
    assert StripeEvent.objects.get(pk="evt_test_race").processed_at is not None
    assert BookingEvent.objects.filter(booking=booking, type="late_payment").count() == 1
    assert {refund["idempotency_key"] for refund in gateway.refunds} == {
        f"booking:{booking.pk}:refund:withdrawn:v1"
    }
    assert booking.late_payment_refund_id
    assert BookingEvent.objects.filter(booking=booking, type="late_payment_refunded").count() == 1
