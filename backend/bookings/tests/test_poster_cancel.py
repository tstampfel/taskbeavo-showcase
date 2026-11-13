"""The poster's cancel, the withdrawal button and the settlement at Stripe (BEAV-122; hard
rules a, b, c and g): every policy row through the real booking, the three Stripe steps
with explicit amounts and their own keys, the R3-1 lookup before a resend, and the
webhook as the only thing that confirms the refund."""

import copy
import datetime as dt
import logging
import threading
from typing import Any

import pytest
from django.core import mail
from django.db import connection, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import sign_in
from accounts.tests.test_schema import error_codes, gql
from bookings import cancel, contract_email, settlement, state_machine, tasks, withdrawal_email
from bookings.cancel import PosterCancelReason
from bookings.checkout import FUNDS_HELD_TOPIC
from bookings.models import Booking, BookingEvent, CancellationSettlement, WithdrawalStatement
from bookings.tests.fakes import FakeGateway, FakeSettlementStripe
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    accept,
    legal_documents,
    make_trader,
    post_event,
    publish,
    succeeded_event,
    two_offers,
    webhook_secret,
)
from core.errors import ApiError, ErrorCode
from core.i18n import SUPPORTED_LOCALES
from core.models import ActorKind, Outbox
from core.resend import ResendError
from legal import withdrawal_confirmation
from legal.models import LegalDocument
from legal.tests.factories import cancellation_body, publish_version
from notifications import delivery as notification_delivery
from notifications.models import Channel, Delivery
from payments.stripe_client import StripeRequestError
from payments.tests.stripe_fakes import load_event
from staff.roles import Role
from staff.tests.factories import make_staff
from tasks.models import Task

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)
REASON = PosterCancelReason.NO_LONGER_NEEDED
WORDED = "2026-11-worded"


def paid(
    client: Client, *, trader: bool = False, helpers: tuple[str, str] = ("marko", "ivan")
) -> Booking:
    chosen, _ = two_offers(helpers)
    if trader:
        make_trader(chosen)
    booking = accept(chosen).booking
    booking.refresh_from_db()
    assert post_event(client, succeeded_event(booking, f"evt_test_{booking.pk.hex}")) == 200
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    return booking


def starting(booking: Booking, after_acceptance: dt.timedelta) -> Booking:
    """The job starts `after_acceptance` after the offer was accepted."""
    Booking.objects.filter(pk=booking.pk).update(slot_start=booking.created_at + after_acceptance)
    booking.refresh_from_db()
    return booking


def poster(booking: Booking) -> User:
    return User.objects.get(pk=booking.poster_id)


def helper(booking: Booking) -> User:
    return User.objects.get(pk=booking.helper_id)


def code(exc: pytest.ExceptionInfo[ApiError]) -> str:
    return exc.value.extensions["code"]


def refunded_event(booking: Booking, amount: int, event_id: str = "evt_test_refunded") -> dict:
    event = copy.deepcopy(load_event("charge.refunded"))
    event["id"] = event_id
    event["data"]["object"].update(
        {
            "amount": booking.amount_cents + booking.fee_cents,
            "amount_refunded": amount,
            "payment_intent": booking.payment_intent_id,
            "refunded": amount == booking.amount_cents + booking.fee_cents,
        }
    )
    return event


def amounts(booking: Booking) -> tuple[int, int, int, int, int]:
    s = CancellationSettlement.objects.get(booking=booking)
    return (
        s.refund_cents,
        s.helper_compensation_cents,
        s.kept_fee_cents,
        s.transfer_reversal_cents,
        s.fee_refund_cents,
    )


@pytest.fixture
def stripe() -> FakeSettlementStripe:
    return FakeSettlementStripe()


# --- Which policy row applies, through the real booking. ---


def test_more_than_24_h_ahead_is_a_full_refund(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client), 72 * H)

    cancelled = cancel.cancel_booking(
        poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H
    )

    assert cancelled.status == S.CANCEL_REQUESTED
    assert cancelled.cancel_reason == "NO_LONGER_NEEDED"
    assert cancelled.cancel_requested_by == ActorKind.POSTER
    assert amounts(booking) == (3_360, 0, 0, 3_360, 360)
    assert cancelled.refund_cents == 3_360 and cancelled.helper_compensation_cents == 0
    [event] = BookingEvent.objects.filter(booking=booking, type="cancel_requested")
    assert event.actor_kind == ActorKind.POSTER
    assert event.data["case"] == "EARLY"
    topics = set(
        Outbox.objects.filter(aggregate_id=str(booking.pk)).values_list("topic", flat=True)
    )
    assert {state_machine.UPDATED_TOPIC, settlement.OPENED_TOPIC, cancel.CANCELLED_TOPIC} <= topics


def test_within_1_h_of_accepting_is_a_full_refund_even_late(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 5 * H)

    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + H)

    assert CancellationSettlement.objects.get(booking=booking).case == "GRACE_PERIOD"
    assert amounts(booking)[0] == 3_360


def test_a_late_cancel_pays_the_helper_25_percent(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)

    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)

    assert CancellationSettlement.objects.get(booking=booking).case == "LATE"
    # The €30 worked example: €25.20 back, €7.50 for the helper, €0.90 kept.
    assert amounts(booking) == (2_520, 750, 90, 2_520, 270)


def test_a_late_cancel_of_a_trader_inside_14_days_is_a_full_refund(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client, trader=True), 20 * H)

    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)

    assert CancellationSettlement.objects.get(booking=booking).case == "LATE_TRADER_WITHDRAWAL"
    assert amounts(booking)[0] == 3_360


def test_at_or_after_the_start_the_cancel_is_refused(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client, trader=True), 5 * H)

    for now in (booking.slot_start, booking.slot_start + H):
        with pytest.raises(ApiError) as exc:
            cancel.cancel_booking(poster(booking), booking.pk, REASON, now=now)
        assert code(exc) == ErrorCode.WORK_STARTED.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def test_after_a_payment_request_it_is_not_a_cancellation(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    Booking.objects.filter(pk=booking.pk).update(status=S.PAYMENT_REQUESTED)

    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


@pytest.mark.parametrize("status", [S.AWAITING_PAYMENT, S.DISPUTED, S.REFUNDED, S.CANCEL_REQUESTED])
def test_only_a_held_booking_can_be_cancelled(
    client: Client, gateway: FakeGateway, status: Booking.Status
) -> None:
    booking = starting(paid(client), 72 * H)
    Booking.objects.filter(pk=booking.pk).update(status=status)

    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_only_the_poster_can_cancel(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)

    for user in (helper(booking), make_staff()):
        with pytest.raises(ApiError) as exc:
            cancel.cancel_booking(user, booking.pk, REASON)
        assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value
    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(poster(booking), "not-a-uuid", REASON)
    assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value


def test_a_second_cancel_is_refused(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    cancel.cancel_booking(poster(booking), booking.pk, REASON)

    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(poster(booking), booking.pk, REASON)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    assert CancellationSettlement.objects.filter(booking=booking).count() == 1


def test_an_open_cancel_cant_be_taken_back_or_disputed(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    cancel.cancel_booking(poster(booking), booking.pk, REASON)

    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.FUNDS_HELD, actor_kind=ActorKind.POSTER, actor=poster(booking)
        )
    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.DISPUTED, actor_kind=ActorKind.HELPER, actor=helper(booking)
        )
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED


# --- The settlement at Stripe. ---


def late_cancelled(client: Client) -> Booking:
    booking = starting(paid(client), 20 * H)
    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)
    booking.refresh_from_db()
    return booking


def test_the_steps_run_fee_refund_reversal_refund_with_their_own_keys(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = late_cancelled(client)

    done = settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.sent() == ["fee_refund", "transfer_reversal", "refund"]
    pk = booking.pk
    assert [kwargs["idempotency_key"] for _, kwargs in stripe.calls] == [
        f"booking:{pk}:cancel:fee_refund:v1",
        f"booking:{pk}:cancel:transfer_reversal:v1",
        f"booking:{pk}:cancel:refund:v1",
    ]
    split = stripe.calls[0][1]["split"]
    assert (split.fee_refund_cents, split.transfer_reversal_cents, split.refund_cents) == (
        270,
        2_520,
        2_520,
    )
    # Helper ends with C, TaskBeavo with K, the poster got R.
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (750, 90, 2_520)
    assert done is not None
    assert (done.fee_refund_id, done.transfer_reversal_id, done.refund_id) == (
        "fr_test1",
        "trr_test1",
        "re_test1",
    )
    assert BookingEvent.objects.filter(booking=booking, type=settlement.STEP_DONE).count() == 3
    # The steps alone confirm nothing: only the webhook does (hard rule a).
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED


def test_a_200_euro_late_cancel_never_takes_more_than_the_helper_holds(
    client: Client, gateway: FakeGateway
) -> None:
    """R2-2: with the reversal first, the €200 case would ask the helper for 20160 of 20000."""
    booking = starting(paid(client), 20 * H)
    Booking.objects.filter(pk=booking.pk).update(amount_cents=20_000, fee_cents=2_400)
    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)
    assert amounts(booking) == (20_160, 2_000, 240, 20_160, 2_160)
    stripe = FakeSettlementStripe(amount_cents=20_000, fee_cents=2_400)

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (2_000, 240, 20_160)


def test_running_again_sends_nothing(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = late_cancelled(client)
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert len(stripe.calls) == 3


def test_a_reversal_that_went_through_but_answered_500_is_looked_up_not_resent(
    client: Client, gateway: FakeGateway
) -> None:
    """R3-1 on the smallest task, €10: the reversal succeeds at Stripe but the answer is a
    500. The retry bumps the key, looks the reversal up first, records it and sends no
    second reversal."""
    booking = starting(paid(client), 20 * H)
    Booking.objects.filter(pk=booking.pk).update(amount_cents=1_000, fee_cents=250)
    booking.refresh_from_db()
    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=booking.created_at + 2 * H)
    assert amounts(booking) == (690, 500, 60, 690, 190)
    stripe = FakeSettlementStripe(
        amount_cents=1_000,
        fee_cents=250,
        fail="transfer_reversal",
        fail_with=StripeRequestError("test", code="api_error", http_status=500),
        fail_after_work=True,
    )

    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    s = CancellationSettlement.objects.get(booking=booking)
    assert s.transfer_reversal_key_version == 2 and not s.transfer_reversal_id

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.sent("transfer_reversal") == ["transfer_reversal"]
    assert "transfer_reversal" in stripe.lookups
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (500, 60, 690)
    s.refresh_from_db()
    assert s.transfer_reversal_id == "trr_test1" and s.refund_id == "re_test1"
    assert BookingEvent.objects.get(
        booking=booking, type=settlement.STEP_DONE, data__step="transfer_reversal"
    ).data["looked_up"]


def test_a_step_that_failed_before_stripe_did_it_is_sent_again_under_the_new_key(
    client: Client, gateway: FakeGateway
) -> None:
    booking = late_cancelled(client)
    stripe = FakeSettlementStripe(
        fail="refund", fail_with=StripeRequestError("test", code="api_error", http_status=503)
    )

    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    pk = booking.pk
    assert stripe.keys("refund") == [
        f"booking:{pk}:cancel:refund:v1",
        f"booking:{pk}:cancel:refund:v2",
    ]
    # Steps 1 and 2 were not sent again.
    assert stripe.sent() == ["fee_refund", "transfer_reversal", "refund", "refund"]
    assert stripe.refunded == 2_520


def test_a_network_error_retries_after_a_lookup(client: Client, gateway: FakeGateway) -> None:
    booking = late_cancelled(client)
    stripe = FakeSettlementStripe(
        fail="fee_refund",
        fail_with=StripeRequestError("test"),
        fail_after_work=True,
    )

    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.sent("fee_refund") == ["fee_refund"]
    assert stripe.lookups[0] == "fee_refund"
    assert (stripe.helper, stripe.taskbeavo) == (750, 90)


def test_a_step_stripe_refuses_for_good_goes_to_support(
    client: Client,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking = late_cancelled(client)
    stripe = FakeSettlementStripe(
        fail="transfer_reversal",
        fail_with=StripeRequestError("test", code="insufficient_funds", http_status=400),
    )

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    s = CancellationSettlement.objects.get(booking=booking)
    assert s.refused_at is not None and s.refused_step == "transfer_reversal"
    assert s.fee_refund_id == "fr_test1" and not s.refund_id
    assert BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG).exists()
    assert any(r.name == "bookings.support" for r in caplog.records)
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    assert stripe.sent() == ["fee_refund", "transfer_reversal"]


@pytest.mark.parametrize("status", [S.FUNDS_HELD, S.DISPUTED, S.PAYMENT_REQUESTED, S.RELEASED])
def test_the_settlement_refuses_to_run_unless_the_money_is_held_for_it(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe, status: Booking.Status
) -> None:
    booking = late_cancelled(client)
    Booking.objects.filter(pk=booking.pk).update(status=status)

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.calls == []
    assert BookingEvent.objects.filter(booking=booking, type=settlement.NOT_SETTLEABLE).exists()


def test_the_sweep_finds_settlements_whose_task_gave_up(
    client: Client, gateway: FakeGateway
) -> None:
    booking = late_cancelled(client)
    s = CancellationSettlement.objects.get(booking=booking)

    assert settlement.unsettled(now=s.created_at + 5 * dt.timedelta(minutes=1)) == []
    assert settlement.unsettled(now=s.created_at + H) == [booking.pk]


def test_the_outbox_queues_the_settlement(
    client: Client,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    queued: list[str] = []
    monkeypatch.setattr("bookings.tasks.settle_cancellation_task.delay", queued.append)
    booking = late_cancelled(client)

    with django_capture_on_commit_callbacks(execute=True):
        publish(settlement.OPENED_TOPIC, booking)

    assert queued == [str(booking.pk)]


# --- The webhook confirms the refund. ---


def settled(client: Client, booking: Booking, stripe: FakeSettlementStripe) -> None:
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]


def test_charge_refunded_settles_a_late_cancel_partially(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = late_cancelled(client)
    settled(client, booking, stripe)

    assert post_event(client, refunded_event(booking, 2_520)) == 200

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    s = CancellationSettlement.objects.get(booking=booking)
    assert s.confirmed_at is not None
    [confirmed] = BookingEvent.objects.filter(booking=booking, type=settlement.CONFIRMED)
    assert confirmed.data["helper_compensation_cents"] == 750
    assert (
        Outbox.objects.filter(topic=settlement.SETTLED_TOPIC, aggregate_id=str(booking.pk)).count()
        == 1
    )
    # Replayed under another ID: nothing more.
    assert post_event(client, refunded_event(booking, 2_520, "evt_test_again")) == 200
    assert (
        Outbox.objects.filter(topic=settlement.SETTLED_TOPIC, aggregate_id=str(booking.pk)).count()
        == 1
    )


def test_charge_refunded_settles_a_full_refund_as_refunded(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client), 72 * H)
    cancel.cancel_booking(poster(booking), booking.pk, REASON)
    booking.refresh_from_db()
    settled(client, booking, stripe)

    assert post_event(client, refunded_event(booking, 3_360)) == 200

    booking.refresh_from_db()
    assert booking.status == S.REFUNDED
    assert not booking.is_active


def test_a_smaller_refund_waits_and_a_bigger_one_goes_to_support(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = late_cancelled(client)

    assert post_event(client, refunded_event(booking, 1_000, "evt_test_small")) == 200
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED
    assert post_event(client, refunded_event(booking, 3_000, "evt_test_big")) == 200
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED
    assert BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason="settlement_refund_mismatch"
    ).exists()


def test_the_poster_is_told_once_the_refund_is_confirmed(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = late_cancelled(client)
    settled(client, booking, stripe)
    post_event(client, refunded_event(booking, 2_520))

    publish(settlement.SETTLED_TOPIC, booking)
    publish(cancel.CANCELLED_TOPIC, booking)

    assert Delivery.objects.filter(
        user_id=booking.poster_id, kind="booking.cancellation_refunded"
    ).exists()
    assert Delivery.objects.filter(user_id=booking.helper_id, kind="booking.cancelled").exists()


# --- The withdrawal button. ---


def test_a_withdrawal_before_the_start_is_a_full_refund(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client, trader=True), 20 * H)

    withdrawn = cancel.withdraw_from_contract(
        poster(booking), booking.pk, now=booking.created_at + 2 * H
    )

    assert withdrawn.status == S.CANCEL_REQUESTED
    assert withdrawn.cancel_reason == "WITHDRAWAL"
    assert CancellationSettlement.objects.get(booking=booking).case == "WITHDRAWAL"
    assert amounts(booking)[0] == 3_360
    [statement] = BookingEvent.objects.filter(booking=booking, type=cancel.STATEMENT)
    assert statement.created_at == booking.created_at + 2 * H
    assert statement.data["work_started"] is False
    assert Outbox.objects.filter(
        topic=cancel.WITHDRAWAL_RECEIVED_TOPIC, aggregate_id=str(booking.pk)
    ).exists()


def test_after_the_start_the_withdrawal_works_while_the_cancel_is_refused(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client, trader=True), 5 * H)
    now = booking.slot_start + H

    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(poster(booking), booking.pk, REASON, now=now)
    assert code(exc) == ErrorCode.WORK_STARTED.value
    assert cancel.withdrawal_available(booking, now)

    withdrawn = cancel.withdraw_from_contract(poster(booking), booking.pk, now=now)

    assert withdrawn.status == S.CANCEL_REQUESTED
    # Support works out the share: no money moves yet.
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert cancel.awaiting_share(withdrawn)
    assert BookingEvent.objects.get(booking=booking, type=cancel.STATEMENT).data["work_started"]
    # The poster can't take it back, the helper can't dispute it from the app.
    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.FUNDS_HELD, actor_kind=ActorKind.POSTER, actor=poster(booking)
        )
    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.DISPUTED, actor_kind=ActorKind.HELPER, actor=helper(booking)
        )


def test_support_enters_the_share_and_the_split_comes_from_cancellation(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client, trader=True), 5 * H)
    cancel.withdraw_from_contract(poster(booking), booking.pk, now=booking.slot_start + H)

    with pytest.raises(ApiError):
        cancel.settle_withdrawal(make_staff(Role.TRUST), booking.pk, 50)
    cancel.settle_withdrawal(make_staff(Role.SUPPORT), booking.pk, 50)

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.case, s.work_done_percent) == ("WITHDRAWAL_PRO_RATA", 50)
    assert amounts(booking) == (1_680, 1_500, 180, 1_680, 180)
    assert (
        BookingEvent.objects.get(booking=booking, type=cancel.SHARE_ENTERED).actor_kind
        == ActorKind.ADMIN
    )
    with pytest.raises(ApiError):
        cancel.settle_withdrawal(make_staff(Role.FINANCE), booking.pk, 10)
    booking.refresh_from_db()
    settled(client, booking, stripe)
    assert post_event(client, refunded_event(booking, 1_680)) == 200
    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED


def test_a_private_helper_booking_has_no_withdrawal_button(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)

    assert not cancel.withdrawal_available(booking)
    with pytest.raises(ApiError) as exc:
        cancel.withdraw_from_contract(poster(booking), booking.pk)
    assert code(exc) == ErrorCode.WITHDRAWAL_NOT_AVAILABLE.value


def test_the_withdrawal_ends_with_the_14_days_and_a_payment_request(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client, trader=True), 30 * 24 * H)
    later = booking.created_at + 16 * 24 * H

    assert not cancel.withdrawal_available(booking, later)
    with pytest.raises(ApiError) as exc:
        cancel.withdraw_from_contract(poster(booking), booking.pk, now=later)
    assert code(exc) == ErrorCode.WITHDRAWAL_NOT_AVAILABLE.value

    Booking.objects.filter(pk=booking.pk).update(status=S.PAYMENT_REQUESTED)
    with pytest.raises(ApiError) as exc:
        cancel.withdraw_from_contract(poster(booking), booking.pk)
    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def confirmation(
    client: Client,
    django_capture_on_commit_callbacks: Any,
    *,
    after_start: bool = True,
    worded: bool | None = None,
    name: str | None = None,
    email: str | None = None,
    subject: str | None = None,
    task_title: str | None = None,
    bare_subject: bool = False,
) -> tuple[Booking, Any]:
    """A trader withdrawal by a German-speaking poster, and the confirmation it sends.
    `worded`: a CANCELLATION version marking the lawyer's sentences is published after the
    booking is paid; False publishes it as a draft and sends as production would."""
    booking = starting(paid(client, trader=True), 5 * H)
    if worded is not None:
        mark_withdrawal_wording(
            is_draft=not worded,
            bare_subject=bare_subject,
            **({"subject": subject} if subject else {}),
        )
    if task_title is not None:
        Task.objects.filter(pk=booking.task_id).update(title=task_title)
    User.objects.filter(pk=booking.poster_id).update(locale="de")
    at = booking.slot_start + H if after_start else booking.created_at + 2 * H
    cancel.withdraw_from_contract(poster(booking), booking.pk, name=name, email=email, now=at)
    mail.outbox.clear()
    with (
        override_settings(LEGAL_DRAFTS_ALLOWED=worded is not False),
        django_capture_on_commit_callbacks(execute=True),
    ):
        publish(cancel.WITHDRAWAL_RECEIVED_TOPIC, booking)
    [email] = [m for m in mail.outbox if "Widerruf" in m.subject]
    return booking, email


def mark_withdrawal_wording(
    *,
    is_draft: bool = False,
    subject: str = "Widerruf {booking_ref} bestätigt",
    bare_subject: bool = False,
) -> None:
    """A new CANCELLATION version in force marking the lawyer's confirmation email, with
    its subject line and placeholders, in each language."""
    bodies = {
        code: cancellation_body(code)
        + "<!-- email:withdrawal_confirmation_trader -->\n"
        + f"Subject: {subject if bare_subject else f'{subject} ({code})'}\n"
        + "Lawyer's confirmation for {name} <{email}>: {statement_text}, received on\n"
        + "{date} at {time} ({timezone}). Business {helper_business_name}, OIB {helper_oib}.\n"
        + "\n"
        + "Paid {total_paid}; done {done_amount}; refund {refund_amount}. {unknown}\n"
        + "<!-- /email:withdrawal_confirmation_trader -->\n"
        for code in SUPPORTED_LOCALES
    }
    publish_version(LegalDocument.Kind.CANCELLATION, WORDED, bodies=bodies, is_draft=is_draft)


def wording_flags(booking: Booking) -> int:
    return BookingEvent.objects.filter(
        booking=booking,
        type=state_machine.SUPPORT_FLAG,
        data__reason=withdrawal_email.WORDING_MISSING,
    ).count()


@pytest.mark.parametrize(
    ("after_start", "done", "refund"),
    [
        (True, "Unser Team ermittelt das gerade", "Unser Team ermittelt das gerade"),
        (False, "done 0,00 €", "refund 33,60 €"),
    ],
)
def test_the_confirmation_is_the_lawyers_wording_with_the_statement_filled_in(
    client: Client,
    gateway: FakeGateway,
    django_capture_on_commit_callbacks: Any,
    after_start: bool,
    done: str,
    refund: str,
) -> None:
    booking, email = confirmation(
        client, django_capture_on_commit_callbacks, after_start=after_start, worded=True
    )

    assert email.subject == f"Widerruf {booking.pk} bestätigt (de)"
    assert "Lawyer's confirmation for" in email.body
    assert "Vertrag widerrufen" in email.body  # the statement, in the poster's language
    assert done in email.body and refund in email.body
    assert "OIB " in email.body and "Paid 33,60 €" in email.body
    assert "{unknown}" in email.body  # not a placeholder: left as written
    assert "<!--" not in email.body and "DRAFT" not in email.body
    assert "Eingegangen (kroatische Zeit)" not in email.body
    assert wording_flags(booking) == 0
    sent = BookingEvent.objects.get(booking=booking, type=withdrawal_email.SENT)
    assert sent.data["language"] == "de"
    assert sent.data["cancellation_version"] == WORDED
    assert "provider_id" in sent.data


def subject_flags(booking: Booking) -> int:
    return BookingEvent.objects.filter(
        booking=booking,
        type=state_machine.SUPPORT_FLAG,
        data__reason=withdrawal_email.SUBJECT_UNUSABLE,
    ).count()


@pytest.mark.parametrize("title", ["Garden\nBcc: evil@example.com", "Garden\r\n\r\n  Bcc:\tx"])
def test_a_task_title_with_line_breaks_never_breaks_the_subject(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any, title: str
) -> None:
    """The title is the poster's own text (review R2-1): the subject stays one line and the
    confirmation goes out."""
    booking, email = confirmation(
        client,
        django_capture_on_commit_callbacks,
        worded=True,
        subject="Widerruf {task_title}",
        task_title=title,
    )

    assert "\n" not in email.subject and "\r" not in email.subject
    assert email.subject == f"Widerruf {' '.join(title.split())} (de)"
    assert email.bcc == []
    assert subject_flags(booking) == 0
    assert BookingEvent.objects.filter(booking=booking, type=withdrawal_email.SENT).exists()


@pytest.mark.parametrize(
    "subject",
    [
        # Over SUBJECT_MAX_LENGTH once filled.
        "{helper_business_name}" + "x" * 300,
        # A format character no header should carry.
        "Widerruf\u200b{booking_ref}",
    ],
)
def test_an_unusable_subject_falls_back_to_the_facts_subject_and_flags_support(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any, subject: str
) -> None:
    booking, email = confirmation(
        client, django_capture_on_commit_callbacks, worded=True, subject=subject
    )

    assert email.subject == "Widerruf eingegangen"
    assert "Lawyer's confirmation for" in email.body
    assert subject_flags(booking) == 1
    assert BookingEvent.objects.filter(booking=booking, type=withdrawal_email.SENT).exists()


def test_a_subject_that_fills_to_nothing_falls_back_and_flags_support(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    booking, email = confirmation(
        client,
        django_capture_on_commit_callbacks,
        worded=True,
        subject="{task_title}",
        task_title=" \r\n ",
        bare_subject=True,
    )

    assert email.subject == "Widerruf eingegangen"
    assert subject_flags(booking) == 1


def test_a_subject_that_fills_to_nothing_is_unusable() -> None:
    filled = withdrawal_confirmation.WithdrawalConfirmationText(
        subject="{task_title}", body="Body"
    ).filled({"task_title": " \r\n "})

    assert filled.subject == ""
    assert not withdrawal_email._usable_subject(filled.subject)  # pyright: ignore[reportPrivateUsage]


def test_the_statement_keeps_the_name_and_email_and_the_confirmation_goes_there(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    booking, email = confirmation(
        client,
        django_capture_on_commit_callbacks,
        worded=True,
        name="  Ana   Horvat ",
        email="ana.h@example.net",
    )

    given = WithdrawalStatement.objects.get(booking=booking)
    assert (given.name, given.email, given.language) == ("Ana Horvat", "ana.h@example.net", "de")
    event = BookingEvent.objects.get(booking=booking, type=cancel.STATEMENT)
    assert event.data["statement"] == cancel.STATEMENT_WITHDRAW
    assert event.created_at == given.submitted_at
    assert email.to == ["ana.h@example.net"]
    assert "Ana Horvat <ana.h@example.net>" in email.body


def test_without_details_the_statement_uses_the_account(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    booking, email = confirmation(client, django_capture_on_commit_callbacks)

    account = poster(booking)
    given = WithdrawalStatement.objects.get(booking=booking)
    assert given.name == f"{account.first_name} {account.last_name}"
    assert given.email == account.email
    assert email.to == [account.email]


@pytest.mark.parametrize(
    ("name", "email"),
    [("", "ana@example.com"), ("Ana", "not-an-email"), ("x" * 201, "a@example.com")],
)
def test_a_statement_without_a_name_or_a_valid_email_is_refused(
    client: Client, gateway: FakeGateway, name: str, email: str
) -> None:
    booking = starting(paid(client, trader=True), 5 * H)

    with pytest.raises(ApiError) as exc:
        cancel.withdraw_from_contract(
            poster(booking), booking.pk, name=name, email=email, now=booking.slot_start + H
        )

    assert code(exc) == ErrorCode.INVALID_WITHDRAWAL_DETAILS.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert not WithdrawalStatement.objects.filter(booking=booking).exists()


def test_without_the_marked_sentences_it_confirms_the_facts_and_flags_support(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    booking, email = confirmation(client, django_capture_on_commit_callbacks)

    # Never held back: the facts go out, with no legal sentence of our own.
    assert "Eingegangen (kroatische Zeit)" in email.body
    assert "Lawyer" not in email.body and "DRAFT" not in email.body
    assert wording_flags(booking) == 1
    sent = BookingEvent.objects.get(booking=booking, type=withdrawal_email.SENT)
    assert sent.data["cancellation_version"] == ""


def test_a_draft_version_never_reaches_a_poster_in_production(
    client: Client,
    gateway: FakeGateway,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking, email = confirmation(client, django_capture_on_commit_callbacks, worded=False)

    assert "Lawyer" not in email.body
    assert wording_flags(booking) == 1


# --- The API. ---

BOOKING_QUERY = """
query($id: ID!) { booking(id: $id) {
  id status withdrawalAvailable withdrawalEndsAt
  cancelPreview { id case chargeCents refundCents helperCompensationCents keptFeeCents refusedWith }
  cancellation { id withdrawal case refundCents awaitingSupport confirmed }
} }
"""
CANCEL = """
mutation($id: ID!, $reason: PosterCancelReason!, $expected: Int!) {
  cancelBooking(id: $id, reason: $reason, expectedRefundCents: $expected) {
  id status cancellation { id case refundCents helperCompensationCents awaitingSupport }
} }
"""
WITHDRAW = """
mutation($id: ID!, $name: String! = "Ana Horvat", $email: String! = "ana@example.com") {
  withdrawFromContract(id: $id, name: $name, email: $email) {
  id status cancellation { id withdrawal case awaitingSupport }
} }
"""


def token(user: User) -> str:
    return sign_in(user).access_token


def accepted_hours_ago(booking: Booking, hours: int, *, starts_in: dt.timedelta) -> Booking:
    """The API uses the real clock: accepted `hours` ago, past the 1-hour grace period."""
    now = timezone.now()
    Booking.objects.filter(pk=booking.pk).update(
        created_at=now - hours * H, slot_start=now + starts_in
    )
    booking.refresh_from_db()
    return booking


def test_the_poster_sees_the_preview_and_the_helper_doesnt(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client, trader=True), 2, starts_in=72 * H)

    mine = gql(client, BOOKING_QUERY, {"id": str(booking.pk)}, token=token(poster(booking)))
    theirs = gql(client, BOOKING_QUERY, {"id": str(booking.pk)}, token=token(helper(booking)))

    preview = mine["data"]["booking"]["cancelPreview"]
    assert (preview["case"], preview["refundCents"], preview["refusedWith"]) == (
        "EARLY",
        3_360,
        None,
    )
    assert mine["data"]["booking"]["withdrawalAvailable"] is True
    assert mine["data"]["booking"]["withdrawalEndsAt"]
    assert theirs["data"]["booking"]["cancelPreview"] is None
    assert theirs["data"]["booking"]["withdrawalAvailable"] is False
    assert theirs["data"]["booking"]["withdrawalEndsAt"] is None


def test_cancel_booking_returns_the_cancelled_booking(client: Client, gateway: FakeGateway) -> None:
    booking = accepted_hours_ago(paid(client), 2, starts_in=72 * H)

    body = gql(
        client,
        CANCEL,
        {"id": str(booking.pk), "reason": "NO_LONGER_NEEDED", "expected": 3_360},
        token=token(poster(booking)),
    )

    result = body["data"]["cancelBooking"]
    assert result["status"] == "CANCEL_REQUESTED"
    assert result["cancellation"]["case"] == "EARLY"
    assert result["cancellation"]["refundCents"] == 3_360


def test_cancel_booking_is_refused_for_others_and_anonymous(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    variables = {"id": str(booking.pk), "reason": "OTHER", "expected": 3_360}

    assert error_codes(gql(client, CANCEL, variables)) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, CANCEL, variables, token=token(helper(booking)))) == [
        "BOOKING_NOT_FOUND"
    ]
    assert error_codes(gql(client, WITHDRAW, {"id": str(booking.pk)})) == ["UNAUTHENTICATED"]


def test_withdraw_from_contract_is_refused_for_the_helper_and_a_stranger(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client, trader=True), 6, starts_in=-H)
    stranger = User.objects.exclude(pk__in=[booking.poster_id, booking.helper_id]).first()
    assert stranger is not None

    for user in (helper(booking), stranger):
        body = gql(client, WITHDRAW, {"id": str(booking.pk)}, token=token(user))
        assert error_codes(body) == ["BOOKING_NOT_FOUND"]
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


def test_withdraw_from_contract_after_the_start_waits_for_support(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client, trader=True), 6, starts_in=-H)
    bearer = token(poster(booking))

    refused = gql(
        client, CANCEL, {"id": str(booking.pk), "reason": "OTHER", "expected": 3_360}, token=bearer
    )
    body = gql(client, WITHDRAW, {"id": str(booking.pk)}, token=bearer)

    assert error_codes(refused) == ["WORK_STARTED"]
    result = body["data"]["withdrawFromContract"]
    assert result["status"] == "CANCEL_REQUESTED"
    assert result["cancellation"] == {
        "id": str(booking.pk),
        "withdrawal": True,
        "case": None,
        "awaitingSupport": True,
    }


def test_the_cancel_mutations_are_rate_limited(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    bearer = token(helper(booking))

    codes = [
        error_codes(gql(client, WITHDRAW, {"id": str(booking.pk)}, token=bearer))[0]
        for _ in range(6)
    ]

    assert codes[:5] == ["BOOKING_NOT_FOUND"] * 5
    assert codes[5] == "RATE_LIMITED"


# --- Support's queue and the share form (the booking admin). ---


def withdrawn_after_start(client: Client) -> Booking:
    booking = starting(paid(client, trader=True), 5 * H)
    cancel.withdraw_from_contract(poster(booking), booking.pk, now=booking.slot_start + H)
    return booking


def test_the_queue_lists_withdrawals_waiting_for_the_share(
    client: Client, gateway: FakeGateway
) -> None:
    waiting = withdrawn_after_start(client)
    other = starting(paid(client, helpers=("luka", "ante")), 72 * H)
    cancel.cancel_booking(poster(other), other.pk, REASON)
    staff = Client()
    staff.force_login(make_staff(Role.SUPPORT))

    page = staff.get(reverse("admin:bookings_booking_changelist") + "?cancellation=share")
    settling = staff.get(reverse("admin:bookings_booking_changelist") + "?cancellation=settling")

    held = starting(paid(client, helpers=("petar", "josip")), 72 * H)
    refused = staff.get(reverse("admin:bookings_booking_changelist") + "?cancellation=refused")

    assert page.status_code == 200
    assert str(waiting.pk) in page.content.decode() and str(other.pk) not in page.content.decode()
    assert str(held.pk) not in page.content.decode()
    # Only bookings with a settlement in flight: not the held one, nor the withdrawal
    # still waiting for its share (review R1-3).
    assert str(other.pk) in settling.content.decode()
    assert str(held.pk) not in settling.content.decode()
    assert str(waiting.pk) not in settling.content.decode()
    assert str(held.pk) not in refused.content.decode()
    assert str(other.pk) not in refused.content.decode()


def test_support_saves_the_share_from_the_booking_page(
    client: Client, gateway: FakeGateway
) -> None:
    booking = withdrawn_after_start(client)
    staff = Client()
    staff.force_login(make_staff(Role.SUPPORT))
    share_url = reverse("admin:bookings_booking_withdrawal_share", args=[booking.pk])

    page = staff.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    assert share_url in page.content.decode()
    assert staff.post(share_url, {"work_done_percent": "120"}).status_code == 200
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    response = staff.post(share_url, {"work_done_percent": "50"})

    assert response.status_code == 302
    assert amounts(booking) == (1_680, 1_500, 180, 1_680, 180)
    again = staff.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    assert share_url not in again.content.decode()


def test_trust_cant_enter_a_share(client: Client, gateway: FakeGateway) -> None:
    booking = withdrawn_after_start(client)
    staff = Client()
    staff.force_login(make_staff(Role.TRUST))
    share_url = reverse("admin:bookings_booking_withdrawal_share", args=[booking.pk])

    page = staff.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    response = staff.post(share_url, {"work_done_percent": "50"})

    assert share_url not in page.content.decode()
    assert response.status_code == 403
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


# --- Review round: the shown refund, transient Stripe errors, a status change mid-way. ---


M = dt.timedelta(minutes=1)


@pytest.mark.parametrize(
    ("starts", "opened", "tapped"),
    [
        # Opened 24 h 2 min before the start (EARLY, full), tapped at 23 h 59 min (LATE).
        (26 * H, 2 * H - 2 * M, 2 * H + M),
        # Opened inside the 1 h grace period, tapped after it, under 24 h to go.
        (20 * H, 50 * M, 70 * M),
    ],
)
def test_a_deadline_passing_on_the_open_screen_refuses_the_cancel(
    client: Client,
    gateway: FakeGateway,
    starts: dt.timedelta,
    opened: dt.timedelta,
    tapped: dt.timedelta,
) -> None:
    booking = starting(paid(client), starts)
    seen = cancel.preview(booking, booking.created_at + opened)
    assert seen.refund_cents == 3_360

    with pytest.raises(ApiError) as exc:
        cancel.cancel_booking(
            poster(booking),
            booking.pk,
            REASON,
            expected_refund_cents=seen.refund_cents,
            now=booking.created_at + tapped,
        )

    assert code(exc) == ErrorCode.CANCELLATION_CHANGED.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    # With the new preview's amount it goes through.
    cancel.cancel_booking(
        poster(booking),
        booking.pk,
        REASON,
        expected_refund_cents=2_520,
        now=booking.created_at + tapped,
    )


@pytest.mark.parametrize(
    ("status", "stripe_code"),
    [(429, "rate_limit"), (409, "idempotency_key_in_use"), (400, "lock_timeout")],
)
def test_a_transient_4xx_is_retried_not_sent_to_support(
    client: Client, gateway: FakeGateway, status: int, stripe_code: str
) -> None:
    booking = late_cancelled(client)
    stripe = FakeSettlementStripe(
        fail="transfer_reversal",
        fail_with=StripeRequestError("test", code=stripe_code, http_status=status),
    )

    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    s = CancellationSettlement.objects.get(booking=booking)
    assert s.refused_at is None
    # Only a 5xx stores the failure under the key; these keep it.
    assert s.transfer_reversal_key_version == 1
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (750, 90, 2_520)


def test_a_dispute_between_steps_stops_the_settlement(client: Client, gateway: FakeGateway) -> None:
    booking = late_cancelled(client)
    stripe = FakeSettlementStripe()
    original = stripe.refund_application_fee

    def then_disputed(**kwargs: Any) -> Any:
        result = original(**kwargs)
        Booking.objects.filter(pk=booking.pk).update(status=S.DISPUTED)
        return result

    stripe.refund_application_fee = then_disputed  # type: ignore[method-assign]

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.sent() == ["fee_refund"]
    assert BookingEvent.objects.filter(booking=booking, type=settlement.NOT_SETTLEABLE).exists()


def test_cancel_booking_refuses_a_stale_refund_over_the_api(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client), 2, starts_in=72 * H)

    body = gql(
        client,
        CANCEL,
        {"id": str(booking.pk), "reason": "OTHER", "expected": 2_520},
        token=token(poster(booking)),
    )

    assert error_codes(body) == ["CANCELLATION_CHANGED"]


# --- Review round 1. ---


def failing_charge_parts(code: str) -> FakeSettlementStripe:
    stripe = FakeSettlementStripe()

    def not_filled_in(**kwargs: Any) -> Any:
        raise StripeRequestError("payment_intents.retrieve", code=code)

    stripe.retrieve_charge_parts = not_filled_in  # type: ignore[method-assign]
    return stripe


@pytest.mark.parametrize("stripe_code", ["no_charge", "not_a_destination_charge"])
def test_a_charge_not_filled_in_yet_is_retried_while_the_settlement_is_young(
    client: Client, gateway: FakeGateway, stripe_code: str
) -> None:
    """Stripe fills a destination charge's transfer and fee a few seconds after it
    succeeds (seen in test mode): that is not a refusal yet."""
    booking = late_cancelled(client)

    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=failing_charge_parts(stripe_code))  # pyright: ignore[reportArgumentType]

    assert CancellationSettlement.objects.get(booking=booking).refused_at is None
    assert not BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)


@pytest.mark.parametrize("stripe_code", ["no_charge", "not_a_destination_charge"])
def test_a_charge_still_without_its_parts_after_an_hour_goes_to_support(
    client: Client,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Any,
    stripe_code: str,
) -> None:
    booking = late_cancelled(client)
    CancellationSettlement.objects.filter(booking=booking).update(
        created_at=timezone.now() - settlement.CHARGE_PARTS_PATIENCE
    )

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        settlement.settle(booking.pk, gateway=failing_charge_parts(stripe_code))  # pyright: ignore[reportArgumentType]

    s = CancellationSettlement.objects.get(booking=booking)
    assert s.refused_at is not None and s.refused_step == "charge"
    assert BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG).exists()
    assert any(r.name == "bookings.support" for r in caplog.records)
    assert booking.pk not in settlement.unsettled(now=timezone.now() + H)


def test_a_share_waiting_too_long_flags_support_once(client: Client, gateway: FakeGateway) -> None:
    waiting = withdrawn_after_start(client)
    statement = BookingEvent.objects.get(booking=waiting, type=cancel.STATEMENT)
    later = statement.created_at + cancel.SHARE_PATIENCE

    assert cancel.flag_overdue_shares(now=later - H) == 0
    assert cancel.flag_overdue_shares(now=later) == 1
    assert cancel.flag_overdue_shares(now=later + H) == 0
    assert (
        BookingEvent.objects.filter(
            booking=waiting, type=state_machine.SUPPORT_FLAG, data__reason=cancel.SHARE_OVERDUE
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_two_cancel_taps_at_once_open_one_settlement(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both taps pass the screen; the row lock lets one cancel and refuses the other."""
    # The threads commit, so the settle task would run against real Stripe: not here.
    monkeypatch.setattr(tasks.settle_cancellation_task, "delay", lambda *args, **kwargs: None)
    booking = starting(paid(client), 72 * H)
    user = poster(booking)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def tap() -> None:
        barrier.wait(5)
        try:
            cancel.cancel_booking(user, booking.pk, REASON)
            results.append("cancelled")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value, "cancelled"]
    assert CancellationSettlement.objects.filter(booking=booking).count() == 1


# --- Review round 3: a provider outage never drops the withdrawal confirmation. ---


class Outage:
    """The email provider: down until `up`, then sends (and counts what it sent)."""

    def __init__(self) -> None:
        self.up = False
        self.sent: list[str] = []

    def __call__(self, to: str, email: Any, **kwargs: Any) -> str:
        if not self.up:
            raise ResendError("503")
        self.sent.append(to)
        return "re_msg_1"


def confirmation_delivery(
    client: Client, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any
) -> tuple[Booking, Delivery, Outage]:
    outage = Outage()
    monkeypatch.setattr(notification_delivery, "send_email", outage)
    booking = starting(paid(client, trader=True), 5 * H)
    cancel.withdraw_from_contract(poster(booking), booking.pk, now=booking.slot_start + H)
    with django_capture_on_commit_callbacks(execute=True):
        publish(cancel.WITHDRAWAL_RECEIVED_TOPIC, booking)
    delivery = Delivery.objects.get(
        kind=withdrawal_email.KIND, channel=Channel.EMAIL, outbox__aggregate_id=str(booking.pk)
    )
    return booking, delivery, outage


def attempt(delivery: Delivery) -> notification_delivery.DeliveryResult:
    Delivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
    with transaction.atomic():
        result, _ = notification_delivery.deliver(delivery.pk)
    return result


def send_failed_flags(booking: Booking) -> int:
    return BookingEvent.objects.filter(
        booking=booking,
        type=state_machine.SUPPORT_FLAG,
        data__reason=withdrawal_email.SEND_FAILED,
    ).count()


@override_settings(NOTIFICATION_MAX_ATTEMPTS=3)
def test_the_final_quick_failure_flags_support_once_and_keeps_retrying_slowly(
    client: Client,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking, delivery, _ = confirmation_delivery(
        client, monkeypatch, django_capture_on_commit_callbacks
    )
    while Delivery.objects.get(pk=delivery.pk).attempts < 3:
        assert send_failed_flags(booking) == 0
        attempt(delivery)

    assert send_failed_flags(booking) == 1
    flag = BookingEvent.objects.get(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=withdrawal_email.SEND_FAILED
    )
    assert flag.data == {"reason": withdrawal_email.SEND_FAILED}  # IDs only, no PII
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.next_attempt_at - timezone.now() > 2 * H  # every 3 h, not every hour

    for _ in range(3):
        assert attempt(delivery) == notification_delivery.DeliveryResult.RETRY_SCHEDULED
    assert send_failed_flags(booking) == 1


@override_settings(NOTIFICATION_MAX_ATTEMPTS=2)
def test_the_slow_retry_sends_it_once_the_provider_is_back_and_never_twice(
    client: Client,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking, delivery, outage = confirmation_delivery(
        client, monkeypatch, django_capture_on_commit_callbacks
    )
    while Delivery.objects.get(pk=delivery.pk).attempts < 4:
        attempt(delivery)
    outage.up = True

    assert attempt(delivery) == notification_delivery.DeliveryResult.SENT
    assert attempt(delivery) == notification_delivery.DeliveryResult.NOT_DUE
    assert (
        notification_delivery.deliver(delivery.pk)[0]
        == notification_delivery.DeliveryResult.NOT_DUE
    )

    assert outage.sent == [poster(booking).email]
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.SENT
    [sent] = BookingEvent.objects.filter(booking=booking, type=withdrawal_email.SENT)
    assert sent.data["provider_id"] == "re_msg_1"


@override_settings(NOTIFICATION_MAX_ATTEMPTS=2)
def test_after_three_days_of_outage_it_fails_for_good_with_support_already_told(
    client: Client,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    booking, delivery, _ = confirmation_delivery(
        client, monkeypatch, django_capture_on_commit_callbacks
    )
    while Delivery.objects.get(pk=delivery.pk).attempts < 2:
        attempt(delivery)
    Delivery.objects.filter(pk=delivery.pk).update(created_at=timezone.now() - dt.timedelta(days=3))

    assert attempt(delivery) == notification_delivery.DeliveryResult.FAILED
    assert send_failed_flags(booking) == 1


@override_settings(NOTIFICATION_MAX_ATTEMPTS=2)
def test_a_contract_confirmation_that_fails_for_good_flags_support(
    client: Client,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    monkeypatch.setattr(notification_delivery, "send_email", Outage())
    booking = paid(client)
    with django_capture_on_commit_callbacks(execute=True):
        publish(FUNDS_HELD_TOPIC, booking)
    delivery = Delivery.objects.get(
        kind=contract_email.KIND, channel=Channel.EMAIL, outbox__aggregate_id=str(booking.pk)
    )
    while Delivery.objects.get(pk=delivery.pk).status == Delivery.Status.PENDING:
        attempt(delivery)

    [flag] = BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=contract_email.SEND_FAILED
    )
    assert flag.data == {"reason": contract_email.SEND_FAILED}
