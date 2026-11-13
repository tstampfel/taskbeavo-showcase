from celery import shared_task

from offers import services


@shared_task(name="offers.expire")
def expire_offers() -> int:
    """Beat task: mark offers past their expiry EXPIRED."""
    return services.expire_offers()


@shared_task(name="offers.queue_expiry_reminders")
def queue_offer_expiry_reminders() -> int:
    """Beat task: queue the poster's reminder a day before an offer expires."""
    return services.queue_expiry_reminders()
