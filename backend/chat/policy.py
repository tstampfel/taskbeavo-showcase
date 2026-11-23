"""Which stage a conversation is in, and whether its participants may write (trust-safety
skill section 2; docs/PLAN.md, "Posting a task and handling offers").

- OFFER, before acceptance: the helper has an offer on the task, nobody has booked yet.
  Every message passes the contact-detail filter, so the two can't take the deal off the
  platform. Writing needs the helper's offer to be live (SENT or NEEDS_UPDATE, not past
  its expiry) on a task that is still open and not booked, and visible to the poster (a
  business poster doesn't see private helpers' offers, accounts.business). A pair whose
  booking waits for the payment (AWAITING_PAYMENT) is still here: until Stripe confirms
  it, the acceptance can fail and the task re-open.
- BOOKED, after acceptance: the pair's booking was paid and still holds the task. Messages
  are not filtered; the two coordinate access, timing and the work. Writing stays open
  while the booking is in progress (CHAT_OPEN, which includes a dispute and the release
  before the payout lands); once it is settled or closed the thread is read-only, as the
  past-task summary shows it. Users' own phone numbers are still never exposed by the API
  (hard rule e); only what they choose to write is shown.

A booking whose task the poster re-opened after a cancel no longer counts: the pair is back
to OFFER, and can write only if the helper offers again.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from django.utils import timezone

from bookings.models import Booking
from bookings.queries import holding, task_is_booked
from chat.models import Conversation
from offers.models import Offer
from offers.services import visible_to_poster
from tasks.models import Task


class Stage(StrEnum):
    OFFER = "OFFER"
    BOOKED = "BOOKED"


@dataclass(frozen=True)
class ConversationState:
    stage: Stage
    can_send: bool
    booking: Booking | None

    # Booking statuses in which the booked pair can still write.
    CHAT_OPEN: ClassVar = (
        Booking.Status.FUNDS_HELD,
        Booking.Status.CANCEL_REQUESTED,
        Booking.Status.NO_ACCESS_REPORTED,
        Booking.Status.INCIDENT_PAUSED,
        Booking.Status.PAYMENT_REQUESTED,
        Booking.Status.DISPUTED,
        Booking.Status.RELEASED,
    )

    @property
    def filtered(self) -> bool:
        """Messages written now pass the contact-detail filter."""
        return self.stage == Stage.OFFER


def state_of(conversation: Conversation) -> ConversationState:
    """The conversation's stage and whether it takes messages, read now."""
    booking = (
        holding(
            Booking.objects.filter(
                task_id=conversation.task_id, helper_id=conversation.helper_id
            ).exclude(status=Booking.Status.PAYMENT_FAILED)
        )
        .order_by("-created_at")
        .first()
    )
    if booking is not None and booking.status != Booking.Status.AWAITING_PAYMENT:
        return ConversationState(
            Stage.BOOKED, booking.status in ConversationState.CHAT_OPEN, booking
        )
    if booking is not None:
        # The poster accepted this helper's offer and is paying for it.
        return ConversationState(Stage.OFFER, True, booking)
    return ConversationState(Stage.OFFER, _offer_open(conversation), None)


def _offer_open(conversation: Conversation) -> bool:
    task = conversation.task
    if task.status != Task.Status.OPEN or task_is_booked(task):
        return False
    offers = Offer.objects.filter(
        task=task,
        helper_id=conversation.helper_id,
        status__in=Offer.LIVE,
        expires_at__gt=timezone.now(),
    )
    return visible_to_poster(offers, task.poster).exists()
