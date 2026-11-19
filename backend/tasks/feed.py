"""Lists of open tasks: the helper's feed and the public browse pages (docs/PLAN.md,
"Posting a task and handling offers"; BEAV-89).

- Where (hard rule j): only tasks whose place lies inside an active Croatian service area,
  checked with ST_Covers like an offer is (offers.services). The helper's feed narrows that
  to the areas on their helper profile.
- What (hard rule e): these functions return Task rows; the API shows them only as
  `TaskListing` (tasks/schema.py), which has no exact address, coordinates, access details
  or on-site contact, whoever asks.
- Order (hard rule k): one neutral order for everyone, newest published first (ties by
  id). Nothing about the helper, the poster or the offers changes it, and no task is
  hidden from a helper for how they behaved.
- Which tasks: OPEN and published, not booked, the poster able to take offers, and the
  date not passed: the tasks a helper could still offer on. The feed also leaves out the
  helper's own tasks, and a business poster's tasks for a private helper, who can't offer
  on them (accounts.business).
- Pages: keyset pagination on (published_at, id), so new tasks don't shift a page.
"""

import base64
import binascii
import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from accounts.models import PosterBusiness, User
from areas.models import ServiceArea
from core.errors import ApiError, ErrorCode
from helpers.models import HelperProfile
from tasks.models import Task

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class Page:
    tasks: list[Task]
    end_cursor: str | None
    has_more: bool


def helper_feed(
    helper: User,
    *,
    first: int = DEFAULT_PAGE_SIZE,
    after: str | None = None,
    category: str | None = None,
    now: dt.datetime | None = None,
) -> Page:
    """Open tasks in `helper`'s service areas. Empty without a helper profile or areas."""
    profile = HelperProfile.objects.filter(user=helper).first()
    if profile is None:
        return Page(tasks=[], end_cursor=None, has_more=False)
    in_my_areas = _active_areas().filter(helpers=profile, polygon__covers=OuterRef("location"))
    tasks = offerable(now).filter(Exists(in_my_areas)).exclude(poster=helper)
    if not profile.is_trader:
        # A business poster's tasks take registered helpers only.
        tasks = tasks.exclude(Exists(PosterBusiness.objects.filter(user=OuterRef("poster"))))
    return _page(_with_category(tasks, category), first, after)


def browse(
    *,
    area_slug: str | None = None,
    first: int = DEFAULT_PAGE_SIZE,
    after: str | None = None,
    category: str | None = None,
    now: dt.datetime | None = None,
) -> Page:
    """Open tasks for anyone, in one active area or all of them."""
    tasks = offerable(now)
    if area_slug is not None:
        area = _active_areas().filter(slug=area_slug).first()
        if area is None:
            raise ApiError(ErrorCode.SERVICE_AREA_NOT_FOUND, "No such active service area.")
        tasks = tasks.filter(location__coveredby=area.polygon)
    return _page(_with_category(tasks, category), first, after)


def offerable(now: dt.datetime | None = None) -> QuerySet[Task]:
    """Published OPEN tasks helpers could still offer on, in an active area, any order."""
    # Imported here: bookings and offers import this app's models.
    from bookings.models import Booking
    from offers.services import FLEXIBLE_TASK_LIFETIME

    now = now or timezone.now()
    booked = Booking.objects.filter(task=OuterRef("pk"), task_reopened_at__isnull=True).exclude(
        status=Booking.Status.PAYMENT_FAILED
    )
    in_an_area = _active_areas().filter(polygon__covers=OuterRef("location"))
    not_passed = Q(flexible_date=False, window_end__gt=now) | Q(
        flexible_date=True, published_at__gt=now - FLEXIBLE_TASK_LIFETIME
    )
    return (
        Task.objects.filter(
            status=Task.Status.OPEN,
            location__isnull=False,
            published_at__isnull=False,
            poster__status=User.Status.ACTIVE,
        )
        .filter(not_passed)
        .filter(Exists(in_an_area))
        .exclude(Exists(booked))
    )


def encode_cursor(task: Task) -> str:
    # Listed tasks are published (`offerable`), so published_at is set.
    published = task.published_at or task.created_at
    raw = f"{published.isoformat()}|{task.pk}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[dt.datetime, UUID]:
    try:
        published, task_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        at = dt.datetime.fromisoformat(published)
        if at.tzinfo is None:
            raise ValueError("naive")
        return at, UUID(task_id)
    except (ValueError, UnicodeError, binascii.Error):
        raise ApiError(ErrorCode.INVALID_CURSOR, "Not a cursor from this list.") from None


def _active_areas() -> QuerySet[ServiceArea]:
    return ServiceArea.objects.filter(active=True, country_code=ServiceArea.Country.HR)


def _with_category(tasks: QuerySet[Task], category: str | None) -> QuerySet[Task]:
    return tasks if category is None else tasks.filter(category=category)


def _page(tasks: QuerySet[Task], first: int, after: str | None) -> Page:
    size = max(1, min(first, MAX_PAGE_SIZE))
    if after is not None:
        published, task_id = decode_cursor(after)
        tasks = tasks.filter(
            Q(published_at__lt=published) | Q(published_at=published, id__lt=task_id)
        )
    rows = list(
        tasks.order_by("-published_at", "-id")
        .select_related("service_area")
        .prefetch_related("task_photos__upload")[: size + 1]
    )
    has_more = len(rows) > size
    rows = rows[:size]
    return Page(
        tasks=rows,
        end_cursor=encode_cursor(rows[-1]) if rows else None,
        has_more=has_more,
    )
