"""The upload flow: issue a presigned PUT, confirm arrival, process, claim and publish,
retain, sweep.

See uploads/models.py for what each purpose keeps and where, and how owning objects call
`claim_uploads`, `set_retention` and the legal hold.
"""

import calendar
import hashlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.gis.geos import Point
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from opentelemetry import trace

from accounts.models import Session, User
from core.errors import ErrorCode
from uploads import images, storage
from uploads.models import Purpose, Status, Upload

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

ALLOWED_CONTENT_TYPES = frozenset(images.FORMATS)
# Longer than a whole `uploads.publish` retry chain (about 31 minutes of backoff, each
# run capped by the task's 90 s time limit): no publish that started before a row was
# deleted can still be copying after this, and the sweep re-queues only after it.
PUBLISH_RETRY_WINDOW = timedelta(hours=1)


class UploadError(Exception):
    """A refused upload request; the resolver returns `code` to the client."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.name)
        self.code = code


class RetentionOutcome(models.TextChoices):
    """How the object owning an upload ended, which decides how long it is kept."""

    # A task closed or expired; a booking closed normally.
    CLOSED = "CLOSED"
    # A booking that ended in a payment issue: evidence is kept longer, from payment.
    NO_ACCESS_CALL_OUT = "NO_ACCESS_CALL_OUT"
    PARTIAL_RELEASE = "PARTIAL_RELEASE"
    REFUND = "REFUND"


@dataclass(frozen=True)
class UploadTarget:
    upload: Upload
    url: str


def create_upload_url(
    owner: User, purpose: Purpose, content_type: str, size_bytes: int, *, platform: str
) -> UploadTarget:
    """Record a PENDING upload and presign the client's PUT into the private bucket.

    `platform` is the signed-in session's (`Session.Platform`). Evidence is taken in the
    app, so WEB sessions (the web app's sign-in routes set WEB) are refused. The platform
    is client-declared: this is a UX guard, not a security boundary. Evidence trust comes
    only from server-side data (`received_at`, and BEAV-125's position check).
    """
    if purpose == Purpose.EVIDENCE_PHOTO and platform == Session.Platform.WEB:
        raise UploadError(ErrorCode.EVIDENCE_NEEDS_APP)
    content_type = content_type.strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadError(ErrorCode.UPLOAD_TYPE_NOT_ALLOWED)
    if not 0 < size_bytes <= settings.UPLOAD_MAX_BYTES:
        raise UploadError(ErrorCode.UPLOAD_TOO_LARGE)
    with transaction.atomic():
        # Serialises one user's requests, so parallel calls can't pass the cap together.
        User.objects.select_for_update().filter(pk=owner.pk).first()
        # Uploads nothing uses yet: live URLs, files still being processed, and READY
        # photos no task or booking has claimed (until the sweep deletes them).
        pending = (
            Upload.objects.filter(owner=owner)
            .filter(
                Q(status=Status.PENDING, url_expires_at__gt=timezone.now())
                | Q(status=Status.PROCESSING)
                | Q(status=Status.READY, attached_at__isnull=True, delete_after__isnull=True)
            )
            .count()
        )
        if pending >= settings.UPLOAD_MAX_PENDING_PER_USER:
            raise UploadError(ErrorCode.TOO_MANY_PENDING_UPLOADS)
        upload = Upload(
            owner=owner,
            purpose=purpose,
            content_type=content_type,
            size_bytes=size_bytes,
            url_expires_at=timezone.now() + timedelta(seconds=settings.UPLOAD_URL_TTL_SECONDS),
        )
        upload.upload_key = f"{_folder(owner, purpose, upload)}/upload"
        upload.save()
    url = storage.presigned_put(
        settings.R2_PRIVATE_BUCKET, upload.upload_key, content_type, size_bytes
    )
    return UploadTarget(upload, url)


def complete_upload(owner: User, upload_id: UUID) -> Upload:
    """Confirm the client's PUT arrived and queue processing. Repeat calls are harmless."""
    with transaction.atomic():
        upload = _own_upload(owner, upload_id, lock=True)
        if upload.status != Status.PENDING:
            return upload
        try:
            found = storage.head(settings.R2_PRIVATE_BUCKET, upload.upload_key)
        except storage.ObjectMissingError:
            raise UploadError(ErrorCode.UPLOAD_NOT_RECEIVED) from None
        # The signed PUT fixes both; a mismatch means the object isn't the one we signed.
        if (
            found.length != upload.size_bytes
            or found.content_type.lower() != upload.content_type
            or not found.etag
        ):
            storage.delete(settings.R2_PRIVATE_BUCKET, upload.upload_key)
            raise UploadError(ErrorCode.UPLOAD_NOT_RECEIVED)
        upload.status = Status.PROCESSING
        # The trusted time of an evidence photo; its EXIF time is only what the file claims.
        upload.received_at = timezone.now()
        # The URL stays valid after this call: processing reads only this exact object.
        upload.received_etag = found.etag
        upload.save(update_fields=["status", "received_at", "received_etag"])
        transaction.on_commit(lambda: _queue_processing(upload.pk))
    return upload


def get_own_upload(owner: User, upload_id: UUID) -> Upload:
    return _own_upload(owner, upload_id, lock=False)


def discard_upload(owner: User, upload_id: UUID) -> UUID:
    """Delete one of the owner's uploads nothing uses yet, freeing its slot in the cap.

    A claimed upload, or one with a retention date or a legal hold, belongs to its owning
    object and is refused as UPLOAD_NOT_FOUND. Its R2 objects go with the row (signals.py);
    a run still processing it finds the row gone and drops what it wrote.
    """
    with transaction.atomic():
        upload = _own_upload(owner, upload_id, lock=True)
        if upload.attached_at is not None or upload.delete_after or upload.legal_hold:
            raise UploadError(ErrorCode.UPLOAD_NOT_FOUND)
        upload.delete()
    return upload_id


def process(upload_id: UUID) -> str:
    """Check and re-encode one PROCESSING upload; returns the status it ends in.

    A run first takes the row's processing lease under its lock, so only one run at a time
    reads the upload and writes the kept objects; a duplicate run (a retry, a re-queue)
    returns while the lease is held. The R2 reads and writes and the decoding run outside a
    transaction, and only the lease holder saves the result. Storage errors and Celery's
    soft time limit propagate, after giving the lease back, so the task retries and the row
    stays PROCESSING; the sweep re-queues it if every retry was lost, and deletes it after
    UPLOAD_UNATTACHED_RETENTION_HOURS. A run killed at the hard time limit holds the lease
    until UPLOAD_PROCESSING_LEASE_SECONDS, longer than that limit, have passed.
    """
    with tracer.start_as_current_span("uploads.process") as span:
        span.set_attribute("upload.id", str(upload_id))
        with transaction.atomic():
            upload = Upload.objects.select_for_update().filter(pk=upload_id).first()
            if upload is None or upload.status != Status.PROCESSING:
                return upload.status if upload else "MISSING"
            if _lease_held(upload):
                return upload.status
            lease = timezone.now()
            upload.processing_started_at = lease
            upload.save(update_fields=["processing_started_at"])
        span.set_attribute("upload.purpose", upload.purpose)
        try:
            result = _check_and_store(upload)
        except BaseException:
            Upload.objects.filter(pk=upload_id, processing_started_at=lease).update(
                processing_started_at=None
            )
            raise
        with transaction.atomic():
            current = Upload.objects.select_for_update().filter(pk=upload_id).first()
            if current is not None and (
                current.status != Status.PROCESSING or current.processing_started_at != lease
            ):
                # Another run took over after this one's lease expired; its result stands.
                return current.status
            if current is not None:
                result.apply(current)
                current.processing_started_at = None
                current.save()
        if current is None:
            # Deleted meanwhile (discardUpload, account deletion), maybe before its objects
            # were written: drop them, through the retried task if R2 refuses now.
            delete_now_or_later(upload.stored_objects())
            return "MISSING"
        span.set_attribute("upload.status", current.status)
        _forget_upload_key(current)
        return current.status


def _lease_held(upload: Upload) -> bool:
    started = upload.processing_started_at
    lease = timedelta(seconds=settings.UPLOAD_PROCESSING_LEASE_SECONDS)
    return started is not None and started > timezone.now() - lease


def claim_uploads(
    owner: User, upload_ids: Sequence[UUID], purpose: Purpose, *, publish: bool = True
) -> list[Upload]:
    """Attach the owner's READY, unclaimed photos to an object being saved.

    Call it inside the owning object's transaction (e.g. postTask), then link the returned
    rows to the object. Any ID that isn't the owner's, isn't READY, has another purpose or
    is already claimed refuses the whole set with UPLOAD_NOT_FOUND. Claimed task photos
    are published (`publish`) once the transaction commits, unless `publish` is False
    (an owner nobody else sees yet): they then wait for `publish_claimed`.
    """
    wanted = list(dict.fromkeys(upload_ids))
    found = list(
        Upload.objects.select_for_update()
        .filter(
            pk__in=wanted,
            owner=owner,
            purpose=purpose,
            status=Status.READY,
            attached_at__isnull=True,
        )
        .order_by("created_at")
    )
    if len(found) != len(wanted):
        raise UploadError(ErrorCode.UPLOAD_NOT_FOUND)
    now = timezone.now()
    publishing = purpose == Purpose.TASK_PHOTO and publish
    queued = now if publishing else None
    held = purpose == Purpose.TASK_PHOTO and not publish
    Upload.objects.filter(pk__in=wanted).update(
        attached_at=now, publish_queued_at=queued, publish_held=held
    )
    for upload in found:
        upload.attached_at = now
        upload.publish_queued_at = queued
        upload.publish_held = held
        if publishing:
            transaction.on_commit(lambda pk=upload.pk: _queue_publishing(pk))
    return found


def publish_claimed(uploads: Iterable[Upload]) -> None:
    """Publish claimed task photos kept private so far (`claim_uploads(publish=False)`,
    `unpublish`), once their owner is visible. Published or queued photos are left alone.
    Call it inside the owner's transaction; publishing runs after the commit."""
    now = timezone.now()
    with transaction.atomic():
        for upload in _locked(uploads):
            if upload.purpose != Purpose.TASK_PHOTO or not upload.publish_held:
                continue
            upload.publish_held = False
            upload.publish_queued_at = now
            upload.save(update_fields=["publish_held", "publish_queued_at"])
            transaction.on_commit(lambda pk=upload.pk: _queue_publishing(pk))


def unpublish(uploads: Iterable[Upload]) -> None:
    """Keep claimed task photos private again (their owner is hidden, e.g. held for
    review): the public copy is deleted after the commit, and nothing republishes them
    until `publish_claimed`. A publishing run already copying deletes its own copy
    (`publish`)."""
    with transaction.atomic():
        for upload in _locked(uploads):
            if upload.purpose != Purpose.TASK_PHOTO or upload.publish_held:
                continue
            public_key = upload.public_key
            upload.publish_held = True
            upload.public_key = ""
            upload.save(update_fields=["publish_held", "public_key"])
            if public_key:
                keys = [(settings.R2_PUBLIC_BUCKET, public_key)]
                transaction.on_commit(lambda keys=keys: delete_now_or_later(keys))


def keep_until(uploads: Iterable[Upload], until: datetime | None) -> None:
    """Set when claimed uploads are deleted while their owner may still be abandoned (a
    draft): `until` replaces any earlier date, and None keeps them until `set_retention`.
    Uploads under a legal hold keep their date."""
    with transaction.atomic():
        for upload in _locked(uploads):
            if upload.legal_hold or upload.delete_after == until:
                continue
            upload.delete_after = until
            upload.save(update_fields=["delete_after"])


def publish(upload_id: UUID) -> str:
    """Copy a claimed task photo's clean copy to the public bucket; returns the outcome.

    Idempotent: a published, unclaimed, deleted or non-task upload is left alone, and a
    second copy to the same key writes the same bytes. Storage errors propagate so the
    Celery task retries. The copy runs outside a transaction. A row deleted meanwhile
    can't leave the copy public: this run deletes it (through the retried
    `uploads.delete_objects` task if R2 refuses now), and if the worker dies before that,
    the second delete the deletion signal schedules (`second_delete_after`) removes it.
    """
    with tracer.start_as_current_span("uploads.publish") as span:
        span.set_attribute("upload.id", str(upload_id))
        upload = Upload.objects.filter(pk=upload_id).first()
        if upload is None or not _awaits_publishing(upload):
            return "SKIPPED"
        public_key = upload.public_key_for()
        storage.copy(
            settings.R2_PRIVATE_BUCKET,
            upload.display_key,
            settings.R2_PUBLIC_BUCKET,
            public_key,
        )
        with transaction.atomic():
            current = Upload.objects.select_for_update().filter(pk=upload_id).first()
            published = current is not None and _awaits_publishing(current)
            if current is not None and published:
                current.public_key = public_key
                current.save(update_fields=["public_key"])
        if current is None:
            # Deleted meanwhile, maybe after its objects were already deleted.
            delete_now_or_later([(settings.R2_PUBLIC_BUCKET, public_key)])
            return "MISSING"
        if not published:
            if current.public_key != public_key:
                # Unpublished (or unclaimed) meanwhile: this run's copy must not stay public.
                delete_now_or_later([(settings.R2_PUBLIC_BUCKET, public_key)])
            # Otherwise another run published it first; its result stands.
            return "SKIPPED"
        span.set_attribute("upload.published", True)
        return "PUBLISHED"


def delete_now_or_later(keys: list[tuple[str, str]]) -> None:
    """Delete objects now, or through the retried `uploads.delete_objects` task if R2
    refuses: a row that is already gone can't be found again to clean up after."""
    try:
        delete_objects(keys)
    except storage.StorageError:
        from uploads.tasks import delete_objects as delete_task

        logger.warning("R2 objects not deleted yet; retrying in the background")
        delete_task.delay([list(key) for key in keys])


def second_delete_after(upload: Upload) -> datetime:
    """When a deleted row's objects are deleted again (signals.py), by which time nothing
    can still write them: its presigned PUT has expired, and any processing or publishing
    run that started before the deletion has ended (its lease or PUBLISH_RETRY_WINDOW)."""
    lease = timedelta(seconds=settings.UPLOAD_PROCESSING_LEASE_SECONDS)
    return max(timezone.now() + max(PUBLISH_RETRY_WINDOW, lease), upload.url_expires_at)


def _awaits_publishing(upload: Upload) -> bool:
    return (
        upload.purpose == Purpose.TASK_PHOTO
        and upload.status == Status.READY
        and upload.attached_at is not None
        and bool(upload.display_key)
        and not upload.public_key
        and not upload.publish_held
    )


def read_url(upload: Upload) -> str | None:
    """Where the owner can fetch a READY photo: the public copy once published, otherwise
    a short-lived GET of the private clean copy. Only for the owner's own `upload` query."""
    if upload.status != Status.READY:
        return None
    if upload.public_key:
        return storage.public_url(upload.public_key)
    if upload.display_key:
        return storage.presigned_get(settings.R2_PRIVATE_BUCKET, upload.display_key)
    return None


def public_photo_url(upload: Upload) -> str | None:
    """Where anyone can fetch a published task photo; None until it is published."""
    if upload.status != Status.READY or not upload.public_key:
        return None
    return storage.public_url(upload.public_key)


def set_retention(
    uploads: Iterable[Upload],
    *,
    closed_at: datetime,
    outcome: RetentionOutcome = RetentionOutcome.CLOSED,
    paid_at: datetime | None = None,
) -> None:
    """Start the retention clock of an owner's uploads when the owner closes.

    Task photos are kept UPLOAD_TASK_PHOTO_RETENTION_DAYS after `closed_at`. Evidence is
    kept UPLOAD_EVIDENCE_RETENTION_DAYS after `closed_at`, or, for a booking that ended
    in a no-access call-out, a partial release or a refund,
    UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS after `paid_at`. A later call never
    shortens what an earlier one set.
    """
    if outcome != RetentionOutcome.CLOSED and paid_at is None:
        raise ValueError(f"{outcome} retention runs from paid_at; pass it.")
    with transaction.atomic():
        for upload in _locked(uploads):
            if upload.purpose == Purpose.TASK_PHOTO:
                until = closed_at + timedelta(days=settings.UPLOAD_TASK_PHOTO_RETENTION_DAYS)
            elif outcome == RetentionOutcome.CLOSED:
                until = closed_at + timedelta(days=settings.UPLOAD_EVIDENCE_RETENTION_DAYS)
            else:
                until = _add_months(
                    paid_at or closed_at, settings.UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS
                )
            _extend(upload, until)


def place_legal_hold(uploads: Iterable[Upload]) -> None:
    """An open dispute, chargeback or claim: keep these uploads until it is resolved."""
    with transaction.atomic():
        for upload in _locked(uploads):
            upload.legal_hold = True
            upload.save(update_fields=["legal_hold"])


def lift_legal_hold(uploads: Iterable[Upload], *, resolved_at: datetime) -> None:
    """The hold's matter is resolved: keep the uploads UPLOAD_LEGAL_HOLD_RETENTION_YEARS more."""
    with transaction.atomic():
        for upload in _locked(uploads):
            upload.legal_hold = False
            _extend(
                upload, _add_months(resolved_at, 12 * settings.UPLOAD_LEGAL_HOLD_RETENTION_YEARS)
            )


def sweep(now: datetime | None = None) -> int:
    """Beat task body: delete expired and abandoned uploads, re-queue stuck processing and
    publishing.

    Expired: past `delete_after`. Abandoned, once older than
    UPLOAD_UNATTACHED_RETENTION_HOURS: PENDING, REJECTED, still PROCESSING (every retry
    failed), or READY and never claimed. Nothing under a legal hold is deleted. Each row
    is re-checked under its lock before deletion, so a claim, a new retention date or a
    hold that lands meanwhile wins. R2 objects go with the rows (signals.py).
    """
    now = now or timezone.now()
    due = _sweepable(now)
    deleted = 0
    for upload_id in list(due.values_list("pk", flat=True)):
        deleted += delete_if_still_sweepable(upload_id, now)
    stuck = Upload.objects.filter(
        status=Status.PROCESSING, created_at__lt=now - timedelta(minutes=10)
    ).values_list("pk", flat=True)
    # Each sweep tries a stuck upload again until the retention cutoff deletes it.
    for upload_id in stuck:
        _queue_processing(upload_id)
    # Claimed photos whose publishing was lost or ran out of retries: once the last chain
    # is surely over, so chains never overlap and a broken photo is retried hourly.
    unpublished = list(
        Upload.objects.filter(
            purpose=Purpose.TASK_PHOTO,
            status=Status.READY,
            attached_at__isnull=False,
            public_key="",
            publish_held=False,
        )
        .filter(
            Q(publish_queued_at__isnull=True) | Q(publish_queued_at__lt=now - PUBLISH_RETRY_WINDOW)
        )
        .values_list("pk", flat=True)
    )
    Upload.objects.filter(pk__in=unpublished).update(publish_queued_at=now)
    for upload_id in unpublished:
        logger.warning(
            "Task photo still unpublished; queued again", extra={"upload.id": str(upload_id)}
        )
        _queue_publishing(upload_id)
    return deleted


def delete_if_still_sweepable(upload_id: UUID, now: datetime) -> int:
    """Delete one upload if it is still due, skipping it while someone else holds it."""
    with transaction.atomic():
        found = list(_sweepable(now).select_for_update(skip_locked=True).filter(pk=upload_id))
        for upload in found:
            upload.delete()
    return len(found)


def _sweepable(now: datetime) -> models.QuerySet[Upload]:
    cutoff = now - timedelta(hours=settings.UPLOAD_UNATTACHED_RETENTION_HOURS)
    # An owner that set a retention date decides when the upload goes, claimed or not.
    abandoned = Q(created_at__lt=cutoff, delete_after__isnull=True) & (
        Q(status__in=[Status.PENDING, Status.REJECTED, Status.PROCESSING])
        | Q(status=Status.READY, attached_at__isnull=True)
    )
    return Upload.objects.filter(legal_hold=False).filter(Q(delete_after__lt=now) | abandoned)


def _locked(uploads: Iterable[Upload]) -> list[Upload]:
    ids = [upload.pk for upload in uploads]
    return list(Upload.objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def _extend(upload: Upload, until: datetime) -> None:
    if upload.delete_after is None or upload.delete_after < until:
        upload.delete_after = until
    upload.save(update_fields=["delete_after", "legal_hold"])


def _add_months(moment: datetime, months: int) -> datetime:
    """The same day `months` later, clamped to the month's end (31 Jan + 1 = 28/29 Feb)."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def delete_objects(keys: Iterable[tuple[str, str]]) -> None:
    for bucket, key in keys:
        storage.delete(bucket, key)


@dataclass
class _Result:
    """What processing found, saved onto the locked row."""

    status: str
    rejection_code: str = ""
    width: int | None = None
    height: int | None = None
    original_key: str = ""
    original_sha256: str = ""
    display_key: str = ""
    exif_taken_at: datetime | None = None
    exif_location: Point | None = None

    def apply(self, upload: Upload) -> None:
        for field, value in vars(self).items():
            setattr(upload, field, value)
        upload.processed_at = timezone.now()


def _check_and_store(upload: Upload) -> _Result:
    try:
        data = storage.read(
            settings.R2_PRIVATE_BUCKET,
            upload.upload_key,
            upload.size_bytes,
            etag=upload.received_etag,
        )
    except storage.ObjectChangedError:
        # PUT again after completeUpload: not the file that was confirmed. Nothing is kept
        # and the upload key is deleted after (process).
        return _rejected(upload, ErrorCode.UPLOAD_CHANGED)
    try:
        if len(data) != upload.size_bytes:
            raise images.InvalidImageError
        original = images.open_checked(data, upload.content_type)
        clean = images.clean_copy(original, upload.content_type)
        capture = images.capture_of(original) if upload.purpose == Purpose.EVIDENCE_PHOTO else None
    except images.ImageTooLargeError:
        return _rejected(upload, ErrorCode.UPLOAD_TOO_LARGE)
    except images.InvalidImageError:
        return _rejected(upload, ErrorCode.UPLOAD_INVALID_IMAGE)
    except (SoftTimeLimitExceeded, storage.StorageError):
        # Slow or unreachable, not a bad file: retried, never rejected.
        raise
    except Exception as exc:
        # A decoder crash on a crafted file must not leave the upload processing forever.
        logger.warning(
            "Upload rejected after an image library error",
            extra={"upload.id": str(upload.pk), "error.type": type(exc).__name__},
        )
        return _rejected(upload, ErrorCode.UPLOAD_INVALID_IMAGE)

    result = _Result(Status.READY, width=clean.width, height=clean.height)
    if capture is None:
        # Only the clean copy is kept, privately until a task claims it (`publish`): the
        # upload, with its GPS position, is deleted after.
        result.display_key = upload.display_key_for()
        storage.put(settings.R2_PRIVATE_BUCKET, result.display_key, clean.data, upload.content_type)
    else:
        # Keep the bytes that were checked, at a key no client was ever given a URL for.
        result.original_key = upload.original_key_for()
        result.original_sha256 = hashlib.sha256(data).hexdigest()
        storage.put(settings.R2_PRIVATE_BUCKET, result.original_key, data, upload.content_type)
        result.display_key = upload.display_key_for()
        storage.put(settings.R2_PRIVATE_BUCKET, result.display_key, clean.data, upload.content_type)
        result.exif_taken_at = capture.taken_at
        result.exif_location = capture.location
    return result


def _rejected(upload: Upload, code: ErrorCode) -> _Result:
    # An expected outcome for a bad file, not an error: logged with the ID only.
    logger.info("Upload rejected", extra={"upload.id": str(upload.pk), "code": code.name})
    return _Result(Status.REJECTED, rejection_code=code.name)


def _forget_upload_key(upload: Upload) -> None:
    """Delete the presigned upload key now, and again once its URL has expired."""
    bucket_key = (settings.R2_PRIVATE_BUCKET, upload.upload_key)
    try:
        storage.delete(*bucket_key)
    except storage.StorageError:
        logger.warning("Upload key not deleted yet", extra={"upload.id": str(upload.pk)})
    delete_later([bucket_key], max(upload.url_expires_at, timezone.now()))


def _own_upload(owner: User, upload_id: UUID, *, lock: bool) -> Upload:
    uploads = Upload.objects.select_for_update() if lock else Upload.objects
    # Someone else's upload is "not found" too, so IDs can't be probed.
    upload = uploads.filter(pk=upload_id, owner=owner).first()
    if upload is None:
        raise UploadError(ErrorCode.UPLOAD_NOT_FOUND)
    return upload


def _folder(owner: User, purpose: Purpose, upload: Upload) -> str:
    kind = "task-photos" if purpose == Purpose.TASK_PHOTO else "evidence"
    return f"users/{owner.pk}/{kind}/{upload.id}"


def delete_later(objects: list[tuple[str, str]], after: datetime) -> None:
    """Delete objects a minute after `after`, once the transaction commits."""
    from uploads.tasks import delete_objects

    keys = [[bucket, key] for bucket, key in objects]
    eta = after + timedelta(seconds=60)
    transaction.on_commit(lambda: delete_objects.apply_async((keys,), eta=eta))


def _queue_processing(upload_id: UUID) -> None:
    from uploads.tasks import process_upload

    process_upload.delay(str(upload_id))


def _queue_publishing(upload_id: UUID) -> None:
    from uploads.tasks import publish_upload

    publish_upload.delay(str(upload_id))
