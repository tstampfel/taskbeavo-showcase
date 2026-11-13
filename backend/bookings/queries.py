"""Read-only questions other apps ask about bookings."""

from django.db.models import Exists, OuterRef, Q, QuerySet

from accounts.models import User
from bookings.models import Booking, NoFaultRequest
from care_contacts.models import CareContact
from tasks.models import Task


def user_has_open_money_bookings(user: User) -> bool:
    """Whether `user`, as poster or helper, has a booking whose money isn't settled yet:
    paid for, held, requested, disputed, released but not paid out, or partially settled.
    Account deletion (BEAV-153) waits while this is true."""
    return Booking.objects.filter(
        Q(poster=user) | Q(helper=user), status__in=Booking.OPEN_MONEY
    ).exists()


def holding(bookings: QuerySet[Booking]) -> QuerySet[Booking]:
    """The bookings that still hold their task: not those whose task the poster re-opened
    after a helper or no-fault cancel (bookings/helper_cancel.py)."""
    return bookings.filter(task_reopened_at__isnull=True)


def active(bookings: QuerySet[Booking]) -> QuerySet[Booking]:
    """The active bookings among `bookings` (`Booking.is_active`): a cancel requested
    counts only while its no-fault request is still open."""
    pending = NoFaultRequest.objects.filter(booking=OuterRef("pk"), closed_at__isnull=True)
    return holding(
        bookings.filter(status__in=Booking.ACTIVE).filter(
            ~Q(status=Booking.Status.CANCEL_REQUESTED) | Q(Exists(pending))
        )
    )


def task_is_booked(task: Task) -> bool:
    """Whether an offer on `task` was accepted: a booking in AWAITING_PAYMENT or later. A
    failed checkout (PAYMENT_FAILED), or the poster re-opening the task after a helper or
    no-fault cancel, makes the task open again. A booked task takes no offers
    (offers.services) and keeps its details (tasks.services.update_task)."""
    booked = Booking.objects.filter(task=task).exclude(status=Booking.Status.PAYMENT_FAILED)
    return holding(booked).exists()


def active_booking_of_helper(task: Task, helper: User) -> Booking | None:
    """`helper`'s active booking on `task`, which lets them see the exact place (hard rule e)."""
    return active(Booking.objects.filter(task=task, helper=helper)).first()


def active_bookings_for_contact(contact: CareContact, *, lock: bool = False) -> list[Booking]:
    """The active bookings naming `contact`; with `lock`, row-locked for the transaction."""
    bookings: QuerySet[Booking] = active(Booking.objects.filter(care_contact=contact))
    if lock:
        bookings = bookings.select_for_update()
    return list(bookings)
