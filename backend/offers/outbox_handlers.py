"""Outbox handlers of the offers app (imported by OffersConfig.ready).

Each topic but `offer.withdrawn_registered_only` goes to the task's poster only: live to
their open subscriptions (`offerReceived`, `offerWithdrawn`, `offerExpiring` in
offers/schema.py, which re-check every event) and by push, never to the helper who acted.
The handler reloads the offer first: one deleted, or on a task that closed since the row
was written, sends nothing.

`offer.received`: a helper sent an offer. Posters with no app signed in (no live push
device) also get one email per offer, so web-only posters hear of it; the daily digest
(BEAV-178) replaces that email.

`offer.expiring`: an offer on the poster's task expires tomorrow. Like a new offer, posters
with no live push device also get it by email.

`offer.withdrawn`: the helper withdrew an offer. Push only. If the poster was paying for
it (the payload has `booking_id`; withdraw_offer already failed that checkout), the
PaymentIntent is cancelled, or refunded in full if it was paid meanwhile
(`bookings.checkout.settle_failed_checkout`, a Celery task outside every row lock).

`offer.withdrawn_registered_only`: TaskBeavo withdrew a private helper's offer because the
task's poster became a business poster (accounts.business). This one goes to the helper
by push (email if they have no app signed in), with the neutral reason; the poster, who
caused it, hears nothing.

`offer.updated`: the helper changed or renewed an offer. Live only, never a push or email:
it just keeps an open offers list showing the current price and expiry.

A business poster hears nothing about a private helper's offer, which they don't see
(`_hidden_from_poster`): say its helper left the BUSINESS status after sending it.

Pushes and emails say only that something happened (notifications/kinds.py): never the
offer's message, price or the helper's name.
"""

from django.utils import timezone

from core.models import Outbox
from core.outbox import register_handler
from core.realtime import send_to_users
from notifications.dispatch import notify
from offers.models import Offer
from offers.services import (
    EXPIRING_TOPIC,
    RECEIVED_TOPIC,
    UPDATED_TOPIC,
    WITHDRAWN_REGISTERED_ONLY_TOPIC,
    WITHDRAWN_TOPIC,
    visible_to_poster,
)
from tasks.models import Task


def _offer_on_open_task(row: Outbox) -> Offer | None:
    # None once the task or the helper was deleted (CASCADE) after the row was written.
    offer = Offer.objects.select_related("task").filter(pk=row.aggregate_id).first()
    if offer is None or offer.task.status != Task.Status.OPEN:
        return None
    return offer


def _hidden_from_poster(offer: Offer) -> bool:
    """A business poster doesn't see a private helper's offer (accounts.business), for
    instance one whose helper left the BUSINESS status: nothing about it reaches them."""
    queryset = Offer.objects.filter(pk=offer.pk)
    return not visible_to_poster(queryset, offer.task.poster).exists()


def _tell_poster(row: Outbox, offer: Offer, kind: str) -> None:
    if _hidden_from_poster(offer):
        return
    send_to_users(row, [offer.task.poster_id])
    notify(row, kind, [offer.task.poster_id], actor_id=offer.helper_id)


@register_handler(RECEIVED_TOPIC)
def announce_offer(row: Outbox) -> None:
    offer = _offer_on_open_task(row)
    # Withdrawn or expired before this ran: the poster hears of that instead, if anything.
    if offer is None or not offer.is_live:
        return
    _tell_poster(row, offer, "offer.received")


@register_handler(EXPIRING_TOPIC)
def remind_poster(row: Outbox) -> None:
    offer = _offer_on_open_task(row)
    if (
        offer is None
        or not offer.is_live
        or offer.expiry_reminder_queued_at is None
        or offer.expires_at <= timezone.now()
    ):
        return
    _tell_poster(row, offer, "offer.expiring")


@register_handler(WITHDRAWN_TOPIC)
def cancel_checkout(row: Outbox) -> None:
    """Ends the poster's payment for the offer, if they were in checkout, and tells them
    the offer was withdrawn. At-least-once: settling is idempotent and reloads the
    booking, and a checkout with no PaymentIntent yet is settled by acceptOffer itself."""
    if "booking_id" in row.payload:
        # Imported here: bookings.models imports this app's models.
        from bookings.checkout import settle_after_commit

        settle_after_commit(row.payload["booking_id"])
    offer = _offer_on_open_task(row)
    if offer is None or offer.status != Offer.Status.WITHDRAWN:
        return
    _tell_poster(row, offer, "offer.withdrawn")


@register_handler(UPDATED_TOPIC)
def refresh_poster_list(row: Outbox) -> None:
    offer = _offer_on_open_task(row)
    if offer is None or not offer.is_live or _hidden_from_poster(offer):
        return
    send_to_users(row, [offer.task.poster_id])


@register_handler(WITHDRAWN_REGISTERED_ONLY_TOPIC)
def tell_helper_registered_only(row: Outbox) -> None:
    # Unlike the poster's topics, the task may have closed since: the helper still hears why.
    offer = Offer.objects.filter(pk=row.aggregate_id).first()
    if (
        offer is None
        or offer.withdrawn_reason != Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY
    ):
        return
    notify(row, "offer.withdrawn_registered_only", [offer.helper_id])
