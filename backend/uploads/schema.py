"""GraphQL API of photo uploads: presigned R2 PUTs (uploads/services.py)."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from accounts.models import Session, User
from core.errors import ApiError, ErrorCode
from uploads import services
from uploads.models import Purpose, Status, Upload


@strawberry.enum(description="What a photo is for. Decides who may see it and what is kept.")
class UploadPurpose(Enum):
    TASK_PHOTO = strawberry.enum_value(
        Purpose.TASK_PHOTO.value,
        description="Shown to other users; stored without any metadata (no GPS).",
    )
    EVIDENCE_PHOTO = strawberry.enum_value(
        Purpose.EVIDENCE_PHOTO.value,
        description=(
            "Proof for a booking. Private; its capture time and position are kept "
            "server-side and viewers see a copy without metadata."
        ),
    )


@strawberry.enum(description="Where an upload is in its life.")
class UploadStatus(Enum):
    PENDING = strawberry.enum_value(
        Status.PENDING.value, description="Waiting for the PUT and completeUpload."
    )
    PROCESSING = strawberry.enum_value(
        Status.PROCESSING.value, description="Received; being checked and cleaned."
    )
    READY = strawberry.enum_value(
        Status.READY.value, description="Ready to use; `url` is set for the owner."
    )
    REJECTED = strawberry.enum_value(
        Status.REJECTED.value, description="Not a valid image; see `rejectionCode`."
    )


@strawberry.type(name="Upload", description="A photo the signed-in user uploaded.")
class UploadType:
    id: strawberry.ID
    purpose: UploadPurpose
    status: UploadStatus
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None
    rejection_code: ErrorCode | None = strawberry.field(
        description="UPLOAD_INVALID_IMAGE, UPLOAD_TOO_LARGE or UPLOAD_CHANGED when REJECTED."
    )
    url: str | None = strawberry.field(
        description=(
            "Where to load the photo once READY. Until a task photo is published (after a "
            "task claims it), and always for an evidence photo, the URL is valid for a few "
            "minutes; query again for a fresh one."
        )
    )

    @classmethod
    def of(cls, upload: Upload) -> "UploadType":
        return cls(
            id=strawberry.ID(str(upload.pk)),
            purpose=UploadPurpose(upload.purpose),
            status=UploadStatus(upload.status),
            content_type=upload.content_type,
            size_bytes=upload.size_bytes,
            width=upload.width,
            height=upload.height,
            rejection_code=ErrorCode[upload.rejection_code] if upload.rejection_code else None,
            url=services.read_url(upload),
        )


@strawberry.type(
    description=(
        "Where to PUT the file: send the bytes as the body with exactly `contentType` as "
        "the Content-Type header and the declared size, before `expiresAt`. Then call "
        "completeUpload. Its `id` is the upload's."
    )
)
class UploadTarget:
    id: strawberry.ID
    upload: UploadType
    url: str
    method: str = strawberry.field(description='Always "PUT".')
    content_type: str
    expires_at: datetime


@strawberry.input
class CreateUploadUrlInput:
    purpose: UploadPurpose
    content_type: str = strawberry.field(description='"image/jpeg", "image/png" or "image/webp".')
    size_bytes: int = strawberry.field(description="The file's exact size in bytes.")


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "One of the signed-in user's uploads, to follow it to READY. "
            "Errors: UNAUTHENTICATED, UPLOAD_NOT_FOUND."
        )
    )
    async def upload(self, info: Info, id: strawberry.ID) -> UploadType:
        user = _signed_in_user(info)
        return await sync_to_async(_call)(services.get_own_upload, user, _uuid(id))


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Start a photo upload: returns a presigned PUT URL into private storage. "
            "Errors: UNAUTHENTICATED, UPLOAD_TYPE_NOT_ALLOWED, UPLOAD_TOO_LARGE, "
            "TOO_MANY_PENDING_UPLOADS, EVIDENCE_NEEDS_APP (evidence from a web session), "
            "RATE_LIMITED."
        )
    )
    async def create_upload_url(self, info: Info, input: CreateUploadUrlInput) -> UploadTarget:
        session = _signed_in_session(info)
        try:
            target = await sync_to_async(services.create_upload_url)(
                session.user,
                Purpose(input.purpose.value),
                input.content_type,
                input.size_bytes,
                platform=session.platform,
            )
        except services.UploadError as error:
            raise ApiError(error.code, "Upload refused.") from None
        upload = target.upload
        return UploadTarget(
            id=strawberry.ID(str(upload.pk)),
            upload=UploadType.of(upload),
            url=target.url,
            method="PUT",
            content_type=upload.content_type,
            expires_at=upload.url_expires_at,
        )

    @strawberry.mutation(
        description=(
            "Tell the API the PUT finished. The photo is checked and cleaned in the "
            "background: follow `status` with the `upload` query until READY or REJECTED. "
            "Errors: UNAUTHENTICATED, UPLOAD_NOT_FOUND, UPLOAD_NOT_RECEIVED (PUT the file "
            "first)."
        )
    )
    async def complete_upload(self, info: Info, id: strawberry.ID) -> UploadType:
        user = _signed_in_user(info)
        return await sync_to_async(_call)(services.complete_upload, user, _uuid(id))

    @strawberry.mutation(
        description=(
            "Delete one of your uploads that nothing uses yet (not attached to a task or "
            "booking), with its files, freeing its place in the pending-upload limit. "
            "Returns the deleted upload's id. "
            "Errors: UNAUTHENTICATED, UPLOAD_NOT_FOUND (also for an attached upload)."
        )
    )
    async def discard_upload(self, info: Info, id: strawberry.ID) -> strawberry.ID:
        user = _signed_in_user(info)
        try:
            discarded = await sync_to_async(services.discard_upload)(user, _uuid(id))
        except services.UploadError as error:
            raise ApiError(error.code, "Upload not available.") from None
        return strawberry.ID(str(discarded))


def _call(action: Any, user: User, upload_id: UUID) -> UploadType:
    try:
        upload = action(user, upload_id)
    except services.UploadError as error:
        raise ApiError(error.code, "Upload not available.") from None
    return UploadType.of(upload)


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ApiError(ErrorCode.UPLOAD_NOT_FOUND, "No such upload.") from None


def _signed_in_session(info: Info[Any, Any]) -> Session:
    session = getattr(info.context, "session", None)
    if not isinstance(session, Session):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return session


def _signed_in_user(info: Info[Any, Any]) -> User:
    return _signed_in_session(info).user
