"""Test tasks, posters and task photos."""

import datetime as dt
from typing import Any

from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user
from tasks import services
from uploads.models import Purpose, Status, Upload

GRUZ = (42.6610, 18.0850)
OLD_TOWN = (42.6407, 18.1077)
OSIJEK = (45.5550, 18.6955)
VIENNA = (48.2082, 16.3738)


def make_poster(name: str = "ana", **fields: Any) -> User:
    return make_user(name, phone_verified_at=timezone.now(), **fields)


def window(days: int = 3, hours: int = 2) -> tuple[dt.datetime, dt.datetime]:
    start = (timezone.now() + dt.timedelta(days=days)).replace(microsecond=0)
    return start, start + dt.timedelta(hours=hours)


def changes(**fields: Any) -> services.TaskChanges:
    """A complete, clean task in Gruž; override any field."""
    start, end = window()
    details: dict[str, Any] = {
        "category": "GARDEN",
        "title": "Mow the lawn",
        "description": "Mow the lawn and trim the hedge in front of the house.",
        "location": services.Location("Ulica Ante Starčevića 1, Dubrovnik", *GRUZ),
        "access_details": "Gate code 1234, key under the mat",
        "window_start": start,
        "window_end": end,
        "budget_cents": 3_500,
        **fields,
    }
    return services.TaskChanges(**details)


def ready_photo(owner: User, purpose: str = Purpose.TASK_PHOTO, **fields: Any) -> Upload:
    """A READY, unclaimed photo, as if it went through createUploadUrl and processing."""
    upload = Upload(
        owner=owner,
        purpose=purpose,
        status=Status.READY,
        content_type="image/jpeg",
        size_bytes=1_000,
        url_expires_at=timezone.now(),
        **fields,
    )
    upload.upload_key = f"users/{owner.pk}/{purpose}/{upload.pk}/upload"
    upload.display_key = f"users/{owner.pk}/{purpose}/{upload.pk}/display.jpg"
    upload.save()
    return upload
