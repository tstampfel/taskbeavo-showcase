"""Withdrawal from the contract (Directive 2023/2673 withdrawal button; PLAN.md, "Poster
uses the withdrawal button"; BEAV-122): when it is offered, the full refund before the
start, and the pro rata split support enters after the start. Amounts from
bookings/cancellation.py only (hard rule g)."""

import datetime as dt

import pytest

from bookings import state_machine
from bookings.cancellation import (
    Case,
    NotACancellation,
    NotACancellationReason,
    Outcome,
    Party,
    cancellation_outcome,
    pro_rata_withdrawal_outcome,
    withdrawal_available,
    withdrawal_outcome,
    withdrawal_period_ends_at,
)
from core.errors import ErrorCode

UTC = dt.UTC
ACCEPTED = dt.datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
H = dt.timedelta(hours=1)
TICK = dt.timedelta(microseconds=1)
ENDS = withdrawal_period_ends_at(ACCEPTED)


def withdraw(
    *,
    amount: int = 3_000,
    slot_start: dt.datetime = ACCEPTED + 72 * H,
    now: dt.datetime = ACCEPTED + 2 * H,
    trader: bool = True,
    payment_requested: bool = False,
) -> Outcome | None:
    return withdrawal_outcome(
        amount_cents=amount,
        fee_cents=state_machine.service_fee_cents(amount),
        fee_percent=12,
        accepted_at=ACCEPTED,
        slot_start=slot_start,
        now=now,
        helper_is_trader=trader,
        payment_requested=payment_requested,
    )


def test_before_the_start_a_withdrawal_is_a_full_refund() -> None:
    outcome = withdraw()
    assert outcome is not None
    assert outcome.case == Case.WITHDRAWAL
    assert outcome.is_full_refund
    assert (outcome.refund_cents, outcome.transfer_reversal_cents, outcome.fee_refund_cents) == (
        3_360,
        3_360,
        360,
    )


def test_a_withdrawal_late_before_the_start_is_still_a_full_refund() -> None:
    outcome = withdraw(slot_start=ACCEPTED + 5 * H, now=ACCEPTED + 4 * H)
    assert outcome is not None and outcome.is_full_refund


@pytest.mark.parametrize("after", [dt.timedelta(0), H, 48 * H])
def test_at_or_after_the_start_support_works_out_the_share(after: dt.timedelta) -> None:
    start = ACCEPTED + 5 * H
    assert withdraw(slot_start=start, now=start + after) is None


def test_work_started_never_blocks_the_withdrawal_while_the_plain_cancel_is_refused() -> None:
    start = ACCEPTED + 5 * H
    now = start + H
    with pytest.raises(NotACancellation) as refused:
        cancellation_outcome(
            cancelled_by=Party.POSTER,
            no_fault_reason=None,
            amount_cents=3_000,
            fee_cents=360,
            fee_percent=12,
            accepted_at=ACCEPTED,
            slot_start=start,
            now=now,
            helper_is_trader=True,
        )
    assert refused.value.error_code == ErrorCode.WORK_STARTED
    assert withdrawal_available(
        helper_is_trader=True, accepted_at=ACCEPTED, now=now, payment_requested=False
    )
    assert withdraw(slot_start=start, now=now) is None


def test_a_private_helper_booking_has_no_withdrawal_from_the_task() -> None:
    assert not withdrawal_available(
        helper_is_trader=False, accepted_at=ACCEPTED, now=ACCEPTED + H, payment_requested=False
    )
    with pytest.raises(NotACancellation) as refused:
        withdraw(trader=False)
    assert refused.value.reason == NotACancellationReason.WITHDRAWAL_UNAVAILABLE
    assert refused.value.error_code == ErrorCode.WITHDRAWAL_NOT_AVAILABLE


def test_the_withdrawal_ends_with_the_period() -> None:
    assert withdrawal_available(
        helper_is_trader=True, accepted_at=ACCEPTED, now=ENDS - TICK, payment_requested=False
    )
    assert not withdrawal_available(
        helper_is_trader=True, accepted_at=ACCEPTED, now=ENDS, payment_requested=False
    )
    with pytest.raises(NotACancellation):
        withdraw(slot_start=ENDS + 72 * H, now=ENDS)


def test_a_requested_payment_ends_the_withdrawal() -> None:
    assert not withdrawal_available(
        helper_is_trader=True, accepted_at=ACCEPTED, now=ACCEPTED + H, payment_requested=True
    )
    with pytest.raises(NotACancellation) as refused:
        withdraw(payment_requested=True)
    assert refused.value.error_code == ErrorCode.BOOKING_ACTION_NOT_ALLOWED


@pytest.mark.parametrize(
    ("amount", "percent", "expected"),
    [
        # (R, C, K, X, Y): helper keeps C = percent of the price, TaskBeavo 12% of C.
        (3_000, 0, (3_360, 0, 0, 3_360, 360)),
        (3_000, 50, (1_680, 1_500, 180, 1_680, 180)),
        (3_000, 25, (2_520, 750, 90, 2_520, 270)),
        # €10 task, minimum fee: 99% done still refunds something.
        (1_000, 99, (141, 990, 119, 141, 131)),
        (10_000, 99, (112, 9_900, 1_188, 112, 12)),
        # Half-up rounding of the share: 33% of €10.01 is 330.33 → 330.
        (1_001, 33, (1_250 + 1 - 330 - 40, 330, 40, 1_250 + 1 - 330 - 40, 210)),
    ],
)
def test_pro_rata_split(amount: int, percent: int, expected: tuple[int, ...]) -> None:
    fee = state_machine.service_fee_cents(amount)
    outcome = pro_rata_withdrawal_outcome(
        amount_cents=amount, fee_cents=fee, fee_percent=12, work_done_percent=percent
    )
    assert outcome.case == Case.WITHDRAWAL_PRO_RATA
    assert outcome.work_done_percent == percent
    got = (
        outcome.refund_cents,
        outcome.helper_compensation_cents,
        outcome.kept_fee_cents,
        outcome.transfer_reversal_cents,
        outcome.fee_refund_cents,
    )
    assert got == expected
    assert outcome.refund_cents > 0 and outcome.fee_refund_cents > 0


@pytest.mark.parametrize("percent", [-1, 100, 101, 12.5, True])
def test_pro_rata_share_is_a_whole_percent_below_100(percent: object) -> None:
    with pytest.raises(ValueError):
        pro_rata_withdrawal_outcome(
            amount_cents=3_000,
            fee_cents=360,
            fee_percent=12,
            work_done_percent=percent,  # pyright: ignore[reportArgumentType]
        )


def test_a_pro_rata_outcome_must_match_its_share() -> None:
    good = pro_rata_withdrawal_outcome(
        amount_cents=3_000, fee_cents=360, fee_percent=12, work_done_percent=50
    )
    fields = {f: getattr(good, f) for f in good.__dataclass_fields__}
    with pytest.raises(ValueError):
        Outcome(**{**fields, "work_done_percent": 40})
    with pytest.raises(ValueError):
        Outcome(**{**fields, "case": Case.LATE})
