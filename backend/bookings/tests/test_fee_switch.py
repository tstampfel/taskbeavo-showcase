"""A booking keeps the fee schedule it was accepted under (BEAV-191, review R1-1 to R1-4):
acceptOffer charges the schedule in force and refuses a total the poster no longer sees,
and every cancellation path (preview, cancel, pro rata withdrawal, settle, charge.refunded)
keeps the booking's stored rate after a new price list takes effect."""

import dataclasses
import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from bookings import cancel, checkout, money, settlement
from bookings.models import Booking, CancellationSettlement
from bookings.tests.fakes import FakeGateway, FakeSettlementStripe
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    accept,
    consent,
    legal_documents,
    make_trader,
    post_event,
    publish,
    succeeded_event,
    two_offers,
    webhook_secret,
)
from bookings.tests.test_poster_cancel import (
    REASON,
    amounts,
    late_cancelled,
    paid,
    poster,
    refunded_event,
    starting,
)
from core.errors import ApiError, ErrorCode
from offers import services as offers_services
from staff.roles import Role
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)

# A: the 12%, €2.50 list in force; B: a 15%, €3.00 list taking effect today.
A = dataclasses.replace(money.FEE_SCHEDULES[-1], effective_from=dt.date(2026, 1, 1))


def b_from_today() -> money.FeeSchedule:
    today = timezone.localtime(timezone.now(), money.FEE_TIME_ZONE).date()
    return money.FeeSchedule(version="B", effective_from=today, percent=15, minimum_cents=300)


@pytest.fixture
def stripe() -> FakeSettlementStripe:
    return FakeSettlementStripe()


@pytest.fixture(autouse=True)
def under_a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(money, "FEE_SCHEDULES", (A,))


@pytest.fixture
def switch(monkeypatch: pytest.MonkeyPatch):
    """Call it to make B the schedule in force from now on."""
    return lambda: monkeypatch.setattr(money, "FEE_SCHEDULES", (A, b_from_today()))


def test_the_booking_stores_the_schedule_it_was_charged_under(
    client: Client, gateway: FakeGateway
) -> None:
    booking = paid(client)

    assert (booking.fee_schedule_version, booking.fee_percent, booking.fee_cents) == (
        A.version,
        12,
        360,
    )


def test_accept_refuses_the_total_shown_before_a_switch(gateway: FakeGateway, switch) -> None:
    chosen, _ = two_offers()
    shown = 3_000 + A.fee_cents(3_000)  # €33.60 on the pay button
    switch()

    with pytest.raises(ApiError) as exc:
        checkout.accept_offer(
            chosen.task.poster,
            str(chosen.pk),
            expected_price_cents=3_000,
            expected_total_cents=shown,
            expected_fee_schedule_version=A.version,
            consent=consent(variant="private"),
        )

    assert exc.value.extensions["code"] == ErrorCode.FEE_CHANGED.name
    assert not Booking.objects.exists()
    # Reloaded, the checkout shows €34.50; that total books at B's rate.
    booking = checkout.accept_offer(
        chosen.task.poster,
        str(chosen.pk),
        expected_price_cents=3_000,
        expected_total_cents=3_450,
        expected_fee_schedule_version="B",
        consent=consent(variant="private"),
    ).booking
    assert (booking.fee_cents, booking.fee_percent, booking.fee_schedule_version) == (450, 15, "B")


def test_accept_refuses_a_rate_change_that_keeps_the_total(
    gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    # €10 is at the minimum under both lists (12% and 15%, at least €2.50), so the total
    # stays €12.50 while the rate K would use changes (review R3-2).
    chosen, _ = two_offers()
    offers_services.update_offer(chosen.helper, str(chosen.pk), price_cents=1_000)
    chosen.refresh_from_db()
    today = timezone.localtime(timezone.now(), money.FEE_TIME_ZONE).date()
    same_minimum = money.FeeSchedule(
        version="B", effective_from=today, percent=15, minimum_cents=250
    )
    monkeypatch.setattr(money, "FEE_SCHEDULES", (A, same_minimum))

    with pytest.raises(ApiError) as exc:
        checkout.accept_offer(
            chosen.task.poster,
            str(chosen.pk),
            expected_price_cents=1_000,
            expected_total_cents=1_250,
            expected_fee_schedule_version=A.version,
            consent=consent(variant="private"),
        )

    assert exc.value.extensions["code"] == ErrorCode.FEE_CHANGED.name
    assert not Booking.objects.exists()
    booking = checkout.accept_offer(
        chosen.task.poster,
        str(chosen.pk),
        expected_price_cents=1_000,
        expected_total_cents=1_250,
        expected_fee_schedule_version="B",
        consent=consent(variant="private"),
    ).booking
    assert (booking.fee_cents, booking.fee_percent, booking.fee_schedule_version) == (250, 15, "B")


def test_preview_and_cancel_after_a_switch_keep_the_bookings_rate(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe, switch
) -> None:
    booking = starting(paid(client), 20 * H)
    switch()
    now = booking.created_at + 2 * H

    preview = cancel.preview(booking, now)
    cancel.cancel_booking(poster(booking), booking.pk, REASON, now=now)

    # 12% of the €7.50 compensation, not B's 15% (€1.13).
    assert (preview.kept_fee_percent, preview.kept_fee_cents) == (12, 90)
    assert amounts(booking) == (2_520, 750, 90, 2_520, 270)
    booking.refresh_from_db()
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (750, 90, 2_520)
    assert post_event(client, refunded_event(booking, 2_520)) == 200
    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED


def test_a_settlement_stored_before_a_switch_completes_after_it(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe, switch
) -> None:
    booking = late_cancelled(client)
    switch()

    done = settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert done is not None
    assert stripe.sent() == ["fee_refund", "transfer_reversal", "refund"]
    assert post_event(client, refunded_event(booking, 2_520)) == 200
    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert CancellationSettlement.objects.get(booking=booking).confirmed_at is not None


def test_a_pro_rata_withdrawal_after_a_switch_keeps_the_bookings_rate(
    client: Client, gateway: FakeGateway, switch
) -> None:
    chosen, _ = two_offers()
    make_trader(chosen)
    booking = accept(chosen).booking
    booking.refresh_from_db()
    assert post_event(client, succeeded_event(booking, f"evt_test_{booking.pk.hex}")) == 200
    booking = starting(Booking.objects.get(pk=booking.pk), 5 * H)
    cancel.withdraw_from_contract(poster(booking), booking.pk, now=booking.slot_start + H)
    switch()

    cancel.settle_withdrawal(make_staff(Role.SUPPORT), booking.pk, 50)

    # Half the €30 price to the helper, 12% of it kept (€1.80), not 15% (€2.25).
    assert amounts(booking) == (1_680, 1_500, 180, 1_680, 180)
