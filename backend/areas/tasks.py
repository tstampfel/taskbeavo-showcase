from celery import shared_task

from areas import services


@shared_task(name="areas.purge_waitlist")
def purge_waitlist() -> int:
    """Beat task: delete waitlist entries past their purpose (areas/services.py)."""
    return services.purge_waitlist()
