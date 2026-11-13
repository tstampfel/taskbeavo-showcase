"""Posting and editing tasks: the rules every write path applies (docs/PLAN.md, "Posting a
task and handling offers" and "Service area: tasks in Croatia only").

- Who: posting, publishing and editing need a verified mobile number
  (`accounts.permissions.require_verified_phone`, PHONE_NOT_VERIFIED). Saving a draft
  needs only a signed-in user.
- Where (hard rule j): a location is checked against the active service areas with
  `areas.services.assert_in_service_area` whenever it is set or changed, drafts included,
  and again on publishing. OUT_OF_SERVICE_AREA leaves the task as it was; the client then
  offers `joinWaitlist`.
- What: the category must be on the server's list (CATEGORY_NOT_ALLOWED). Title and
  description go through the contact-detail filter (`moderation.contact_filter`) and the
  excluded-category screening (`tasks.categories.screen_excluded`) when a task is
  published and whenever an open task's text changes. Masked text is stored; a doubtful
  contact detail or an excluded-category match holds the task as PENDING_REVIEW instead
  of OPEN. A bare messenger app name (FLAGGED) is published and logged (kinds only).
- When: a date window starting now or later and ending within 60 days, unless the task
  is flexible (INVALID_TASK_DATES). Drafts may hold half a window; the whole window is
  checked again on publishing, so a draft whose window has passed can't go OPEN. Making a
  task flexible without giving a window clears its window.
- Price: at least EUR 10 (BUDGET_BELOW_MINIMUM).
- On-site contact: only one the poster owns (CARE_CONTACT_NOT_FOUND).
- Photos: the poster's READY task photos, claimed in the same transaction
  (`uploads.services.claim_uploads`) without publishing them. They go to the public
  bucket only while the task is OPEN (`sync_photos`), and leave it again if the task is
  held. A draft's photos are deleted TASK_DRAFT_PHOTO_RETENTION_DAYS after its last save
  unless it is published; removed photos and a closed task's photos start their
  retention clock (`uploads.services.set_retention`).
- Locks: a write that names an on-site contact locks the contact before the task, the
  order `care_contacts.services.delete_contact` uses.
- Offers: a change to the date, address or scope (category or description) marks the
  task's sent offers Needs update (`offers.services.mark_needs_update`). Title, budget,
  photos, access details and on-site contact don't.
- Duplicates: another open task of the poster at the same place on the same day is a
  warning (DUPLICATE_TASK), not an error.

Never log titles, descriptions, addresses or access details.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.permissions import require_verified_phone
from accounts.states import require_can_create_work
from areas.services import assert_in_service_area, point_from_lat_lng
from care_contacts.models import CareContact
from core.errors import ApiError, ErrorCode
from moderation.contact_filter import Verdict, filter_contact_details
from offers import services as offers
from tasks.categories import require_allowed_category, screen_excluded
from tasks.models import Task, TaskPhoto
from uploads import services as uploads
from uploads.models import Purpose

if TYPE_CHECKING:
    from bookings.models import Booking

logger = logging.getLogger(__name__)

MIN_BUDGET_CENTS = 1_000
MAX_BUDGET_CENTS = 1_000_000
MAX_DAYS_AHEAD = 60
MAX_TITLE = 100
MAX_DESCRIPTION = 2_000
MAX_ADDRESS = 200
MAX_ACCESS_DETAILS = 500
MAX_PHOTOS = 10
# A window may start a little in the past: the poster's clock and the request take time.
START_GRACE = dt.timedelta(minutes=10)
# Two tasks at most this far apart count as the same place for the duplicate warning.
DUPLICATE_DISTANCE_M = 50
# The grid `approximate_location` snaps to: 0.01° is about 1.1 km north-south and
# 0.8 km east-west in Croatia.
APPROXIMATE_GRID = 0.01
TASK_TIME_ZONE = ZoneInfo("Europe/Zagreb")

_LIVE = (Task.Status.PENDING_REVIEW, Task.Status.OPEN)
_EDITABLE = (Task.Status.PENDING_REVIEW, Task.Status.OPEN)


class TaskWarning(StrEnum):
    DUPLICATE_TASK = "DUPLICATE_TASK"


@dataclass(frozen=True)
class Location:
    address: str
    lat: float
    lng: float


@dataclass(frozen=True)
class TaskChanges:
    """Fields to set; None leaves a field as it is. `care_contact_id=""` clears it."""

    category: str | None = None
    title: str | None = None
    description: str | None = None
    location: Location | None = None
    access_details: str | None = None
    care_contact_id: str | None = None
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    flexible_date: bool | None = None
    budget_cents: int | None = None
    photo_ids: Sequence[UUID] | None = None


@dataclass
class TaskResult:
    task: Task
    warnings: list[TaskWarning] = field(default_factory=list)


def save_draft(poster: User, changes: TaskChanges, task_id: str | None = None) -> TaskResult:
    """Create a draft, or change one of the poster's drafts. Every field may stay empty;
    the ones given are checked as on publishing, except the text screening."""
    with transaction.atomic():
        contact = _contact_change(poster, changes)
        if task_id is None:
            task = Task(poster=poster, status=Task.Status.DRAFT)
        else:
            task = _owned(poster, task_id, lock=True)
            if task.status != Task.Status.DRAFT:
                raise ApiError(ErrorCode.TASK_NOT_EDITABLE, "Only drafts are saved as drafts.")
        _apply(task, changes, contact, draft=True)
        task.save()
        _set_photos(task, changes.photo_ids)
        sync_photos(task)
    return TaskResult(task)


def post_task(poster: User, changes: TaskChanges) -> TaskResult:
    """Create and publish a task in one go."""
    require_verified_phone(poster)
    with transaction.atomic():
        contact = _contact_change(poster, changes)
        task = Task(poster=poster, status=Task.Status.DRAFT)
        _apply(task, changes, contact)
        warnings = _publish(task)
        _set_photos(task, changes.photo_ids)
        sync_photos(task)
    return TaskResult(task, warnings)


def publish_draft(poster: User, task_id: str) -> TaskResult:
    """Publish one of the poster's drafts as it is saved."""
    require_verified_phone(poster)
    with transaction.atomic():
        task = _owned(poster, task_id, lock=True)
        if task.status != Task.Status.DRAFT:
            raise ApiError(ErrorCode.TASK_NOT_EDITABLE, "The task is already published.")
        warnings = _publish(task)
        sync_photos(task)
    return TaskResult(task, warnings)


def update_task(poster: User, task_id: str, changes: TaskChanges) -> TaskResult:
    """Change a published task. A new address is re-checked against the service areas, and
    changed text is screened again, which can hold the task or release a held one.

    Once an offer is accepted (a booking in AWAITING_PAYMENT or later), only the photos
    and, while the booking is active, the access details can change (TASK_BOOKED
    otherwise): the booking's date, the place the helper goes to, the contact and the
    scope the helper priced stay as booked. A new date goes through a reschedule both
    sides agree to (plan: "Reschedule"). New access details lock the booking, write its
    audit and outbox rows and tell the helper (user decision 2026-11-03).

    An account in limited mode (accounts.states) may change only those same fields, so it
    can finish its bookings but not turn an open task into new work; any other field
    answers the state's code (user decision 2026-11-04, review BEAV-77 R1-1)."""
    require_verified_phone(poster)
    if _changed_fields(changes) - _BOOKED_CHANGEABLE:
        require_can_create_work(poster)
    with transaction.atomic():
        contact = _contact_change(poster, changes)
        task = _owned(poster, task_id, lock=True)
        if task.status not in _EDITABLE:
            raise ApiError(ErrorCode.TASK_NOT_EDITABLE, "Only open tasks can be changed.")
        booking = _booking_for_change(task, changes)
        access_before = task.access_details
        before = _scope(task)
        _apply(task, changes, contact)
        _require_complete(task)
        if changes.title is not None or changes.description is not None:
            _screen(task)
            task.status = Task.Status.PENDING_REVIEW if task.hold_reasons else Task.Status.OPEN
        task.save()
        if _scope(task) != before:
            offers.mark_needs_update(task, poster)
        if booking is not None and task.access_details != access_before:
            record_access_details_change(booking, poster)
        _set_photos(task, changes.photo_ids)
        sync_photos(task)
        warnings = _warnings(task) if changes.location or changes.window_start else []
    return TaskResult(task, warnings)


def task_is_booked(task: Task) -> bool:
    # Imported here: bookings.models imports this app's models.
    from bookings.queries import task_is_booked as booked

    return booked(task)


# What may change on a booked task; access details only while the booking is active.
_BOOKED_CHANGEABLE = frozenset({"photo_ids", "access_details"})


def _changed_fields(changes: TaskChanges) -> set[str]:
    return {f.name for f in fields(changes) if getattr(changes, f.name) is not None}


def _booking_for_change(task: Task, changes: TaskChanges) -> "Booking | None":
    """For a booked task: refuse anything but photos and access details, and return the
    active booking, locked, when the access details change. None for an unbooked task."""
    # Imported here: bookings.models imports this app's models.
    from bookings.state_machine import lock_active_booking

    if not task_is_booked(task):
        return None
    if _changed_fields(changes) - _BOOKED_CHANGEABLE:
        raise ApiError(ErrorCode.TASK_BOOKED, "A booked task keeps its details.")
    if changes.access_details is None:
        return None
    booking = lock_active_booking(task)
    if booking is None:
        raise ApiError(
            ErrorCode.TASK_BOOKED, "Access details change only while the booking is active."
        )
    return booking


def record_access_details_change(booking: "Booking", poster: User) -> None:
    # Imported here: bookings.models imports this app's models.
    from bookings.state_machine import record_access_details_change as record

    record(booking, poster)


def _scope(task: Task) -> tuple[object, ...]:
    """What helpers priced their offers on: a change here is a big change (date, address
    or scope), which marks the task's offers Needs update."""
    location = None if task.location is None else (task.location.x, task.location.y)
    return (
        task.category,
        task.description,
        task.address,
        location,
        task.window_start,
        task.window_end,
        task.flexible_date,
    )


def close_task(task: Task, *, closed_at: dt.datetime | None = None) -> Task:
    """Close a task (cancelled, expired or done; those flows call this), end its live
    offers and start its photos' retention clock: public task photos are kept 30 days
    after closing."""
    closed_at = closed_at or timezone.now()
    with transaction.atomic():
        task = Task.objects.select_for_update().get(pk=task.pk)
        if task.status == Task.Status.CLOSED:
            return task
        task.status = Task.Status.CLOSED
        task.closed_at = closed_at
        task.save(update_fields=["status", "closed_at", "updated_at"])
        uploads.set_retention(task.photos.all(), closed_at=closed_at)
        offers.close_offers(task, now=closed_at)
    return task


def sync_photos(task: Task) -> None:
    """Make the task's photos match its status: public while OPEN, private while a DRAFT
    or held for review, and a draft's photos deleted if it isn't saved or published within
    TASK_DRAFT_PHOTO_RETENTION_DAYS. Call it, inside the task's transaction, after any
    status change (staff releasing a held task too). A closed task's photos stay as they
    are, with the retention `close_task` gave them."""
    photos = list(task.photos.all())
    if not photos:
        return
    if task.status == Task.Status.OPEN:
        uploads.publish_claimed(photos)
        uploads.keep_until(photos, None)
    elif task.status == Task.Status.PENDING_REVIEW:
        uploads.unpublish(photos)
        uploads.keep_until(photos, None)
    elif task.status == Task.Status.DRAFT:
        uploads.unpublish(photos)
        days = settings.TASK_DRAFT_PHOTO_RETENTION_DAYS
        uploads.keep_until(photos, timezone.now() + dt.timedelta(days=days))


def visible_task(viewer: User | None, task_id: str) -> Task:
    """The task if `viewer` may see it: the poster always, the helper of its active
    booking whatever the task's status, anyone else once it is OPEN. TASK_NOT_FOUND
    otherwise, so IDs reveal nothing."""
    # Imported here: bookings.models imports this app's models.
    from bookings.queries import active_booking_of_helper

    try:
        task = Task.objects.select_related("service_area").get(pk=UUID(str(task_id)))
    except (ValueError, Task.DoesNotExist):
        raise ApiError(ErrorCode.TASK_NOT_FOUND, "No such task.") from None
    if task.status == Task.Status.OPEN or (viewer is not None and task.poster_id == viewer.pk):
        return task
    if viewer is not None and active_booking_of_helper(task, viewer) is not None:
        return task
    raise ApiError(ErrorCode.TASK_NOT_FOUND, "No such task.")


def _publish(task: Task) -> list[TaskWarning]:
    location = _require_complete(task)
    _require_valid_window(task)
    # The area may have been deactivated since the draft was saved.
    task.service_area = assert_in_service_area(location)
    _screen(task)
    task.status = Task.Status.PENDING_REVIEW if task.hold_reasons else Task.Status.OPEN
    task.published_at = timezone.now()
    task.save()
    return _warnings(task)


def _require_complete(task: Task) -> Point:
    """The task's location, once every field publishing needs is set."""
    location = task.location
    missing = not (
        task.category
        and task.title
        and task.description
        and task.address
        and task.budget_cents is not None
    )
    if missing or location is None:
        raise ApiError(
            ErrorCode.INVALID_TASK_DETAILS,
            "Category, title, description, address and budget are required.",
        )
    if not task.flexible_date and (task.window_start is None or task.window_end is None):
        raise ApiError(ErrorCode.INVALID_TASK_DATES, "A task without flexible date needs a window.")
    return location


def _require_valid_window(task: Task) -> None:
    """The stored window, if any, holds now: a draft saved last week may have passed."""
    if task.window_start is None and task.window_end is None:
        return
    _check_window(task.window_start, task.window_end, partial=False)


def _screen(task: Task) -> None:
    """Run the contact-detail filter and the excluded-category screening on the text."""
    locale = task.poster.locale
    title = filter_contact_details(task.title, locale)
    description = filter_contact_details(task.description, locale)
    task.title = title.text
    task.description = description.text
    verdict = max(title.verdict, description.verdict, key=list(Verdict).index)
    task.moderation_verdict = verdict.value
    task.moderation_kinds = sorted(kind.value for kind in title.kinds | description.kinds)
    reasons = []
    if verdict == Verdict.NEEDS_REVIEW:
        reasons.append(Task.HoldReason.CONTACT_DETAILS.value)
    if screen_excluded(task.title, task.description):
        reasons.append(Task.HoldReason.EXCLUDED_CATEGORY.value)
    task.hold_reasons = reasons
    if verdict == Verdict.FLAGGED or reasons:
        logger.info(
            "Task text screened",
            extra={"task_id": str(task.pk), "verdict": verdict.value, "hold_reasons": reasons},
        )


def _warnings(task: Task) -> list[TaskWarning]:
    if task.location is None:
        return []
    others = Task.objects.filter(
        poster_id=task.poster_id,
        status__in=_LIVE,
        location__distance_lte=(task.location, D(m=DUPLICATE_DISTANCE_M)),
    ).exclude(pk=task.pk)
    if task.window_start is None:
        others = others.filter(window_start__isnull=True)
    else:
        day = task.window_start.astimezone(TASK_TIME_ZONE).date()
        start = dt.datetime.combine(day, dt.time.min, tzinfo=TASK_TIME_ZONE)
        others = others.filter(
            window_start__gte=start, window_start__lt=start + dt.timedelta(days=1)
        )
    return [TaskWarning.DUPLICATE_TASK] if others.exists() else []


def _apply(
    task: Task, changes: TaskChanges, contact: CareContact | None, *, draft: bool = False
) -> None:
    """Apply `changes`; `contact` is the already locked result of `_contact_change`."""
    if changes.category is not None:
        task.category = require_allowed_category(changes.category).value
    if changes.title is not None:
        task.title = _text(changes.title, MAX_TITLE)
    if changes.description is not None:
        task.description = _text(changes.description, MAX_DESCRIPTION)
    if changes.location is not None:
        _set_location(task, changes.location)
    if changes.access_details is not None:
        task.access_details = _text(changes.access_details, MAX_ACCESS_DETAILS, allow_empty=True)
    if changes.care_contact_id is not None:
        task.care_contact = contact
    if changes.flexible_date is not None:
        task.flexible_date = changes.flexible_date
    if changes.window_start is not None or changes.window_end is not None:
        _set_window(task, changes.window_start, changes.window_end, partial=draft)
    elif changes.flexible_date:
        # Flexible with no new window: an old one would still drive the duplicate warning.
        task.window_start = None
        task.window_end = None
    if changes.budget_cents is not None:
        _set_budget(task, changes.budget_cents)


def _set_location(task: Task, location: Location) -> None:
    address = _text(location.address, MAX_ADDRESS)
    try:
        point = point_from_lat_lng(location.lat, location.lng)
    except ValueError:
        raise ApiError(ErrorCode.INVALID_TASK_DETAILS, "Coordinates out of range.") from None
    # Raises OUT_OF_SERVICE_AREA before anything on the task changes.
    task.service_area = assert_in_service_area(point)
    task.address = address
    task.location = point
    task.approximate_location = approximate(point)


def approximate(point: Point) -> Point:
    """The centre of the grid cell `point` lies in: close enough to judge the distance,
    too coarse to find the door."""

    def snap(value: float) -> float:
        cell = value // APPROXIMATE_GRID
        return round((cell + 0.5) * APPROXIMATE_GRID, 6)

    return Point(snap(point.x), snap(point.y), srid=point.srid)


def _set_window(
    task: Task, start: dt.datetime | None, end: dt.datetime | None, *, partial: bool
) -> None:
    """Set the window from the given ends and the stored ones. A draft (`partial`) may
    hold one end only; the whole window is checked on publishing."""
    start = start or task.window_start
    end = end or task.window_end
    _check_window(start, end, partial=partial)
    task.window_start = start
    task.window_end = end


def _check_window(start: dt.datetime | None, end: dt.datetime | None, *, partial: bool) -> None:
    now = timezone.now()
    valid = (
        (partial or (start is not None and end is not None))
        and (start is None or start >= now - START_GRACE)
        and (end is None or end <= now + dt.timedelta(days=MAX_DAYS_AHEAD))
        and (start is None or end is None or end > start)
    )
    if not valid:
        raise ApiError(
            ErrorCode.INVALID_TASK_DATES,
            f"The window must start from now and end within {MAX_DAYS_AHEAD} days.",
        )


def _set_budget(task: Task, cents: int) -> None:
    if cents < MIN_BUDGET_CENTS:
        raise ApiError(ErrorCode.BUDGET_BELOW_MINIMUM, "The budget is below EUR 10.")
    if cents > MAX_BUDGET_CENTS:
        raise ApiError(ErrorCode.INVALID_TASK_DETAILS, "The budget is too high.")
    task.budget_cents = cents


def _set_photos(task: Task, photo_ids: Sequence[UUID] | None) -> None:
    """Make the task's photos exactly `photo_ids`, in order. Call after the task is saved,
    inside its transaction."""
    if photo_ids is None:
        return
    wanted = list(dict.fromkeys(photo_ids))
    if len(wanted) > MAX_PHOTOS:
        raise ApiError(ErrorCode.INVALID_TASK_DETAILS, f"At most {MAX_PHOTOS} photos.")
    current = {photo.upload_id: photo for photo in task.task_photos.select_related("upload")}
    new = [pk for pk in wanted if pk not in current]
    try:
        # Not published here: `sync_photos` publishes them once the task is OPEN.
        uploads.claim_uploads(task.poster, new, Purpose.TASK_PHOTO, publish=False)
    except uploads.UploadError as error:
        raise ApiError(error.code, "Photo not available.") from None
    removed = [photo for pk, photo in current.items() if pk not in wanted]
    if removed:
        uploads.set_retention([photo.upload for photo in removed], closed_at=timezone.now())
        TaskPhoto.objects.filter(pk__in=[photo.pk for photo in removed]).delete()
    for position, pk in enumerate(wanted):
        TaskPhoto.objects.update_or_create(
            upload_id=pk, defaults={"task": task, "position": position}
        )


def _owned(poster: User, task_id: str, *, lock: bool = False) -> Task:
    queryset = Task.objects.filter(poster=poster)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=UUID(str(task_id)))
    except (ValueError, Task.DoesNotExist):
        raise ApiError(ErrorCode.TASK_NOT_FOUND, "No such task.") from None


def _contact_change(poster: User, changes: TaskChanges) -> CareContact | None:
    """The poster's contact `changes` names, locked; None for "" or no change, and
    CARE_CONTACT_NOT_FOUND for anyone else's. Call it before locking the task: locking the
    contact first is the order `care_contacts.services.delete_contact` uses."""
    contact_id = changes.care_contact_id
    if not contact_id:
        return None
    try:
        # Locked, so a concurrent delete_contact waits for this task or sees it.
        return CareContact.objects.select_for_update().get(owner=poster, pk=int(contact_id))
    except (ValueError, CareContact.DoesNotExist):
        raise ApiError(ErrorCode.CARE_CONTACT_NOT_FOUND, "No such contact.") from None


def _text(value: str, limit: int, *, allow_empty: bool = False) -> str:
    text = value.strip()
    if (not text and not allow_empty) or len(text) > limit:
        raise ApiError(ErrorCode.INVALID_TASK_DETAILS, f"Text must be 1 to {limit} characters.")
    return text
