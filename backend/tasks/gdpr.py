"""Tasks in the poster's account deletion and data export (plan: "GDPR flows").

Deleting the `User` row cascades to their tasks and task photos; `Task.care_contact` is
RESTRICT, which lets the same cascade remove the poster's on-site contacts.

TODO(BEAV-153): include `export_tasks` in "Download my data". Once bookings exist, tasks
that became bookings fall under the accounting retention instead of being deleted.
"""

from typing import Any

from accounts.models import User
from tasks.models import Task


def export_tasks(poster: User) -> list[dict[str, Any]]:
    """`poster`'s tasks in full, for their data export."""
    return [
        {
            "id": str(task.pk),
            "status": task.status,
            "category": task.category,
            "title": task.title,
            "description": task.description,
            "address": task.address,
            "location": None if task.location is None else [task.location.y, task.location.x],
            "access_details": task.access_details,
            "care_contact_id": task.care_contact_id,
            "window_start": task.window_start.isoformat() if task.window_start else None,
            "window_end": task.window_end.isoformat() if task.window_end else None,
            "flexible_date": task.flexible_date,
            "budget_cents": task.budget_cents,
            "photo_ids": [str(photo.upload_id) for photo in task.task_photos.all()],
            "created_at": task.created_at.isoformat(),
            "published_at": task.published_at.isoformat() if task.published_at else None,
            "closed_at": task.closed_at.isoformat() if task.closed_at else None,
        }
        for task in Task.objects.filter(poster=poster)
    ]
