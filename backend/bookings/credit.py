"""Spending the poster's TaskBeavo credit (hard rules b and g; user decision 2026-11-07,
BEAV-123).

A `PosterCredit` (the EUR 5 for a helper's late cancel) comes off TaskBeavo's service fee
only, on the poster's later bookings: at acceptOffer the fee is reduced by
min(remaining credit, that booking's fee), and any rest carries over to the next one.
The money flow stays a destination charge: the PaymentIntent is the task price plus the
reduced fee, `application_fee_amount` is the reduced fee, the helper's amount never
changes, and nothing moves through TaskBeavo's bank account (hard rule b).

`take` locks the poster's credits and works out the split, `record` writes a
`PosterCreditUse` per credit in the booking's transaction, and `restore` gives all of the
credit back (user decision 2026-11-07) when the checkout fails (PAYMENT_FAILED), and after
every cancellation, withdrawal split and dispute split (`cancellation.credit_back_cents`);
it is spent only when the payment is released. Oldest credit first. No expiry for now
(lawyer question, BEAV-39).
"""

import datetime as dt
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Sum

from accounts.models import User
from bookings.models import Booking, BookingEvent, PosterCredit, PosterCreditUse
from core.models import ActorKind

USED = "poster_credit_used"
RESTORED = "poster_credit_restored"


@dataclass(frozen=True)
class Allocation:
    """How much of each credit a booking's fee takes, oldest first."""

    parts: tuple[tuple[int, int], ...] = ()

    @property
    def total_cents(self) -> int:
        return sum(cents for _, cents in self.parts)


def _remaining(credits: list[PosterCredit]) -> list[tuple[PosterCredit, int]]:
    used = dict(
        PosterCreditUse.objects.filter(credit__in=credits)
        .values("credit_id")
        .annotate(total=Sum(F("amount_cents") - F("restored_cents")))
        .values_list("credit_id", "total")
    )
    return [(c, c.amount_cents - (used.get(c.pk) or 0)) for c in credits]


def available_cents(poster: User) -> int:
    """The poster's unspent credit, without locks (for the screens)."""
    credits = list(PosterCredit.objects.filter(poster=poster))
    return sum(max(left, 0) for _, left in _remaining(credits))


def credit_for_fee(poster: User, fee_cents: int) -> int:
    """What the poster's credit would take off a fee of `fee_cents` now."""
    return min(available_cents(poster), fee_cents)


def take(poster: User, fee_cents: int) -> Allocation:
    """Lock the poster's credits and split up to `fee_cents` among them, oldest first.
    Inside the transaction that creates the booking; `record` writes the uses."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Credit is taken inside the booking's transaction.")
    credits = list(
        PosterCredit.objects.select_for_update().filter(poster=poster).order_by("granted_at", "pk")
    )
    parts: list[tuple[int, int]] = []
    due = fee_cents
    for credit, left in _remaining(credits):
        if due <= 0:
            break
        cents = min(left, due)
        if cents > 0:
            parts.append((credit.pk, cents))
            due -= cents
    return Allocation(tuple(parts))


def record(booking: Booking, allocation: Allocation, now: dt.datetime) -> None:
    """Write the uses `take` worked out, for the booking just created."""
    if not allocation.parts:
        return
    PosterCreditUse.objects.bulk_create(
        PosterCreditUse(credit_id=credit_id, booking=booking, amount_cents=cents, used_at=now)
        for credit_id, cents in allocation.parts
    )
    BookingEvent.objects.create(
        booking=booking,
        type=USED,
        actor_kind=ActorKind.POSTER,
        actor_id=booking.poster_id,
        created_at=now,
        data={"cents": allocation.total_cents},
    )


def restore(booking: Booking, now: dt.datetime, *, reason: str, cents: int | None = None) -> int:
    """Give back `cents` of the credit the booking used (all of it when None), once: a
    failed checkout, a full refund, or a cancellation settlement (`cents` from
    `cancellation.credit_back_cents`). `restored_at` makes a repeated webhook a no-op;
    `restored_cents` keeps the amount for the audit. Returns the cents restored."""
    uses = list(
        PosterCreditUse.objects.select_for_update()
        .filter(booking=booking, restored_at__isnull=True)
        .order_by("pk")
    )
    if not uses:
        return 0
    due = sum(use.amount_cents for use in uses) if cents is None else cents
    total = 0
    for use in uses:
        use.restored_cents = min(use.amount_cents, due - total)
        use.restored_at = now
        total += use.restored_cents
    PosterCreditUse.objects.bulk_update(uses, ["restored_cents", "restored_at"])
    if not total:
        return 0
    BookingEvent.objects.create(
        booking=booking,
        type=RESTORED,
        actor_kind=ActorKind.SYSTEM,
        created_at=now,
        data={"cents": total, "reason": reason},
    )
    return total


def used_cents(booking: Booking) -> int:
    """The credit still taken off `booking`'s fee (less what came back)."""
    return (
        PosterCreditUse.objects.filter(booking=booking).aggregate(
            total=Sum(F("amount_cents") - F("restored_cents"))
        )["total"]
        or 0
    )
