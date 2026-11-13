from celery import shared_task
from django.conf import settings

from core import outbox


@shared_task(name="core.ping")
def ping() -> str:
    """Smoke-test task: proves a worker is consuming from the broker."""
    return "pong"


@shared_task(
    name="core.outbox.publish",
    soft_time_limit=settings.OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS,
    time_limit=settings.OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS + 30,
)
def publish_outbox(row_id: int) -> str:
    """Publish one outbox row; on failure, schedule the retry `publish_row` recorded.

    The Beat sweep is the safety net if this retry message is lost.
    """
    result, retry_at = outbox.publish_row(row_id)
    if retry_at is not None:
        publish_outbox.apply_async((row_id,), eta=retry_at)
    return result.value


@shared_task(name="core.outbox.sweep")
def sweep_outbox() -> int:
    """Beat task: publish pending rows whose commit hook or retry message never ran."""
    row_ids = outbox.due_row_ids()
    for row_id in row_ids:
        publish_outbox.delay(row_id)
    return len(row_ids)
