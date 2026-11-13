"""Cancellation amounts (hard rule g; docs/PLAN.md, "TaskBeavo cancellation policy"):
every row of the policy, the time and money boundaries, the rounding, and the Stripe
split (BEAV-106 handoff) that leaves the helper with C and TaskBeavo with K."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from bookings import state_machine
from bookings.cancellation import (
    MIN_TASK_PRICE_CENTS,
    Case,
    NoFaultReason,
    NotACancellation,
    NotACancellationReason,
    Outcome,
    Party,
    call_out_cents,
    cancellation_outcome,
    kept_fee_cents,
    late_pay_cents,
    no_access_call_out,
    pro_rata_withdrawal_outcome,
    withdrawal_period_ends_at,
)
from core.errors import ErrorCode
from offers import services as offer_services

UTC = dt.UTC
ZAGREB = ZoneInfo("Europe/Zagreb")
ACCEPTED = dt.datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
H = dt.timedelta(hours=1)
TICK = dt.timedelta(microseconds=1)


def fee(amount: int) -> int:
    return state_machine.service_fee_cents(amount)


def cancel(
    *,
    by: Party = Party.POSTER,
    reason: NoFaultReason | None = None,
    amount: int = 3_000,
    accepted_at: dt.datetime = ACCEPTED,
    slot_start: dt.datetime = ACCEPTED + 72 * H,
    now: dt.datetime = ACCEPTED + 2 * H,
    trader: bool = False,
    funds_held: bool = True,
    payment_requested: bool = False,
) -> Outcome:
    return cancellation_outcome(
        cancelled_by=by,
        no_fault_reason=reason,
        amount_cents=amount,
        fee_cents=fee(amount),
        fee_percent=12,
        accepted_at=accepted_at,
        slot_start=slot_start,
        now=now,
        helper_is_trader=trader,
        funds_held=funds_held,
        payment_requested=payment_requested,
    )


def money(o: Outcome) -> tuple[int, int, int, int, int, int, int]:
    """(R, C, K, X, Y, credit, charge)"""
    return (
        o.refund_cents,
        o.helper_compensation_cents,
        o.kept_fee_cents,
        o.transfer_reversal_cents,
        o.fee_refund_cents,
        o.poster_credit_cents,
        o.charge_cents,
    )


# --- The policy table: one row per situation, the €30 worked example of PLAN.md. ---

# (label, cancel kwargs, case, R, C, K, credit)
POLICY_ROWS = [
    ("before acceptance", {"funds_held": False}, Case.NOT_CHARGED, 0, 0, 0, 0),
    (
        "poster, within 1 h of accepting, start in 2 h",
        {"now": ACCEPTED + 30 * dt.timedelta(minutes=1), "slot_start": ACCEPTED + 2 * H},
        Case.GRACE_PERIOD,
        3_360,
        0,
        0,
        0,
    ),
    ("poster, more than 24 h before the start", {}, Case.EARLY, 3_360, 0, 0, 0),
    (
        "poster, under 24 h, private helper",
        {"slot_start": ACCEPTED + 20 * H},
        Case.LATE,
        2_520,
        750,
        90,
        0,
    ),
    (
        "poster, under 24 h, trader inside the 14 days",
        {"slot_start": ACCEPTED + 20 * H, "trader": True},
        Case.LATE_TRADER_WITHDRAWAL,
        3_360,
        0,
        0,
        0,
    ),
    (
        "poster, under 24 h, trader booked more than 14 days ahead",
        {
            "slot_start": ACCEPTED + 21 * 24 * H,
            "now": ACCEPTED + 20 * 24 * H + 12 * H,
            "trader": True,
        },
        Case.LATE,
        2_520,
        750,
        90,
        0,
    ),
    ("helper, more than 24 h before", {"by": Party.HELPER}, Case.HELPER_CANCEL, 3_360, 0, 0, 0),
    (
        "helper, under 24 h",
        {"by": Party.HELPER, "slot_start": ACCEPTED + 20 * H},
        Case.HELPER_CANCEL,
        3_360,
        0,
        0,
        500,
    ),
    (
        "helper no-show, after the start",
        {"by": Party.HELPER, "slot_start": ACCEPTED + 2 * H, "now": ACCEPTED + 3 * H},
        Case.HELPER_CANCEL,
        3_360,
        0,
        0,
        500,
    ),
    (
        "no fault, poster, under 24 h",
        {"reason": NoFaultReason.HOSPITALISATION, "slot_start": ACCEPTED + 20 * H},
        Case.NO_FAULT,
        3_360,
        0,
        0,
        0,
    ),
    (
        "no fault, helper, under 24 h: no credit",
        {"by": Party.HELPER, "reason": NoFaultReason.WEATHER, "slot_start": ACCEPTED + 20 * H},
        Case.NO_FAULT,
        3_360,
        0,
        0,
        0,
    ),
]


@pytest.mark.parametrize(
    ("kwargs", "case", "refund", "comp", "kept", "credit"),
    [row[1:] for row in POLICY_ROWS],
    ids=[row[0] for row in POLICY_ROWS],
)
def test_every_policy_row(
    kwargs: dict, case: Case, refund: int, comp: int, kept: int, credit: int
) -> None:
    o = cancel(**kwargs)
    assert o.case == case
    assert o.refund_cents == refund
    assert o.helper_compensation_cents == comp
    assert o.kept_fee_cents == kept
    assert o.poster_credit_cents == credit


@pytest.mark.parametrize("reason", list(NoFaultReason))
def test_every_no_fault_reason_is_a_full_refund(reason: NoFaultReason) -> None:
    o = cancel(reason=reason, slot_start=ACCEPTED + 3 * H)
    assert (o.case, o.refund_cents, o.helper_compensation_cents) == (Case.NO_FAULT, 3_360, 0)


def test_no_fault_reasons_are_the_plans_list() -> None:
    assert {r.value for r in NoFaultReason} == {
        "WEATHER",
        "HOSPITALISATION",
        "BEREAVEMENT",
        "EMERGENCY",
        "MUTUAL_AGREEMENT",
        # Given only by the poster's free cancel after Running late (BEAV-124).
        "HELPER_LATE",
    }


def test_no_access_call_out_on_a_30_euro_task() -> None:
    o = no_access_call_out(amount_cents=3_000, fee_cents=360, fee_percent=12)
    assert o.case == Case.NO_ACCESS_CALL_OUT
    assert money(o) == (2_520, 750, 90, 2_520, 270, 0, 3_360)


def test_no_access_call_out_has_no_trader_exception() -> None:
    """PLAN.md: the call-out applies to traders inside the 14 days too (a risk for the
    lawyer), so the function doesn't even take the trader flag."""
    o = no_access_call_out(amount_cents=10_000, fee_cents=1_200, fee_percent=12)
    assert o.helper_compensation_cents == 1_500


# --- Payment already requested: not a cancellation. ---


def test_a_requested_payment_is_not_a_cancellation() -> None:
    with pytest.raises(NotACancellation) as err:
        cancel(payment_requested=True, slot_start=ACCEPTED + 20 * H)
    assert err.value.reason == NotACancellationReason.PAYMENT_REQUESTED


@pytest.mark.parametrize("by", list(Party))
@pytest.mark.parametrize("reason", [None, *NoFaultReason])
@pytest.mark.parametrize("now", [ACCEPTED + 2 * H, ACCEPTED + 80 * H], ids=["before", "after"])
def test_no_party_or_reason_bypasses_a_requested_payment(
    by: Party, reason: NoFaultReason | None, now: dt.datetime
) -> None:
    with pytest.raises(NotACancellation) as err:
        cancel(by=by, reason=reason, now=now, payment_requested=True)
    assert err.value.reason == NotACancellationReason.PAYMENT_REQUESTED


def test_a_requested_payment_blocks_the_call_out_too() -> None:
    with pytest.raises(NotACancellation):
        no_access_call_out(
            amount_cents=3_000, fee_cents=360, fee_percent=12, payment_requested=True
        )


# --- Time boundaries. ---


def test_exactly_1_h_after_accepting_is_still_the_grace_period() -> None:
    o = cancel(now=ACCEPTED + H, slot_start=ACCEPTED + 5 * H)
    assert o.case == Case.GRACE_PERIOD


def test_just_over_1_h_after_accepting_is_late() -> None:
    o = cancel(now=ACCEPTED + H + TICK, slot_start=ACCEPTED + 5 * H)
    assert o.case == Case.LATE


def test_the_grace_period_ends_when_the_work_starts() -> None:
    start = ACCEPTED + 30 * dt.timedelta(minutes=1)
    assert cancel(now=start - TICK, slot_start=start).case == Case.GRACE_PERIOD
    with pytest.raises(NotACancellation) as err:
        cancel(now=start, slot_start=start)
    assert err.value.reason == NotACancellationReason.WORK_STARTED


# --- A poster's cancel at or after the start is refused (user decision 2026-11-04). ---

START = ACCEPTED + 5 * H


@pytest.mark.parametrize(
    ("kwargs", "before"),
    [
        ({}, Case.LATE),
        ({"trader": True}, Case.LATE_TRADER_WITHDRAWAL),
    ],
    ids=["private helper", "trader inside the 14 days"],
)
def test_a_poster_cancel_at_the_start_is_refused(kwargs: dict, before: Case) -> None:
    assert cancel(slot_start=START, now=START - TICK, **kwargs).case == before
    for now in (START, START + 2 * H):
        with pytest.raises(NotACancellation) as err:
            cancel(slot_start=START, now=now, **kwargs)
        assert err.value.reason == NotACancellationReason.WORK_STARTED
        assert err.value.error_code == ErrorCode.WORK_STARTED


@pytest.mark.parametrize("by", list(Party))
@pytest.mark.parametrize("reason", list(NoFaultReason))
def test_a_no_fault_cancel_at_and_after_the_start_is_a_full_refund(
    by: Party, reason: NoFaultReason
) -> None:
    for now in (START, START + 2 * H):
        o = cancel(by=by, reason=reason, slot_start=START, now=now)
        assert (o.case, o.refund_cents, o.poster_credit_cents) == (Case.NO_FAULT, 3_360, 0)


def test_a_helper_no_show_after_the_start_is_still_a_helper_cancel() -> None:
    o = cancel(by=Party.HELPER, slot_start=START, now=START)
    assert (o.case, o.refund_cents, o.poster_credit_cents) == (Case.HELPER_CANCEL, 3_360, 500)


def test_nothing_charged_after_the_start_moves_nothing() -> None:
    assert cancel(slot_start=START, now=START, funds_held=False).case == Case.NOT_CHARGED


def test_a_requested_payment_maps_to_booking_action_not_allowed() -> None:
    with pytest.raises(NotACancellation) as err:
        cancel(payment_requested=True)
    assert err.value.error_code == ErrorCode.BOOKING_ACTION_NOT_ALLOWED


def test_now_before_the_acceptance_is_refused() -> None:
    with pytest.raises(ValueError, match="before"):
        cancel(now=ACCEPTED - TICK)


def test_exactly_24_h_before_the_start_is_not_late() -> None:
    start = ACCEPTED + 72 * H
    assert cancel(now=start - 24 * H, slot_start=start).case == Case.EARLY
    assert cancel(now=start - 24 * H + TICK, slot_start=start).case == Case.LATE


def test_the_helper_credit_needs_strictly_under_24_h() -> None:
    start = ACCEPTED + 72 * H
    assert cancel(by=Party.HELPER, now=start - 24 * H, slot_start=start).poster_credit_cents == 0
    later = cancel(by=Party.HELPER, now=start - 24 * H + TICK, slot_start=start)
    assert later.poster_credit_cents == 500


def test_the_late_window_comes_from_the_setting(settings: Any) -> None:
    settings.LATE_CANCEL_WINDOW_HOURS = 48
    assert cancel(slot_start=ACCEPTED + 40 * H).case == Case.LATE


def test_the_default_late_window_is_24_h(settings: Any) -> None:
    assert settings.LATE_CANCEL_WINDOW_HOURS == 24


# --- The trader's 14-day withdrawal period (NEEDS REVIEW by the lawyer). ---


@pytest.mark.parametrize(
    ("accepted_local", "ends_local"),
    [
        # Day of acceptance not counted; the period ends at 24:00 of the 14th day.
        (dt.datetime(2026, 11, 4, 10, 0), dt.datetime(2026, 11, 19, 0, 0)),
        (dt.datetime(2026, 11, 4, 0, 0), dt.datetime(2026, 11, 19, 0, 0)),
        (dt.datetime(2026, 11, 4, 23, 59), dt.datetime(2026, 11, 19, 0, 0)),
        # Across the end of summer time (25 Oct 2026).
        (dt.datetime(2026, 10, 20, 12, 0), dt.datetime(2026, 11, 4, 0, 0)),
        # Across the start of summer time (29 Mar 2026).
        (dt.datetime(2026, 3, 20, 12, 0), dt.datetime(2026, 4, 4, 0, 0)),
    ],
)
def test_the_withdrawal_period_ends_at_midnight_zagreb_after_14_days(
    accepted_local: dt.datetime, ends_local: dt.datetime
) -> None:
    accepted = accepted_local.replace(tzinfo=ZAGREB)
    assert withdrawal_period_ends_at(accepted) == ends_local.replace(tzinfo=ZAGREB)


def test_the_withdrawal_day_is_the_zagreb_day_not_the_utc_day() -> None:
    # 23:30 UTC on 4 Nov is 00:30 on 5 Nov in Zagreb.
    accepted = dt.datetime(2026, 11, 4, 23, 30, tzinfo=UTC)
    assert withdrawal_period_ends_at(accepted) == dt.datetime(2026, 11, 20, 0, 0, tzinfo=ZAGREB)


def test_a_late_trader_cancel_at_the_end_of_the_withdrawal_period() -> None:
    accepted = dt.datetime(2026, 11, 4, 10, 0, tzinfo=ZAGREB)
    ends = dt.datetime(2026, 11, 19, 0, 0, tzinfo=ZAGREB)
    start = ends + 10 * H

    def at(now: dt.datetime) -> Case:
        return cancel(accepted_at=accepted, slot_start=start, now=now, trader=True).case

    assert at(ends - TICK) == Case.LATE_TRADER_WITHDRAWAL
    assert at(ends) == Case.LATE


def test_a_trader_cancel_more_than_24_h_ahead_is_early() -> None:
    assert cancel(trader=True).case == Case.EARLY


# --- Money boundaries and rounding. ---


@pytest.mark.parametrize(
    ("price", "pay"),
    [
        (1_000, 500),  # 25% is 250: the €5 minimum
        (1_999, 500),  # 499.75 → 500
        (2_000, 500),  # exactly the minimum
        (2_001, 500),  # 500.25 → 500
        (2_002, 501),  # 500.50 → 501, half up
        (3_000, 750),
        (7_998, 2_000),  # 1999.5 → 2000
        (8_000, 2_000),  # exactly the €20 maximum
        (8_004, 2_000),
        (20_000, 2_000),
    ],
)
def test_late_pay_is_25_percent_min_5_max_20(price: int, pay: int) -> None:
    assert late_pay_cents(price) == pay


@pytest.mark.parametrize(
    ("price", "pay"),
    [
        (1_000, 500),
        (2_000, 500),
        (2_002, 501),
        (5_994, 1_499),  # 1498.5 → 1499
        (5_996, 1_499),
        (5_998, 1_500),  # 1499.5 → 1500
        (6_000, 1_500),  # exactly the €15 maximum
        (6_004, 1_500),
        (20_000, 1_500),
    ],
)
def test_call_out_is_25_percent_min_5_max_15(price: int, pay: int) -> None:
    assert call_out_cents(price) == pay


@pytest.mark.parametrize(
    ("compensation", "kept"),
    [(500, 60), (501, 60), (504, 60), (505, 61), (750, 90), (1_500, 180), (2_000, 240)],
)
def test_taskbeavo_keeps_12_percent_of_the_compensation_rounded_half_up(
    compensation: int, kept: int
) -> None:
    assert kept_fee_cents(compensation, 12) == kept


# The BEAV-106 handoff table: (price, F, C, K, R, X, Y).
SPLITS = [
    (3_000, 360, 750, 90, 2_520, 2_520, 270),
    (1_000, 250, 500, 60, 690, 690, 190),  # €2.50 minimum fee; K is still 12% of C
    (20_000, 2_400, 2_000, 240, 20_160, 20_160, 2_160),  # the €20 cap
]


@pytest.mark.parametrize(("price", "f", "c", "k", "r", "x", "y"), SPLITS)
def test_late_cancel_split_matches_the_handoff(
    price: int, f: int, c: int, k: int, r: int, x: int, y: int
) -> None:
    assert fee(price) == f
    o = cancel(amount=price, slot_start=ACCEPTED + 20 * H)
    assert money(o) == (r, c, k, x, y, 0, price + f)


def test_full_refunds_reverse_everything() -> None:
    o = cancel()
    assert money(o) == (3_360, 0, 0, 3_360, 360, 0, 3_360)
    assert o.is_full_refund


def test_the_not_charged_outcome_moves_no_money() -> None:
    o = cancel(funds_held=False)
    assert money(o) == (0, 0, 0, 0, 0, 0, 0)
    assert not o.is_full_refund


# --- The split leaves every party right, in the handoff's order, never negative. ---


def ledger(o: Outcome) -> tuple[int, int, int]:
    """Run fee refund → transfer reversal → poster refund on a destination charge and
    return (poster, helper, TaskBeavo); every intermediate balance must stay >= 0."""
    helper, taskbeavo, poster = o.amount_cents, o.fee_cents, 0
    taskbeavo -= o.fee_refund_cents
    helper += o.fee_refund_cents
    assert taskbeavo >= 0
    helper -= o.transfer_reversal_cents
    taskbeavo += o.transfer_reversal_cents
    assert helper >= 0
    taskbeavo -= o.refund_cents
    poster += o.refund_cents
    assert taskbeavo >= 0
    return poster, helper, taskbeavo


PRICES = [1_000, 1_001, 1_999, 2_002, 2_083, 3_000, 5_998, 7_998, 8_000, 18_667, 20_000, 99_999]


@pytest.mark.parametrize("price", PRICES)
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"slot_start": ACCEPTED + 20 * H},
        {"slot_start": ACCEPTED + 20 * H, "trader": True},
        {"by": Party.HELPER, "slot_start": ACCEPTED + 20 * H},
        {"reason": NoFaultReason.EMERGENCY},
    ],
)
def test_the_split_balances_for_every_price(price: int, kwargs: dict) -> None:
    o = cancel(amount=price, **kwargs)
    assert ledger(o) == (o.refund_cents, o.helper_compensation_cents, o.kept_fee_cents)
    assert o.refund_cents + o.helper_compensation_cents + o.kept_fee_cents == o.charge_cents


@pytest.mark.parametrize("price", PRICES)
def test_the_call_out_split_balances_for_every_price(price: int) -> None:
    o = no_access_call_out(amount_cents=price, fee_cents=fee(price), fee_percent=12)
    assert ledger(o) == (o.refund_cents, o.helper_compensation_cents, o.kept_fee_cents)


# --- Bad input is refused, never rounded into a split. ---


def test_the_minimum_task_price_matches_the_offer_minimum() -> None:
    assert MIN_TASK_PRICE_CENTS == offer_services.MIN_PRICE_CENTS


def test_the_kept_fee_uses_the_service_fee_rate() -> None:
    # Above the €2.50 minimum the fee and K are the same function of their base.
    for cents in (3_000, 3_004, 3_005, 12_345):
        assert kept_fee_cents(cents, 12) == state_machine.service_fee_cents(cents)


def test_a_task_below_the_10_euro_minimum_is_refused() -> None:
    with pytest.raises(ValueError, match="minimum"):
        cancel(amount=999)


@pytest.mark.parametrize("f", [-1, 3_000, 3_001])
def test_a_fee_outside_0_and_the_price_is_refused(f: int) -> None:
    with pytest.raises(ValueError):
        no_access_call_out(amount_cents=3_000, fee_cents=f, fee_percent=12)


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match="time zone"):
        cancel(now=dt.datetime(2026, 11, 4, 12, 0))


@pytest.mark.parametrize(
    ("fee", "refund", "reversal", "fee_refund"),
    [(0, 2_160, 2_250, 0), (50, 2_210, 2_250, 0), (80, 2_240, 2_250, 0), (220, 2_380, 2_380, 130)],
)
def test_taskbeavo_keeps_the_full_rate_whatever_fee_a_credit_left(
    fee: int, refund: int, reversal: int, fee_refund: int
) -> None:
    """Rule g (user decision 2026-11-07): K is 12% of C even when a credit lowered F below
    K. The part of K the fee didn't cover comes back with the transfer reversal, so the
    helper still ends with exactly C (Stripe sandbox: payments/sandbox)."""
    outcome = no_access_call_out(amount_cents=3_000, fee_cents=fee, fee_percent=12)

    assert (
        outcome.helper_compensation_cents,
        outcome.kept_fee_cents,
        outcome.refund_cents,
        outcome.transfer_reversal_cents,
        outcome.fee_refund_cents,
    ) == (750, 90, refund, reversal, fee_refund)
    helper_keeps = 3_000 + outcome.fee_refund_cents - outcome.transfer_reversal_cents
    taskbeavo_keeps = (
        fee - outcome.fee_refund_cents + outcome.transfer_reversal_cents - outcome.refund_cents
    )
    assert (helper_keeps, taskbeavo_keeps) == (750, 90)


def test_k_never_takes_more_than_is_left_of_the_charge() -> None:
    """A credited pro rata withdrawal where the helper keeps nearly the whole price: K is bounded
    by what is left of the charge, never by the fee."""
    outcome = pro_rata_withdrawal_outcome(
        amount_cents=3_000, fee_cents=0, fee_percent=12, work_done_percent=99
    )

    assert (outcome.helper_compensation_cents, outcome.kept_fee_cents, outcome.refund_cents) == (
        2_970,
        30,
        0,
    )


def test_an_early_cancel_of_a_booking_without_a_fee_refunds_the_price() -> None:
    outcome = cancellation_outcome(
        cancelled_by=Party.POSTER,
        no_fault_reason=None,
        amount_cents=3_000,
        fee_cents=0,
        fee_percent=12,
        accepted_at=ACCEPTED,
        slot_start=ACCEPTED + 72 * H,
        now=ACCEPTED + 2 * H,
        helper_is_trader=False,
    )

    assert (outcome.case, outcome.refund_cents, outcome.fee_refund_cents) == (Case.EARLY, 3_000, 0)
    assert outcome.is_full_refund


@pytest.mark.parametrize(
    "change",
    [
        {
            "kept_fee_cents": 91,
            "refund_cents": 2_519,
            "transfer_reversal_cents": 2_519,
            "fee_refund_cents": 269,
        },  # K isn't 12% of C
        {"transfer_reversal_cents": 2_500},  # X != R: TaskBeavo would fund part of it
        {"fee_refund_cents": 360},  # Y != F - K
        {"refund_cents": 2_521, "transfer_reversal_cents": 2_521},  # R + C + K != A
        {
            "fee_cents": 2_999,
            "helper_compensation_cents": 3_001,
            "kept_fee_cents": 360,
            "refund_cents": 2_638,
            "transfer_reversal_cents": 2_638,
            "fee_refund_cents": 2_639,
        },  # C isn't the late pay, every other invariant holds
        {"poster_credit_cents": 500},  # a credit on a poster's late cancel
        {"case": Case.EARLY},  # a full-refund case that pays the helper
        {  # a late cancel that pays the helper nothing
            "helper_compensation_cents": 0,
            "kept_fee_cents": 0,
            "refund_cents": 3_360,
            "transfer_reversal_cents": 3_360,
            "fee_refund_cents": 360,
        },
        {  # the call-out paid as late pay
            "case": Case.NO_ACCESS_CALL_OUT,
            "amount_cents": 8_000,
            "fee_cents": 960,
            "helper_compensation_cents": 2_000,
            "kept_fee_cents": 240,
            "refund_cents": 6_720,
            "transfer_reversal_cents": 6_720,
            "fee_refund_cents": 720,
        },
    ],
)
def test_an_inconsistent_outcome_cant_be_built(change: dict) -> None:
    base = {
        "case": Case.LATE,
        "amount_cents": 3_000,
        "fee_cents": 360,
        "helper_compensation_cents": 750,
        "kept_fee_cents": 90,
        "kept_fee_percent": 12,
        "refund_cents": 2_520,
        "transfer_reversal_cents": 2_520,
        "fee_refund_cents": 270,
        "poster_credit_cents": 0,
    }
    Outcome(**base)  # the consistent one builds
    with pytest.raises(ValueError):
        Outcome(**{**base, **change})


@pytest.mark.parametrize("credit", [1, 499, 501, 1_000])
def test_the_helper_cancel_credit_is_exactly_5_euro(credit: int) -> None:
    full = {
        "case": Case.HELPER_CANCEL,
        "amount_cents": 3_000,
        "fee_cents": 360,
        "helper_compensation_cents": 0,
        "kept_fee_cents": 0,
        "kept_fee_percent": 12,
        "refund_cents": 3_360,
        "transfer_reversal_cents": 3_360,
        "fee_refund_cents": 360,
    }
    Outcome(**full, poster_credit_cents=500)
    with pytest.raises(ValueError, match="credit"):
        Outcome(**full, poster_credit_cents=credit)
