"""GraphQL API of tasks. Rules: tasks/services.py; who sees what: tasks/models.py."""

import datetime as dt
from enum import Enum
from typing import Any
from uuid import UUID

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from accounts.models import User
from accounts.permissions import CanCreateWork, PhoneVerified
from bookings.models import Booking
from bookings.queries import active_booking_of_helper
from care_contacts.models import CareContact
from care_contacts.schema import CareContactType, OnSiteContactType
from core.categories import TaskCategoryEnum
from core.errors import ApiError, ErrorCode
from moderation.contact_filter import Verdict
from tasks import services
from tasks.models import Task
from uploads import services as uploads


@strawberry.enum(name="TaskStatus")
class TaskStatusEnum(Enum):
    DRAFT = Task.Status.DRAFT.value
    PENDING_REVIEW = strawberry.enum_value(
        Task.Status.PENDING_REVIEW.value,
        description="Published but held until staff check it; see `holdReasons`.",
    )
    OPEN = Task.Status.OPEN.value
    CLOSED = Task.Status.CLOSED.value


@strawberry.enum(name="TaskHoldReason", description="Why a task waits for review.")
class TaskHoldReasonEnum(Enum):
    CONTACT_DETAILS = strawberry.enum_value(
        Task.HoldReason.CONTACT_DETAILS.value,
        description="The text may contain contact details.",
    )
    EXCLUDED_CATEGORY = strawberry.enum_value(
        Task.HoldReason.EXCLUDED_CATEGORY.value,
        description="The text reads like a ride, medical care or personal care.",
    )


@strawberry.enum(name="TaskWarning", description="Something the poster should know; not an error.")
class TaskWarningEnum(Enum):
    DUPLICATE_TASK = strawberry.enum_value(
        services.TaskWarning.DUPLICATE_TASK.value,
        description="You already have an open task at this address on this day.",
    )


@strawberry.type(name="TaskPhoto")
class TaskPhotoType:
    id: strawberry.ID
    url: str


@strawberry.type(
    name="Task",
    description=(
        "A task. Everyone who may see it gets the public fields. The exact address, access "
        "details and on-site contact are for the poster and the helper of the task's active "
        "booking; `careContact` and the moderation fields are for the poster only."
    ),
)
class TaskType:
    id: strawberry.ID
    status: TaskStatusEnum
    category: TaskCategoryEnum | None
    title: str
    description: str
    approximate_lat: float | None = strawberry.field(
        description="About a kilometre off the real place; safe to show on a map."
    )
    approximate_lng: float | None
    service_area_name: str | None
    window_start: dt.datetime | None
    window_end: dt.datetime | None
    flexible_date: bool
    budget_cents: int | None = strawberry.field(description="Euro cents; at least 1000.")
    photos: list[TaskPhotoType]
    is_mine: bool
    created_at: dt.datetime
    published_at: dt.datetime | None
    # The poster and the helper of an active booking (hard rule e).
    address: str | None
    lat: float | None = strawberry.field(
        description="The exact place. The poster and the helper of an active booking."
    )
    lng: float | None
    access_details: str | None = strawberry.field(
        description=(
            "Doorbell, floor, gate code, key. The poster and the helper of an active booking."
        )
    )
    on_site_contact: OnSiteContactType | None = strawberry.field(
        description="Whom the helper meets. The poster and the helper of an active booking."
    )
    # Poster only.
    care_contact: CareContactType | None
    text_masked: bool | None = strawberry.field(
        description="Contact details were hidden in title or description. Poster only."
    )
    hold_reasons: list[TaskHoldReasonEnum] | None = strawberry.field(
        description="Why the task is PENDING_REVIEW. Poster only."
    )

    @classmethod
    def of(cls, task: Task, viewer: User | None) -> "TaskType":
        mine = viewer is not None and task.poster_id == viewer.pk
        booking = None if mine or viewer is None else active_booking_of_helper(task, viewer)
        # The exact place: the poster, and the helper while their booking is active.
        exact_place = mine or booking is not None
        contact = _on_site_contact(task, booking) if exact_place else None
        approximate = task.approximate_location
        exact = task.location if exact_place else None
        return cls(
            id=strawberry.ID(str(task.pk)),
            status=TaskStatusEnum(task.status),
            category=TaskCategoryEnum(task.category) if task.category else None,
            title=task.title,
            description=task.description,
            approximate_lat=approximate.y if approximate else None,
            approximate_lng=approximate.x if approximate else None,
            service_area_name=task.service_area.name if task.service_area else None,
            window_start=task.window_start,
            window_end=task.window_end,
            flexible_date=task.flexible_date,
            budget_cents=task.budget_cents,
            photos=_photos(task, mine),
            is_mine=mine,
            created_at=task.created_at,
            published_at=task.published_at,
            address=task.address if exact_place else None,
            lat=exact.y if exact else None,
            lng=exact.x if exact else None,
            access_details=task.access_details if exact_place else None,
            on_site_contact=OnSiteContactType.of(viewer, contact, booking) if contact else None,
            care_contact=(
                CareContactType.of(task.care_contact) if mine and task.care_contact else None
            ),
            text_masked=task.moderation_verdict == Verdict.MASKED.value if mine else None,
            hold_reasons=[TaskHoldReasonEnum(r) for r in task.hold_reasons] if mine else None,
        )


@strawberry.type(
    description="A saved task and what the poster should know. Its `id` is the task's."
)
class TaskPayload:
    id: strawberry.ID
    task: TaskType
    warnings: list[TaskWarningEnum]

    @classmethod
    def of(cls, result: services.TaskResult, viewer: User) -> "TaskPayload":
        return cls(
            id=strawberry.ID(str(result.task.pk)),
            task=TaskType.of(result.task, viewer),
            warnings=[TaskWarningEnum(warning.value) for warning in result.warnings],
        )


@strawberry.input
class TaskLocationInput:
    address: str = strawberry.field(description="The exact address; shown to the poster only.")
    lat: float
    lng: float


@strawberry.input(description="Every field of a task. Omitted fields stay as they are on edits.")
class TaskInput:
    category: TaskCategoryEnum | None = strawberry.field(
        default=None,
        description=(
            "TaskCategory lists only categories TaskBeavo accepts, so rides, medical and "
            "personal care fail schema validation."
        ),
    )
    title: str | None = None
    description: str | None = None
    location: TaskLocationInput | None = None
    access_details: str | None = None
    care_contact_id: strawberry.ID | None = strawberry.field(
        default=None, description='One of your on-site contacts; "" removes it.'
    )
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    flexible_date: bool | None = None
    budget_cents: int | None = None
    photo_ids: list[strawberry.ID] | None = strawberry.field(
        default=None,
        description="Your READY task-photo uploads, in order; replaces the task's photos.",
    )


_TASK_ERRORS = (
    "OUT_OF_SERVICE_AREA, INVALID_TASK_DETAILS, INVALID_TASK_DATES, "
    "BUDGET_BELOW_MINIMUM, CARE_CONTACT_NOT_FOUND, UPLOAD_NOT_FOUND"
)


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "A task: the poster sees their own in any state, everyone else only OPEN tasks. "
            "Errors: TASK_NOT_FOUND."
        )
    )
    async def task(self, info: Info, id: strawberry.ID) -> TaskType:
        viewer = _viewer(info)
        task = await sync_to_async(_load)(viewer, str(id))
        return task

    @strawberry.field(description="Your tasks, newest first. Errors: UNAUTHENTICATED.")
    async def my_tasks(self, info: Info) -> list[TaskType]:
        user = _signed_in_user(info)
        return await sync_to_async(_my_tasks)(user)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Save a draft: a new one without `id`, or change your draft. Fields may stay "
            "empty; the ones given are checked. Errors: UNAUTHENTICATED, TASK_NOT_FOUND, "
            f"TASK_NOT_EDITABLE, {_TASK_ERRORS}."
        )
    )
    async def save_task_draft(
        self, info: Info, input: TaskInput, id: strawberry.ID | None = None
    ) -> TaskPayload:
        user = _signed_in_user(info)
        changes = _changes(input)
        result = await sync_to_async(services.save_draft)(user, changes, _id(id))
        return await sync_to_async(TaskPayload.of)(result, user)

    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Post a task. It is OPEN, or PENDING_REVIEW when its text is held for review. "
            "Errors: UNAUTHENTICATED, TERMS_UPDATE_REQUIRED, ACCOUNT_SUSPENDED, "
            "ACCOUNT_DELETION_PENDING, PHONE_NOT_VERIFIED, "
            f"{_TASK_ERRORS}."
        ),
    )
    async def post_task(self, info: Info, input: TaskInput) -> TaskPayload:
        user = _signed_in_user(info)
        result = await sync_to_async(services.post_task)(user, _changes(input))
        return await sync_to_async(TaskPayload.of)(result, user)

    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Publish your draft as saved. Errors: UNAUTHENTICATED, TERMS_UPDATE_REQUIRED, "
            "ACCOUNT_SUSPENDED, ACCOUNT_DELETION_PENDING, PHONE_NOT_VERIFIED, "
            f"TASK_NOT_FOUND, TASK_NOT_EDITABLE, {_TASK_ERRORS}."
        ),
    )
    async def publish_task_draft(self, info: Info, id: strawberry.ID) -> TaskPayload:
        user = _signed_in_user(info)
        result = await sync_to_async(services.publish_draft)(user, str(id))
        return await sync_to_async(TaskPayload.of)(result, user)

    @strawberry.mutation(
        permission_classes=[PhoneVerified],
        description=(
            "Change your published task; omitted fields stay as they are. A new address is "
            "checked against the service areas again. Once an offer is accepted only the "
            "photos and, while the booking is active, the access details can change. "
            "An account in limited mode may change only the photos and access details. "
            "Errors: UNAUTHENTICATED, PHONE_NOT_VERIFIED, TERMS_UPDATE_REQUIRED, "
            "ACCOUNT_SUSPENDED, ACCOUNT_DELETION_PENDING, TASK_NOT_FOUND, TASK_NOT_EDITABLE, "
            f"TASK_BOOKED, {_TASK_ERRORS}."
        ),
    )
    async def update_task(self, info: Info, id: strawberry.ID, input: TaskInput) -> TaskPayload:
        user = _signed_in_user(info)
        result = await sync_to_async(services.update_task)(user, str(id), _changes(input))
        return await sync_to_async(TaskPayload.of)(result, user)


def _changes(input: TaskInput) -> services.TaskChanges:
    location = input.location
    return services.TaskChanges(
        category=input.category.value if input.category else None,
        title=input.title,
        description=input.description,
        location=(
            services.Location(address=location.address, lat=location.lat, lng=location.lng)
            if location
            else None
        ),
        access_details=input.access_details,
        care_contact_id=None if input.care_contact_id is None else str(input.care_contact_id),
        window_start=input.window_start,
        window_end=input.window_end,
        flexible_date=input.flexible_date,
        budget_cents=input.budget_cents,
        photo_ids=None if input.photo_ids is None else [_upload_id(i) for i in input.photo_ids],
    )


def _load(viewer: User | None, task_id: str) -> TaskType:
    return TaskType.of(services.visible_task(viewer, task_id), viewer)


def _my_tasks(user: User) -> list[TaskType]:
    tasks = Task.objects.filter(poster=user).select_related("service_area", "care_contact")
    return [TaskType.of(task, user) for task in tasks.prefetch_related("task_photos__upload")]


def _on_site_contact(task: Task, booking: Booking | None) -> CareContact | None:
    """The booking's contact for its helper (fixed when the offer was accepted), the task's
    current one for the poster."""
    contact = task.care_contact if booking is None else booking.care_contact
    # A contact the poster doesn't own (a bug, or a forged ID) shows nothing.
    if contact is None or contact.owner_id != task.poster_id:
        return None
    return contact


def _photos(task: Task, mine: bool) -> list[TaskPhotoType]:
    """The poster sees every photo (unpublished ones through short-lived URLs); everyone
    else only published ones."""
    photos = []
    for photo in task.task_photos.all():
        upload = photo.upload
        url = uploads.read_url(upload) if mine else uploads.public_photo_url(upload)
        if url:
            photos.append(TaskPhotoType(id=strawberry.ID(str(upload.pk)), url=url))
    return photos


def _id(value: strawberry.ID | None) -> str | None:
    return None if value is None else str(value)


def _upload_id(value: strawberry.ID) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ApiError(ErrorCode.UPLOAD_NOT_FOUND, "No such upload.") from None


def _viewer(info: Info[Any, Any]) -> User | None:
    user = info.context.user
    return user if isinstance(user, User) and user.is_active else None


def _signed_in_user(info: Info[Any, Any]) -> User:
    user = _viewer(info)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return user
