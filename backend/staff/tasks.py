from celery import shared_task

from staff import retention


@shared_task(name="staff.sweep_notes")
def sweep_notes() -> int:
    """Delete staff notes past their retention (staff/retention.py `sweep`)."""
    return retention.sweep()
