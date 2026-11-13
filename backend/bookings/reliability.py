"""A helper's reliability (PLAN.md, "TaskBeavo cancellation policy": "Helpers never pay
money penalties"; "Other edge cases": helper cancels or doesn't show up; BEAV-123).

A helper who cancels, doesn't show up, or whose no-fault reason support rejects pays
nothing: the poster's refund comes back from the charge and the EUR 5 credit from
TaskBeavo. The only consequence is a `HelperFault`, which lowers `reliability_score`,
and two faults within REVIEW_WINDOW open a `HelperReviewItem` for support. A person
decides anything further; nothing here blocks or suspends an account.

`reliability_score` is a working definition (NEEDS REVIEW, product): the share of the
helper's paid bookings in the last SCORE_WINDOW that didn't end in a helper fault.
"""

import datetime as dt

from django.db import IntegrityError, transaction

from accounts.models import User
from bookings.models import Booking, BookingEvent, HelperFault, HelperReviewItem
from core.models import ActorKind

REVIEW_FAULTS = 2
REVIEW_WINDOW = dt.timedelta(days=30)
SCORE_WINDOW = dt.timedelta(days=365)
FAULT_EVENT = "helper_fault_recorded"
REVIEW_EVENT = "helper_review_opened"

# Bookings whose money was confirmed held at some point: the ones a helper could let down.
_PAID = frozenset(
    {
        Booking.Status.FUNDS_HELD,
        Booking.Status.CANCEL_REQUESTED,
        Booking.Status.NO_ACCESS_REPORTED,
        Booking.Status.INCIDENT_PAUSED,
        Booking.Status.PAYMENT_REQUESTED,
        Booking.Status.DISPUTED,
        Booking.Status.RELEASED,
        Booking.Status.PAID_OUT,
        Booking.Status.PARTIALLY_SETTLED,
        Booking.Status.REFUNDED,
        Booking.Status.CLOSED,
    }
)


def record_fault(booking: Booking, kind: HelperFault.Kind, now: dt.datetime) -> HelperFault:
    """Count `booking`'s cancellation against its helper, inside the cancel transaction
    that locked it, and open a review item on the second fault within REVIEW_WINDOW.
    Idempotent per booking."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("A helper fault is recorded inside the cancel transaction.")
    fault, created = HelperFault.objects.get_or_create(
        booking=booking,
        defaults={"helper_id": booking.helper_id, "kind": kind, "created_at": now},
    )
    if not created:
        return fault
    BookingEvent.objects.create(
        booking=booking, type=FAULT_EVENT, actor_kind=ActorKind.SYSTEM, data={"kind": kind}
    )
    recent = list(
        HelperFault.objects.filter(
            helper_id=booking.helper_id, created_at__gt=now - REVIEW_WINDOW, created_at__lte=now
        ).order_by("created_at")
    )
    if len(recent) >= REVIEW_FAULTS:
        _open_review(booking, recent, now)
    return fault


def _open_review(booking: Booking, faults: list[HelperFault], now: dt.datetime) -> None:
    reason = HelperReviewItem.Reason.REPEATED_HELPER_CANCELS
    item = HelperReviewItem.objects.filter(
        helper_id=booking.helper_id, reason=reason, reviewed_at__isnull=True
    ).first()
    if item is None:
        try:
            with transaction.atomic():
                item = HelperReviewItem.objects.create(
                    helper_id=booking.helper_id, reason=reason, created_at=now
                )
        except IntegrityError:
            # Another cancel opened it at the same moment (helper_review_one_open).
            item = HelperReviewItem.objects.get(
                helper_id=booking.helper_id, reason=reason, reviewed_at__isnull=True
            )
        BookingEvent.objects.create(
            booking=booking,
            type=REVIEW_EVENT,
            actor_kind=ActorKind.SYSTEM,
            data={"review_item_id": item.pk, "faults": len(faults)},
        )
    item.faults.add(*faults)


def mark_reviewed(item: HelperReviewItem, staff: User, now: dt.datetime) -> None:
    """Support looked at the helper; a later fault pair opens a new item."""
    if item.reviewed_at is None:
        item.reviewed_at = now
        item.reviewed_by = staff
        item.save(update_fields=["reviewed_at", "reviewed_by"])


def reliability_score(helper: User, now: dt.datetime) -> int | None:
    """The percentage of `helper`'s paid bookings accepted in the last SCORE_WINDOW that
    didn't end in a helper fault, rounded down; None without any."""
    since = now - SCORE_WINDOW
    paid = Booking.objects.filter(helper=helper, created_at__gt=since, status__in=_PAID).count()
    if paid == 0:
        return None
    faults = HelperFault.objects.filter(helper=helper, booking__created_at__gt=since).count()
    return max(0, (paid - faults) * 100 // paid)
