from celery import shared_task

from vat import monitor


@shared_task(name="vat.check_thresholds")
def check_thresholds_task() -> int:
    """Beat task (daily): alert on any VAT threshold this year's service fees crossed."""
    return len(monitor.check_thresholds())
