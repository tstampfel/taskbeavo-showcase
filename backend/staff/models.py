"""The staff console's own records: the action audit log and staff notes (BEAV-156).

Staff (support, finance, trust; `staff.roles`) work in the Django admin and see personal
data there: exact addresses, access details, photos, chat. Every read of a booking's
evidence and every staff action writes a `StaffAuditEvent`, so it can be shown later who
looked at what and when. The log holds IDs and enum keys only, never the data that was
seen (hard rule e, `core.payloads.check_ids_only`).
"""

from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.crypto import EncryptedTextField
from core.models import ActorKind, AuditEvent


class StaffAction(models.TextChoices):
    """What a staff member did. Later tickets add their own actions here."""

    VIEW_BOOKING_EVIDENCE = "VIEW_BOOKING_EVIDENCE", _("Viewed a booking's evidence")
    VIEW_USER = "VIEW_USER", _("Viewed a user's account page")
    REVEAL_TAX_IDS = "REVEAL_TAX_IDS", _("Revealed a helper's OIB, IBAN or VAT ID")
    ADD_NOTE = "ADD_NOTE", _("Added a staff note")
    ADMIN_ADD = "ADMIN_ADD", _("Created a record in the admin")
    ADMIN_CHANGE = "ADMIN_CHANGE", _("Changed a record in the admin")
    ADMIN_DELETE = "ADMIN_DELETE", _("Deleted a record in the admin")
    ADMIN_ACTION = "ADMIN_ACTION", _("Ran an admin action")
    # System, not staff: retention (staff.retention).
    NOTE_PURGED = "NOTE_PURGED", _("Deleted a staff note at the end of its retention")
    NOTE_REDACTED = "NOTE_REDACTED", _("Redacted a staff note after an account deletion")


class StaffAuditEvent(AuditEvent):
    """One staff read or action. Append-only like every `AuditEvent`.

    `type` is a `StaffAction`, `actor` the staff user (`actor_kind` ADMIN). The object is
    named by `object_type` (an "app_label.model" key) and `object_id`, not a foreign key,
    so one log covers bookings, users, helper profiles and the objects later tickets add,
    and the row outlives the object. `data` holds IDs and enum keys only: which sections
    were shown, which fields were revealed or changed, never their values.

    Kept like the accounting records it explains (hard rule h); the actor link is cleared
    when a staff account is deleted.
    """

    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)

    class Meta(AuditEvent.Meta):
        db_table = "staff_audit_events"
        verbose_name = _("staff audit event")
        verbose_name_plural = _("staff audit events")
        indexes: ClassVar = [
            models.Index(fields=["object_type", "object_id"], name="staff_audit_object"),
            models.Index(fields=["actor", "created_at"], name="staff_audit_actor"),
        ]

    def __str__(self) -> str:
        return f"StaffAuditEvent:{self.pk} {self.type} {self.object_type}:{self.object_id}"


class StaffNoteQuerySet(models.QuerySet["StaffNote"]):
    # Retention changes notes through `StaffNote._base_manager` (staff.retention).
    def update(self, **kwargs: Any) -> NoReturn:
        raise ValueError("Staff notes are append-only.")

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ValueError("Staff notes are append-only.")


class StaffNote(models.Model):
    """A note staff keep on an object (a booking now; users, incidents and reports later),
    named like `StaffAuditEvent` by `object_type` and `object_id`.

    A restricted note may hold health details from a safety incident (plan: "Safety
    incidents"; GDPR Art. 9), so only the trust role reads or writes it
    (`staff.roles.Capability.RESTRICTED_NOTES`); other staff don't see that it exists,
    and audit rows about notes name the note alone, never its booking or `restricted`.
    The text is encrypted at rest and never logged.

    Append-only for staff: the admin can't edit or delete a note; a correction is a new
    note. Only the system path in `staff.retention` changes a note (user decision
    2026-11-04, hard rule h), the way BEAV-63 keeps evidence photos:

    - `delete_after`: the booking's close + UPLOAD_EVIDENCE_RETENTION_DAYS (set by the
      sweep), or a lifted hold's resolution + UPLOAD_LEGAL_HOLD_RETENTION_YEARS. The
      `staff.sweep_notes` Beat task deletes the note after it.
    - `legal_hold`: an open dispute, chargeback or claim; a held note is never deleted
      or redacted.
    - `redacted_at`: account deletion emptied the body (`staff.retention.redact_notes_about`).

    Restricted notes follow the same rule. TODO(BEAV-141): incidents may need their own
    period. Deleting a staff account clears `author` (SET_NULL).
    """

    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Empty once redacted.
    body = EncryptedTextField(blank=True)
    restricted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    delete_after = models.DateTimeField(null=True, blank=True)
    legal_hold = models.BooleanField(default=False)
    redacted_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[models.Manager["StaffNote"]] = StaffNoteQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        db_table = "staff_notes"
        ordering: ClassVar = ["created_at", "id"]
        verbose_name = _("staff note")
        verbose_name_plural = _("staff notes")
        indexes: ClassVar = [
            models.Index(fields=["object_type", "object_id"], name="staff_note_object"),
            models.Index(fields=["delete_after"], name="staff_note_delete_after"),
        ]

    def __str__(self) -> str:
        # No body: __str__ ends up in logs and admin history.
        return f"StaffNote {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Staff notes are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValueError("Staff notes are append-only.")


class StaffAccess(models.Model):
    """No rows: carries the custom permissions behind the staff roles (`staff.roles`).

    Django needs a model to hang permissions on. The groups get them in migration 0002."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions: ClassVar = [
            ("view_exact_place", _("See exact addresses and access details")),
            ("view_chat", _("Read booking chat")),
            ("view_photos", _("See task and evidence photos")),
            ("view_stripe_ids", _("See Stripe IDs and payout account status")),
            ("view_contact_phone", _("See the on-site contact's phone number")),
            ("reveal_tax_ids", _("Reveal a helper's OIB, IBAN and VAT ID")),
            ("restricted_notes", _("Read and write restricted notes")),
            (
                "view_no_fault_detail",
                _("See the specific health-revealing reason of a cancellation"),
            ),
        ]

    def __str__(self) -> str:
        return "StaffAccess"


__all__ = ["ActorKind", "StaffAccess", "StaffAction", "StaffAuditEvent", "StaffNote"]
