"""From one outbox row to the push and email each recipient should get.

A topic's outbox handler decides who may receive the row (participants only), sends the
live event and then calls `notify`:

    @register_handler("booking.updated")
    def publish_booking_updated(row):
        booking = Booking.objects.get(pk=row.aggregate_id)
        send_to_users(row, [booking.poster_id])
        notify(row, "booking.payment_requested", [booking.poster_id], actor_id=booking.helper_id)

`notify` runs inside the outbox row's transaction. For each recipient it applies the
rules and writes one `Delivery` per channel:

- inactive users and the actor (nobody is notified of their own action) get nothing;
- push goes out unless the user switched the kind's category off for push; booking and
  payment messages can't be switched off;
- email follows the kind's `EmailRule` (always, only without a live push device, or
  never) and the user's email preference. A NO_PUSH_DEVICE push that finds no device at
  send time adds the email then (notifications/delivery.py).

The deliveries are sent by their own Celery tasks once the row's transaction commits
(notifications/delivery.py). A redelivered row finds its deliveries already written,
so nothing is sent twice.
"""

from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from core.models import Outbox
from notifications.devices import has_live_device
from notifications.kinds import KINDS, EmailRule, Kind
from notifications.models import Channel, Delivery
from notifications.preferences import is_enabled


def notify(
    row: Outbox, kind_key: str, user_ids: Iterable[int], *, actor_id: int | None = None
) -> list[Delivery]:
    """Write the push and email deliveries of `row` for `user_ids` and send them after commit.

    Returns the pending deliveries; on a redelivered row these are the ones not sent yet.
    """
    kind = KINDS[kind_key]
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("notify() must run inside the outbox row's transaction.")
    recipients = get_user_model().objects.filter(
        pk__in={int(user_id) for user_id in user_ids}, is_active=True
    )
    if actor_id is not None:
        recipients = recipients.exclude(pk=actor_id)

    wanted = [
        Delivery(outbox=row, user=user, kind=kind.key, channel=channel)
        for user in recipients.order_by("pk")
        for channel in _channels(kind, user)
    ]
    Delivery.objects.bulk_create(wanted, ignore_conflicts=True)
    created = list(
        Delivery.objects.filter(
            outbox=row,
            user__in=[delivery.user for delivery in wanted],
            status=Delivery.Status.PENDING,
        ).order_by("pk")
    )
    _send_after_commit(created)
    return created


def add_email_fallback(push: Delivery) -> Delivery | None:
    """Email instead, for a NO_PUSH_DEVICE kind whose push found no live device.

    Runs inside the push delivery's transaction; the unique (outbox, user, channel)
    constraint keeps it to one email however often it runs.
    """
    kind = KINDS[push.kind]
    if kind.email is not EmailRule.NO_PUSH_DEVICE:
        return None
    if not is_enabled(push.user, kind.category, Channel.EMAIL):
        return None
    email, created = Delivery.objects.get_or_create(
        outbox=push.outbox, user=push.user, channel=Channel.EMAIL, defaults={"kind": kind.key}
    )
    if created:
        _send_after_commit([email])
    return email


def _send_after_commit(deliveries: list[Delivery]) -> None:
    # notifications.tasks imports the delivery code, so it is looked up at call time.
    from notifications.tasks import send_delivery

    for delivery in deliveries:
        transaction.on_commit(lambda pk=delivery.pk: send_delivery.delay(pk))


def _channels(kind: Kind, user: AbstractBaseUser) -> list[Channel]:
    channels: list[Channel] = []
    if kind.push and is_enabled(user, kind.category, Channel.PUSH):
        channels.append(Channel.PUSH)
    if kind.email is EmailRule.NEVER:
        return channels
    if kind.push and kind.email is EmailRule.NO_PUSH_DEVICE and has_live_device(user):
        return channels
    if is_enabled(user, kind.category, Channel.EMAIL):
        channels.append(Channel.EMAIL)
    return channels
