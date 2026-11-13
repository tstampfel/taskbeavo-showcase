"""Cancellation amounts (hard rule g; docs/PLAN.md, "TaskBeavo cancellation policy
(revised, consumer-safe)"). Every cancellation amount is computed here and nowhere else.

Pure functions: no database, no Stripe. The caller (the cancel and no-access flows,
BEAV-122/123) passes the booking's facts and gets an `Outcome` with the amounts for each
Stripe step. All money is integer euro cents; every percentage rounds half up to a whole
cent with `bookings.money.percent_half_up`, like the service fee. At today's 12% K never
lands exactly on a half cent, so it doesn't depend on the tie rule; another rate may, and
then rounds half up like everything else.

Which row applies, checked in this order:

1. The helper already requested payment: not a cancellation (`NotACancellation`). The
   poster releases payment or opens a dispute.
2. The payment isn't confirmed (`funds_held` false, e.g. before acceptance or while
   checkout waits): `NOT_CHARGED`, nothing moves. A checkout that fails or is withdrawn
   is settled by `bookings.checkout`.
3. A no-fault reason (weather, hospitalisation, bereavement, emergency, both agree):
   `NO_FAULT`, full refund, whoever asked, before or after the start (the no-fault flow,
   BEAV-123, and support rely on it). Whether the reason stands is settled by the
   no-fault flow (BEAV-123), not by this module.
4. The poster cancels at or after the start (`now >= slot_start`) without a no-fault
   reason, with any helper, trader inside the 14 days included: refused
   (`NotACancellation`, `WORK_STARTED`). The poster opens a dispute or, with a trader
   helper inside its 14-day period, uses the withdrawal button (below). User decisions
   2026-11-04.
5. The helper cancels or doesn't show: `HELPER_CANCEL`, full refund, plus a €5 credit
   funded by TaskBeavo when under the late window. The helper never pays.
6. The poster cancels before the start:
   - within 1 h of accepting (exactly 1 h still counts) and before the start:
     `GRACE_PERIOD`, full refund;
   - with the late window or more to go (exactly 24 h is not late): `EARLY`, full refund;
   - under the late window, trader helper still inside its 14-day withdrawal period:
     `LATE_TRADER_WITHDRAWAL`, full refund;
   - otherwise `LATE`: the helper gets 25% of the price, at least €5, at most €20.

The withdrawal button (Directive 2023/2673, CRD Art. 11a; PLAN.md, "Poster uses the
withdrawal button"; BEAV-122) is a separate path that `WORK_STARTED` never blocks. It is
offered to the poster of a trader helper's booking while the 14-day period runs and until
the helper requests payment (`withdrawal_available`). Before the start it is a full
refund (`withdrawal_outcome`, `WITHDRAWAL`). At or after the start the poster's statement
is recorded and support enters the share of the work already done; the split for that
share is `pro_rata_withdrawal_outcome` (`WITHDRAWAL_PRO_RATA`): the helper keeps that
percentage of the price, TaskBeavo the booking's fee rate of it, the poster gets the rest.

The no-access call-out (`no_access_call_out`) is 25% of the price, at least €5, at most
€15, with no trader exception (PLAN.md: a risk to raise with the lawyer). Rescheduling
moves no money and a disputed call-out is decided by support, so neither is computed here.

In every case TaskBeavo keeps K = the booking's fee rate (`Booking.fee_percent`, stored at
acceptance; 12% today) of the helper's compensation C, even when the booking paid the
minimum fee (BEAV-106), and the poster gets the rest back. F is the fee actually charged:
the schedule's fee less any TaskBeavo credit the poster spent on it (`Booking.fee_cents`,
bookings/credit.py, BEAV-123), which may be 0. K doesn't depend on F (rule g, user decision
2026-11-07): on a credited booking TaskBeavo still keeps the full rate of C, and the credit
comes back to the poster (bookings/credit.py). The only bound is the charge itself: K is at
most A - C, what is left once the helper has C (`kept_fee_cents(C, rate, room=A - C)`), which
binds only when a credit lowered F and the helper keeps nearly the whole price.

The Stripe split (BEAV-106 handoff, `.reviews/BEAV-106-partial-refund-handoff.md`). On a
destination charge A = P + F, the helper's account starts with P and TaskBeavo with F:

- fee refund       Y = max(0, F - K)   (TaskBeavo → helper's account; never to the poster)
- transfer reversal X = R + max(0, K - F)   (helper → TaskBeavo)

When K > F (a credited booking), the part of K the application fee didn't cover comes back
from the helper's account with the reversal: Stripe can't raise an application fee after the
charge, and the reversal is the poster's money held by the helper, so nothing passes through
TaskBeavo's own account and the helper still ends with exactly C. Proven in the Stripe
sandbox for F = 0, F < K and F > K (`payments/sandbox/k_on_credited_charge.sh`).
- poster refund     R = A - C - K

run in that order, the helper ends with C, TaskBeavo with K and Stripe's net application
fee is K. A full refund is the same formula with C = K = 0. `Outcome` refuses any split
that breaks these, so a caller can't hand Stripe an inconsistent one.

Boundaries decided by the user on 2026-11-04 (BEAV-108).

Where the inputs come from (BEAV-122/123 must follow this; never from the client):

- `amount_cents`, `fee_cents`, `slot_start`: the locked booking row.
- `accepted_at`: when the poster accepted the offer (the booking's creation).
- `fee_percent`: the booking's service fee rate, stored at acceptance (`Booking.fee_percent`);
  K is always this percent, never the rate of a schedule in force later.
- `helper_is_trader`: the checkout snapshot the poster contracted on
  (`ConsentRecord.helper_is_trader`), never the helper's current profile, so a helper who
  registers an obrt later doesn't turn their late pay into a full refund.
- `no_fault_reason`: once the no-fault request stands, because the other side agreed or
  didn't reply within 48 h, or support upheld it after a disagreement; `None` otherwise.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from django.conf import settings

from bookings.money import percent_half_up
from core.errors import ErrorCode

# Tasks under €10 are refused at posting (PLAN.md), so a valid split always exists. Kept
# equal to `offers.services.MIN_PRICE_CENTS` by a test.
MIN_TASK_PRICE_CENTS = 1_000
GRACE_PERIOD = dt.timedelta(hours=1)
LATE_PAY_PERCENT = 25
LATE_PAY_MIN_CENTS = 500
LATE_PAY_MAX_CENTS = 2_000
CALL_OUT_PERCENT = 25
CALL_OUT_MIN_CENTS = 500
CALL_OUT_MAX_CENTS = 1_500
HELPER_CANCEL_CREDIT_CENTS = 500
WITHDRAWAL_PERIOD_DAYS = 14
WITHDRAWAL_TIME_ZONE = ZoneInfo("Europe/Zagreb")


class Party(StrEnum):
    POSTER = "POSTER"
    HELPER = "HELPER"


class NoFaultReason(StrEnum):
    """Reasons that make a cancellation nobody's fault: always a full refund."""

    WEATHER = "WEATHER"
    HOSPITALISATION = "HOSPITALISATION"
    BEREAVEMENT = "BEREAVEMENT"
    EMERGENCY = "EMERGENCY"
    MUTUAL_AGREEMENT = "MUTUAL_AGREEMENT"
    # The poster's free cancel after the helper reported running late: the helper's own
    # report counts as agreement (bookings/arrival.py, user decision 2026-11-08). Never
    # cited by hand (`no_fault.request_no_fault` refuses it).
    HELPER_LATE = "HELPER_LATE"


class NoFaultLabel(StrEnum):
    """What the other side, push, email, telemetry and booking_events see of a no-fault
    reason. Hospitalisation and bereavement can reveal health data (GDPR Art. 9), so the
    specific reason is support's only (user decision 2026-11-07). NEEDS REVIEW (lawyer)."""

    PERSONAL_EMERGENCY = "PERSONAL_EMERGENCY"
    WEATHER = "WEATHER"
    MUTUAL_AGREEMENT = "MUTUAL_AGREEMENT"
    HELPER_LATE = "HELPER_LATE"


def no_fault_label(reason: NoFaultReason) -> NoFaultLabel:
    if reason in (
        NoFaultReason.HOSPITALISATION,
        NoFaultReason.BEREAVEMENT,
        NoFaultReason.EMERGENCY,
    ):
        return NoFaultLabel.PERSONAL_EMERGENCY
    return NoFaultLabel(reason.value)


class Case(StrEnum):
    """The policy row that applied."""

    NOT_CHARGED = "NOT_CHARGED"
    GRACE_PERIOD = "GRACE_PERIOD"
    EARLY = "EARLY"
    LATE = "LATE"
    LATE_TRADER_WITHDRAWAL = "LATE_TRADER_WITHDRAWAL"
    HELPER_CANCEL = "HELPER_CANCEL"
    NO_FAULT = "NO_FAULT"
    NO_ACCESS_CALL_OUT = "NO_ACCESS_CALL_OUT"
    # The poster withdrew from a trader helper's contract before the start: full refund.
    WITHDRAWAL = "WITHDRAWAL"
    # ... at or after the start: support entered the share of the work already done.
    WITHDRAWAL_PRO_RATA = "WITHDRAWAL_PRO_RATA"


class NotACancellationReason(StrEnum):
    # The helper requested payment: the poster releases it or opens a dispute.
    PAYMENT_REQUESTED = "PAYMENT_REQUESTED"
    # The poster asked at or after the start: a dispute, or a withdrawal through support.
    WORK_STARTED = "WORK_STARTED"
    # No withdrawal right to use: a private helper, or the 14-day period is over.
    WITHDRAWAL_UNAVAILABLE = "WITHDRAWAL_UNAVAILABLE"


class NotACancellation(Exception):
    """The booking can't be cancelled; `reason` is an enum key and `error_code` the API
    error the caller answers with."""

    ERROR_CODES: Mapping[NotACancellationReason, ErrorCode] = {
        NotACancellationReason.PAYMENT_REQUESTED: ErrorCode.BOOKING_ACTION_NOT_ALLOWED,
        NotACancellationReason.WORK_STARTED: ErrorCode.WORK_STARTED,
        NotACancellationReason.WITHDRAWAL_UNAVAILABLE: ErrorCode.WITHDRAWAL_NOT_AVAILABLE,
    }

    def __init__(self, reason: NotACancellationReason) -> None:
        super().__init__(reason.value)
        self.reason = reason

    @property
    def error_code(self) -> ErrorCode:
        return self.ERROR_CODES[self.reason]


@dataclass(frozen=True, kw_only=True)
class Outcome:
    """What a cancellation moves, in cents. P = `amount_cents`, F = `fee_cents`,
    C = `helper_compensation_cents`, K = `kept_fee_cents`, R = `refund_cents`,
    X = `transfer_reversal_cents`, Y = `fee_refund_cents`. `poster_credit_cents` is the
    goodwill credit TaskBeavo funds itself, outside the charge. `work_done_percent` is
    the share support entered for a `WITHDRAWAL_PRO_RATA`, and None for every other case."""

    case: Case
    amount_cents: int
    fee_cents: int
    helper_compensation_cents: int
    kept_fee_cents: int
    # The booking's service fee rate (`Booking.fee_percent`, stored at acceptance): K is
    # this percent of C, so a later fee schedule never alters an existing booking.
    kept_fee_percent: int
    refund_cents: int
    transfer_reversal_cents: int
    fee_refund_cents: int
    poster_credit_cents: int
    work_done_percent: int | None = None

    def __post_init__(self) -> None:
        _check_price_and_fee(self.amount_cents, self.fee_cents)
        moved = (
            self.helper_compensation_cents,
            self.kept_fee_cents,
            self.refund_cents,
            self.transfer_reversal_cents,
            self.fee_refund_cents,
            self.poster_credit_cents,
        )
        if any(not isinstance(c, int) or isinstance(c, bool) or c < 0 for c in moved):
            raise ValueError("Every amount is a non-negative whole number of cents.")
        if self.case == Case.NOT_CHARGED:
            if any(moved):
                raise ValueError("Nothing moves when nothing was charged.")
            return
        if (self.case == Case.WITHDRAWAL_PRO_RATA) != (self.work_done_percent is not None):
            raise ValueError("Only a pro rata withdrawal has a share of the work done.")
        c, k = self.helper_compensation_cents, self.kept_fee_cents
        if c != _compensation(self.case, self.amount_cents, self.work_done_percent):
            raise ValueError("The compensation doesn't match the case.")
        if self.poster_credit_cents and (
            self.case != Case.HELPER_CANCEL
            or self.poster_credit_cents != HELPER_CANCEL_CREDIT_CENTS
        ):
            raise ValueError("Only a helper's late cancel earns the poster the €5 credit.")
        if k != kept_fee_cents(c, self.kept_fee_percent, room=self.charge_cents - c):
            raise ValueError("TaskBeavo keeps the booking's fee rate of the compensation.")
        if self.refund_cents + c + k != self.charge_cents:
            raise ValueError("The refund, compensation and kept fee must add up to the charge.")
        if self.transfer_reversal_cents != self.refund_cents + max(0, k - self.fee_cents):
            # The whole refund comes back from the helper, plus the part of K the fee
            # didn't cover (hard rule b): never paid from TaskBeavo's own balance.
            raise ValueError("The reversal is the refund plus K beyond the fee charged.")
        if self.fee_refund_cents != max(0, self.fee_cents - k):
            raise ValueError("The fee refund is the fee minus what TaskBeavo keeps.")

    @property
    def charge_cents(self) -> int:
        """What the poster paid (A = P + F), or 0 when nothing was charged."""
        return (
            0 if self.case == Case.NOT_CHARGED else charged_cents(self.amount_cents, self.fee_cents)
        )

    @property
    def is_full_refund(self) -> bool:
        return self.case != Case.NOT_CHARGED and self.refund_cents == self.charge_cents


def cancellation_outcome(
    *,
    cancelled_by: Party,
    no_fault_reason: NoFaultReason | None,
    amount_cents: int,
    fee_cents: int,
    fee_percent: int,
    accepted_at: dt.datetime,
    slot_start: dt.datetime,
    now: dt.datetime,
    helper_is_trader: bool,
    funds_held: bool = True,
    payment_requested: bool = False,
) -> Outcome:
    """The outcome of a poster's, helper's or no-fault cancellation (module docstring)."""
    _check_price_and_fee(amount_cents, fee_cents)
    _check_aware(accepted_at=accepted_at, slot_start=slot_start, now=now)
    if payment_requested:
        raise NotACancellation(NotACancellationReason.PAYMENT_REQUESTED)
    if now < accepted_at:
        raise ValueError("now is before the offer was accepted.")
    if not funds_held:
        return _outcome(Case.NOT_CHARGED, amount_cents, fee_cents, fee_percent, 0)

    late = slot_start - now < late_cancel_window()
    if no_fault_reason is not None:
        return _outcome(Case.NO_FAULT, amount_cents, fee_cents, fee_percent, 0)
    if cancelled_by == Party.POSTER and now >= slot_start:
        raise NotACancellation(NotACancellationReason.WORK_STARTED)
    if cancelled_by == Party.HELPER:
        credit = HELPER_CANCEL_CREDIT_CENTS if late else 0
        return _outcome(Case.HELPER_CANCEL, amount_cents, fee_cents, fee_percent, 0, credit=credit)
    if now - accepted_at <= GRACE_PERIOD:
        return _outcome(Case.GRACE_PERIOD, amount_cents, fee_cents, fee_percent, 0)
    if not late:
        return _outcome(Case.EARLY, amount_cents, fee_cents, fee_percent, 0)
    if helper_is_trader and now < withdrawal_period_ends_at(accepted_at):
        return _outcome(Case.LATE_TRADER_WITHDRAWAL, amount_cents, fee_cents, fee_percent, 0)
    return _outcome(
        Case.LATE, amount_cents, fee_cents, fee_percent, _compensation(Case.LATE, amount_cents)
    )


def no_access_call_out(
    *,
    amount_cents: int,
    fee_cents: int,
    fee_percent: int,
    payment_requested: bool = False,
) -> Outcome:
    """The call-out the poster accepted, or that was accepted after 48 h without a reply."""
    _check_price_and_fee(amount_cents, fee_cents)
    if payment_requested:
        raise NotACancellation(NotACancellationReason.PAYMENT_REQUESTED)
    case = Case.NO_ACCESS_CALL_OUT
    compensation = _compensation(case, amount_cents)
    return _outcome(case, amount_cents, fee_cents, fee_percent, compensation)


def withdrawal_available(
    *,
    helper_is_trader: bool,
    accepted_at: dt.datetime,
    now: dt.datetime,
    payment_requested: bool,
) -> bool:
    """Whether the poster may use the withdrawal button now: a trader helper's booking,
    inside its 14-day period, before the helper requested payment (CRD Art. 16(a): the
    right ends once the service is fully performed). Before or after the start alike."""
    _check_aware(accepted_at=accepted_at, now=now)
    return (
        helper_is_trader and not payment_requested and now < withdrawal_period_ends_at(accepted_at)
    )


def withdrawal_outcome(
    *,
    amount_cents: int,
    fee_cents: int,
    fee_percent: int,
    accepted_at: dt.datetime,
    slot_start: dt.datetime,
    now: dt.datetime,
    helper_is_trader: bool,
    payment_requested: bool = False,
) -> Outcome | None:
    """The poster's withdrawal from a trader helper's contract (module docstring).

    Before the start: a full refund (`WITHDRAWAL`). At or after the start: None, meaning
    the statement is recorded and support enters the share of the work done for
    `pro_rata_withdrawal_outcome`. Raises `NotACancellation` when there is no withdrawal
    right to use (`WITHDRAWAL_UNAVAILABLE`) or the helper requested payment.

    NEEDS REVIEW (lawyer, BEAV-39): whether this flow meets the withdrawal function rules.
    """
    _check_price_and_fee(amount_cents, fee_cents)
    _check_aware(accepted_at=accepted_at, slot_start=slot_start, now=now)
    if payment_requested:
        raise NotACancellation(NotACancellationReason.PAYMENT_REQUESTED)
    if now < accepted_at:
        raise ValueError("now is before the offer was accepted.")
    if not withdrawal_available(
        helper_is_trader=helper_is_trader,
        accepted_at=accepted_at,
        now=now,
        payment_requested=payment_requested,
    ):
        raise NotACancellation(NotACancellationReason.WITHDRAWAL_UNAVAILABLE)
    if now >= slot_start:
        return None
    return _outcome(Case.WITHDRAWAL, amount_cents, fee_cents, fee_percent, 0)


def pro_rata_withdrawal_outcome(
    *, amount_cents: int, fee_cents: int, fee_percent: int, work_done_percent: int
) -> Outcome:
    """The split of a withdrawal after the start, for the share of the work support
    entered (a whole percent, 0 to 99; a fully done job is released, not withdrawn).
    The helper keeps that share of the price, half up, TaskBeavo the booking's fee rate of
    it."""
    _check_price_and_fee(amount_cents, fee_cents)
    _check_work_done_percent(work_done_percent)
    case = Case.WITHDRAWAL_PRO_RATA
    compensation = _compensation(case, amount_cents, work_done_percent)
    return _outcome(
        case, amount_cents, fee_cents, fee_percent, compensation, work_done=work_done_percent
    )


def charged_cents(amount_cents: int, fee_cents: int) -> int:
    """What the poster paid for a held booking: A = P + F."""
    return amount_cents + fee_cents


def late_pay_cents(amount_cents: int) -> int:
    """25% of the price, at least €5, at most €20 (both bounds inclusive)."""
    return _capped_percent(amount_cents, LATE_PAY_PERCENT, LATE_PAY_MIN_CENTS, LATE_PAY_MAX_CENTS)


def call_out_cents(amount_cents: int) -> int:
    """25% of the price, at least €5, at most €15 (both bounds inclusive)."""
    return _capped_percent(amount_cents, CALL_OUT_PERCENT, CALL_OUT_MIN_CENTS, CALL_OUT_MAX_CENTS)


def kept_fee_cents(compensation_cents: int, fee_percent: int, *, room: int | None = None) -> int:
    """K: the booking's service fee rate (`Booking.fee_percent`, 12% today) of the
    helper's compensation, which TaskBeavo keeps, whatever fee was charged. `room` is what
    is left of the charge once the helper has C; K never exceeds it (module docstring)."""
    kept = percent_half_up(compensation_cents, fee_percent)
    return kept if room is None else min(kept, room)


def credit_back_cents(outcome: Outcome, credit_cents: int) -> int:
    """The part of the TaskBeavo credit a booking used (`credit_cents`) that comes back
    with `outcome`: all of it, after every cancellation, withdrawal split and dispute
    split (rule g, user decision 2026-11-07). Where TaskBeavo keeps K, it takes it in cash
    through the transfer reversal, so keeping part of the credit too would charge the
    poster twice. A credit is spent only when the payment is released."""
    if credit_cents < 0:
        raise ValueError("The credit must be non-negative cents.")
    return 0 if outcome.case == Case.NOT_CHARGED else credit_cents


def late_cancel_window() -> dt.timedelta:
    """Under this long before the start a poster's cancel is late and a helper's earns
    the poster the credit; exactly this long is not late."""
    return dt.timedelta(hours=settings.LATE_CANCEL_WINDOW_HOURS)


def withdrawal_period_ends_at(accepted_at: dt.datetime) -> dt.datetime:
    """When a trader booking's 14-day withdrawal period ends: 24:00 Europe/Zagreb at the
    end of the 14th calendar day after the day of acceptance, which isn't counted
    (Regulation 1182/71 day counting). A cancel before this instant is inside it.

    NEEDS REVIEW (lawyer): the counting rule, the Zagreb time zone, whether the period
    runs from acceptance or from the payment confirmation, and Regulation 1182/71
    Art. 3(4): a period whose last day is a Saturday, Sunday or public holiday ends at
    the end of the next working day. Not applied here (user decision 2026-11-04, and
    PLAN.md doesn't require it): accepted Saturday 7 Nov 2026, the period ends at 00:00
    on Sunday 22 Nov, while with Art. 3(4) it would run to the end of Monday 23 Nov.
    """
    _check_aware(accepted_at=accepted_at)
    day = accepted_at.astimezone(WITHDRAWAL_TIME_ZONE).date()
    last = day + dt.timedelta(days=WITHDRAWAL_PERIOD_DAYS + 1)
    return dt.datetime.combine(last, dt.time(0), tzinfo=WITHDRAWAL_TIME_ZONE)


def _outcome(
    case: Case,
    amount_cents: int,
    fee_cents: int,
    fee_percent: int,
    compensation_cents: int,
    *,
    credit: int = 0,
    work_done: int | None = None,
) -> Outcome:
    _check_fee_percent(fee_percent)
    percent = fee_percent
    if case == Case.NOT_CHARGED:
        kept = refund = fee_refund = 0
    else:
        charge = charged_cents(amount_cents, fee_cents)
        kept = kept_fee_cents(compensation_cents, percent, room=charge - compensation_cents)
        refund = charge - compensation_cents - kept
        fee_refund = max(0, fee_cents - kept)
    return Outcome(
        case=case,
        amount_cents=amount_cents,
        fee_cents=fee_cents,
        helper_compensation_cents=compensation_cents,
        kept_fee_cents=kept,
        kept_fee_percent=percent,
        refund_cents=refund,
        transfer_reversal_cents=refund + max(0, kept - fee_cents),
        fee_refund_cents=fee_refund,
        poster_credit_cents=credit,
        work_done_percent=work_done,
    )


def _compensation(case: Case, amount_cents: int, work_done_percent: int | None = None) -> int:
    """C for a case: late pay, call-out pay, the pro rata share, or 0 for every full
    refund."""
    if case == Case.WITHDRAWAL_PRO_RATA:
        _check_work_done_percent(work_done_percent)
        return percent_half_up(amount_cents, work_done_percent)  # pyright: ignore[reportArgumentType]
    if case == Case.LATE:
        return late_pay_cents(amount_cents)
    if case == Case.NO_ACCESS_CALL_OUT:
        return call_out_cents(amount_cents)
    return 0


def _capped_percent(cents: int, percent: int, low: int, high: int) -> int:
    return min(max(percent_half_up(cents, percent), low), high)


def _check_price_and_fee(amount_cents: int, fee_cents: int) -> None:
    for name, value in (("amount_cents", amount_cents), ("fee_cents", fee_cents)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be whole cents.")
    if amount_cents < MIN_TASK_PRICE_CENTS:
        raise ValueError("The task price is below the €10 minimum.")
    # 0 when the poster's credit covered the whole fee (bookings/credit.py).
    if not 0 <= fee_cents < amount_cents:
        raise ValueError("The fee can't be negative and must be below the task price.")


def _check_fee_percent(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 100:
        raise ValueError("The booking's fee rate is a whole percent between 0 and 100.")


def _check_work_done_percent(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 100:
        raise ValueError("The share of the work done is a whole percent from 0 to 99.")


def _check_aware(**moments: dt.datetime) -> None:
    for name, moment in moments.items():
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"{name} needs a time zone.")
