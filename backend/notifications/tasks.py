from celery import shared_task
from django.conf import settings

from notifications import delivery


@shared_task(
    name="notifications.send",
    soft_time_limit=settings.NOTIFICATION_SEND_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.NOTIFICATION_SEND_SOFT_TIME_LIMIT_SECONDS + 30,
)
def send_delivery(delivery_id: int) -> str:
    """Send one push or email; on failure, schedule the retry `deliver` recorded.

    The Beat sweep is the safety net if this retry message is lost.
    """
    result, retry_at = delivery.deliver(delivery_id)
    if retry_at is not None:
        send_delivery.apply_async((delivery_id,), eta=retry_at)
    return result.value


@shared_task(name="notifications.sweep")
def sweep_deliveries() -> int:
    """Beat task: send pending deliveries whose commit hook or retry message never ran."""
    delivery_ids = delivery.due_delivery_ids()
    for delivery_id in delivery_ids:
        send_delivery.delay(delivery_id)
    return len(delivery_ids)


@shared_task(name="notifications.purge_finished")
def purge_finished_deliveries() -> int:
    """Beat task: delete old finished deliveries and the tokens of expired sessions."""
    return delivery.purge_finished()


@shared_task(
    name="notifications.check_push_receipts",
    soft_time_limit=settings.NOTIFICATION_SEND_SOFT_TIME_LIMIT_SECONDS,
)
def check_push_receipts() -> int:
    """Beat task: fetch Expo push receipts and delete the tokens of dead devices."""
    return delivery.check_receipts()
