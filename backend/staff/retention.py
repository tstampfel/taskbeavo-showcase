"""Retention of staff notes (user decision 2026-11-04, hard rule h, GDPR Art. 9).

Notes follow the evidence-photo rule (BEAV-63, uploads/services.py): kept while the
booking is open, then UPLOAD_EVIDENCE_RETENTION_DAYS after it closes; an open dispute,
chargeback or claim holds them, and once it is resolved they are kept
UPLOAD_LEGAL_HOLD_RETENTION_YEARS more. NEEDS REVIEW (lawyer): both periods.

This is the only code that changes or deletes a note. It is system-only: nothing in the
admin calls it. It writes through `StaffNote._base_manager`, since the default manager
refuses updates and deletes, and every purge or redaction writes a `StaffAuditEvent`
naming the note only: never its booking or whether it is restricted, so the log doesn't
show that restricted notes exist.
"""

import calendar
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from bookings.models import Booking
from core.models import ActorKind
from staff import audit
from staff.models import StaffAction, StaffNote

logger = logging.getLogger(__name__)

_BOOKING = Booking._meta.label_lower


def _notes() -> models.QuerySet[StaffNote]:
    """A plain queryset, without the default manager's append-only guard."""
    return StaffNote._base_manager.all()


def place_legal_hold(object_type: str, object_id: str) -> int:
    """An open dispute, chargeback or claim on the object: keep its notes until resolved.
    Notes added while it is open start held (`staff.evidence.add_note`)."""
    return _notes().filter(object_type=object_type, object_id=object_id).update(legal_hold=True)


def lift_legal_hold(object_type: str, object_id: str, *, resolved_at: datetime) -> int:
    """The matter is resolved: keep the notes UPLOAD_LEGAL_HOLD_RETENTION_YEARS more, or
    longer if they already had a later date."""
    until = _add_years(resolved_at, settings.UPLOAD_LEGAL_HOLD_RETENTION_YEARS)
    notes = _notes().filter(object_type=object_type, object_id=object_id, legal_hold=True)
    lifted = 0
    with transaction.atomic():
        for note in notes.select_for_update():
            delete_after = note.delete_after
            if delete_after is None or delete_after < until:
                delete_after = until
            _notes().filter(pk=note.pk).update(legal_hold=False, delete_after=delete_after)
            lifted += 1
    return lifted


def sweep(now: datetime | None = None) -> int:
    """Beat task body: date the notes of closed bookings, then delete the notes past
    `delete_after`. Held notes are neither dated nor deleted; each note is re-checked
    under its lock, so a hold that lands meanwhile wins. Returns the number deleted."""
    now = now or timezone.now()
    keep = timedelta(days=settings.UPLOAD_EVIDENCE_RETENTION_DAYS)
    closed = Booking.objects.filter(closed_at__isnull=False).values_list("pk", "closed_at")
    undated = set(
        _notes()
        .filter(object_type=_BOOKING, delete_after__isnull=True, legal_hold=False)
        .values_list("object_id", flat=True)
    )
    for booking_id, closed_at in closed.filter(pk__in=undated):
        _notes().filter(
            object_type=_BOOKING,
            object_id=str(booking_id),
            delete_after__isnull=True,
            legal_hold=False,
        ).update(delete_after=closed_at + keep)

    deleted = 0
    due = _notes().filter(delete_after__lte=now, legal_hold=False)
    for note_id in list(due.values_list("pk", flat=True)):
        with transaction.atomic():
            note = (
                _notes()
                .select_for_update()
                .filter(pk=note_id, delete_after__lte=now, legal_hold=False)
                .first()
            )
            if note is None:
                continue
            audit.record(
                None,
                StaffAction.NOTE_PURGED,
                note,
                actor_kind=ActorKind.SYSTEM,
            )
            _notes().filter(pk=note.pk).delete()
            deleted += 1
    if deleted:
        logger.info("Staff notes purged", extra={"staff_notes.deleted": deleted})
    return deleted


def redact_notes_about(user_id: int, now: datetime | None = None) -> tuple[int, int]:
    """Account deletion (BEAV-153): empty the bodies of the notes on bookings where the
    deleted person was the poster or the helper, except notes under a legal hold, which
    stay until the matter is resolved and the sweep deletes them. The note rows stay as
    an empty shell with their dates. Returns (redacted, held)."""
    now = now or timezone.now()
    booking_ids = [
        str(pk) for pk in Booking.objects.filter(poster_id=user_id).values_list("pk", flat=True)
    ] + [str(pk) for pk in Booking.objects.filter(helper_id=user_id).values_list("pk", flat=True)]
    notes = _notes().filter(object_type=_BOOKING, object_id__in=booking_ids)
    held = notes.filter(legal_hold=True).count()
    redacted = 0
    with transaction.atomic():
        for note in notes.filter(legal_hold=False, redacted_at__isnull=True).select_for_update():
            _notes().filter(pk=note.pk).update(body="", redacted_at=now)
            audit.record(
                None,
                StaffAction.NOTE_REDACTED,
                note,
                actor_kind=ActorKind.SYSTEM,
            )
            redacted += 1
    return redacted, held


def _add_years(moment: datetime, years: int) -> datetime:
    """The same day `years` later; 29 February becomes 28 February."""
    year = moment.year + years
    day = min(moment.day, calendar.monthrange(year, moment.month)[1])
    return moment.replace(year=year, day=day)
