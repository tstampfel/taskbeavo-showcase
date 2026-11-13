"""The transaction shape every state change uses (hard rule c).

    with locked_for_update(Booking.objects, booking_id) as booking:
        assert_transition_allowed(booking.status, NEW, actor)
        booking.status = NEW
        booking.save(update_fields=["status", "updated_at"])
        BookingEvent.objects.create(booking=booking, type="released", actor_kind=...)
        enqueue("booking.updated", aggregate=booking)

- The row lock (`SELECT ... FOR UPDATE`) stops a double tap, or a webhook racing a
  mutation, from passing the same check twice: the second caller waits for the first
  transaction to finish and then sees the new state.
- The audit row and the outbox row commit or roll back with the change, so a
  notification exists if and only if the change happened.
- Stripe calls happen outside this block, before or after it, with an idempotency key
  from `payments.idempotency.idempotency_key` (hard rule a). Holding a row lock across
  a network call would block every other change to that row.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.db import models, transaction


@contextmanager
def locked_for_update[M: models.Model](
    queryset: models.QuerySet[M] | models.Manager[M], pk: Any
) -> Iterator[M]:
    """Open a transaction and yield the row with `pk`, locked until the transaction ends.

    Raises the model's `DoesNotExist` if there is no such row. If the block raises, the
    whole transaction (change, audit row and outbox row) rolls back.
    """
    with transaction.atomic():
        yield queryset.select_for_update().get(pk=pk)
