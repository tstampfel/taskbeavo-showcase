"""Tasks posters post (docs/PLAN.md, "Posting a task and handling offers").

A task starts as a DRAFT (the wizard saves as it goes; every field may be empty), is
published to OPEN, or to PENDING_REVIEW when the content screening holds it
(tasks/services.py), and ends CLOSED. Offers, bookings and expiry add their own states
in later tickets.

Personal data and who sees it (hard rule e):
- `address`, `location` and `access_details` (doorbell, floor, gate code, key) are the
  exact place. The Task API shows them to the poster and to the helper of the task's
  active booking (`bookings.models.Booking.ACTIVE`); everyone else, and that helper once
  the booking closes, sees `approximate_location` and the service area's name.
  `access_details` is encrypted at rest.
- `care_contact` is the on-site contact; its phone follows `care_contacts.policy`.
- Deleting the poster deletes their tasks (CASCADE); `tasks.gdpr` holds the export.
"""

import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.gis.db import models

from areas.models import ServiceArea
from care_contacts.models import CareContact
from core.categories import TaskCategory
from core.crypto import EncryptedTextField
from uploads.models import Upload


class Task(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        # Published, but the screening held it until staff look at it (BEAV-138).
        PENDING_REVIEW = "PENDING_REVIEW"
        OPEN = "OPEN"
        CLOSED = "CLOSED"

    class HoldReason(models.TextChoices):
        # The contact-detail filter found a doubtful match (verdict NEEDS_REVIEW).
        CONTACT_DETAILS = "CONTACT_DETAILS"
        # The text reads like a ride, medical or personal care (tasks/categories.py).
        EXCLUDED_CATEGORY = "EXCLUDED_CATEGORY"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poster = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks"
    )
    # RESTRICT: a contact can't be deleted on its own while a task names it, but deleting
    # the poster's account removes both (care_contacts.services.delete_contact).
    care_contact = models.ForeignKey(
        CareContact,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="tasks",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    category = models.CharField(max_length=20, choices=TaskCategory.choices, blank=True)
    # As stored and shown: the contact-detail filter's output (masked where it matched).
    title = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    # The exact place, shown to the poster only.
    address = models.CharField(max_length=200, blank=True)
    location = models.PointField(srid=4326, null=True, blank=True)
    # What everyone else sees: the location snapped to a grid of about a kilometre.
    approximate_location = models.PointField(srid=4326, null=True, blank=True)
    # The active area that covered `location` when it was last checked.
    service_area = models.ForeignKey(
        ServiceArea, on_delete=models.PROTECT, null=True, blank=True, related_name="tasks"
    )
    access_details = EncryptedTextField(blank=True)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    # Any day suits; the task stays open 30 days (renewal and expiry: a later ticket).
    flexible_date = models.BooleanField(default=False)
    budget_cents = models.PositiveIntegerField(null=True, blank=True)
    # The last screening of title and description: the filter's verdict and what it found.
    moderation_verdict = models.CharField(max_length=20, blank=True)
    moderation_kinds = models.JSONField(default=list, blank=True)
    hold_reasons = models.JSONField(default=list, blank=True)
    photos = models.ManyToManyField(Upload, through="TaskPhoto", related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tasks"
        ordering: ClassVar = ["-created_at"]
        indexes: ClassVar = [
            models.Index(fields=["poster", "status"], name="task_poster_status"),
            models.Index(fields=["status", "service_area"], name="task_status_area"),
        ]

    def __str__(self) -> str:
        # No title or address: __str__ ends up in logs and admin history.
        return f"Task {self.pk}"


class TaskPhoto(models.Model):
    """A task's photo, in the order the poster chose. Each upload belongs to one task."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="task_photos")
    upload = models.OneToOneField(Upload, on_delete=models.CASCADE, related_name="task_photo")
    position = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "task_photos"
        ordering: ClassVar = ["position"]

    def __str__(self) -> str:
        return f"TaskPhoto {self.pk}"
