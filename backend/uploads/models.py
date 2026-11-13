"""Photos uploaded straight to Cloudflare R2 with presigned URLs.

An `Upload` is one photo and the R2 keys that hold it. The flow (uploads/services.py):

1. `createUploadUrl` checks the type and size and returns a presigned PUT into the
   private bucket at `upload_key`, `users/<user id>/<purpose>/<upload id>/upload`.
   Evidence photos are refused from web sessions: they must be taken in the app.
2. The client PUTs the file to R2 itself; the API never receives the bytes.
3. `completeUpload` checks the object arrived, records `received_at` (the trusted
   server time) and the object's ETag (`received_etag`), and queues `uploads.process`,
   which reads exactly that object once (If-Match on the ETag: a file PUT again after
   `completeUpload` is rejected as UPLOAD_CHANGED and deleted) and re-encodes it without
   metadata (uploads/images.py):
   - a task photo's clean copy is stored in the private bucket at `display_key`, and the
     owner alone sees it through short-lived URLs. It goes public only once a task
     claims it (step 4), so nobody can host files on the public bucket by uploading
     photos no task uses;
   - an evidence photo's checked bytes are kept at `original_key` with their SHA-256,
     whatever capture time and GPS position its EXIF claims go into `exif_taken_at` and
     `exif_location` (untrusted: EXIF can be forged; never exposed by the API), and a
     clean copy is stored next to it for the people allowed to see it.
   Then `upload_key` is deleted, and deleted again once its URL has expired: the
   presigned PUT stays usable until then, so whatever is PUT there after processing is
   never read, kept or trusted. Kept files live only at keys that were never presigned,
   and every key is derived from the row's ID and type (`stored_objects`), so deleting a
   row finds every object even when processing failed before saving its keys.
4. `claim_uploads` attaches READY photos to their owning object. For a task photo it
   queues `uploads.publish` once the claim commits, which copies the clean copy to the
   public bucket at `public_key` (retried; the sweep re-queues it at most hourly).
   `public_key` is set only once the copy exists.
   The owner can delete an upload nothing claimed with `discardUpload`, freeing its slot
   in UPLOAD_MAX_PENDING_PER_USER.

GPS evidence: the trusted device position at the door, the location prompt and the
100 m check against the task address belong to BEAV-125. The app will send the position
with `completeUpload` for EVIDENCE_PHOTO and the server will store it next to
`received_at`; nothing in this module trusts EXIF GPS.

How owning objects use uploads (tasks BEAV-85, task edits BEAV-107, bookings BEAV-116,
no-access BEAV-125, disputes BEAV-126):

- Point at `Upload` rows (a foreign key or many-to-many) and attach them inside the
  owning transaction with `uploads.services.claim_uploads(owner, ids, purpose)`.
  Unclaimed uploads are deleted after `UPLOAD_UNATTACHED_RETENTION_HOURS`.
- Claiming a task photo triggers its publishing, which runs after the commit. A task
  page (BEAV-85, BEAV-86, BEAV-87) shows a photo only once it is published: use
  `uploads.services.public_photo_url(upload)`, which is None until then, never the
  owner's `read_url`.
- An owner that isn't public yet (a draft, a task held for review) claims with
  `claim_uploads(..., publish=False)`: the photo is attached but `publish_held`, so it
  stays private. `publish_claimed(uploads)` publishes it once the owner is visible, and
  `unpublish(uploads)` deletes the public copy if the owner is hidden again.
- An owner that may be abandoned before it closes (a draft) sets
  `keep_until(uploads, until)`, replacing any earlier date, and `keep_until(uploads,
  None)` once it is published.
- When the owner closes, call `uploads.services.set_retention(uploads, closed_at=...,
  outcome=...)`: a task that closes or expires (task photos, kept
  `UPLOAD_TASK_PHOTO_RETENTION_DAYS` more), a booking that closes (evidence, kept
  `UPLOAD_EVIDENCE_RETENTION_DAYS`), or one that ends in a no-access call-out, a partial
  release or a refund (evidence, kept `UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS`
  after `paid_at`). Until then `delete_after` is empty and the upload is kept.
- An open dispute, chargeback or claim calls `place_legal_hold(uploads)`; nothing held
  is ever swept. Resolving it calls `lift_legal_hold(uploads, resolved_at=...)`, which
  keeps the uploads `UPLOAD_LEGAL_HOLD_RETENTION_YEARS` more.
- The Beat task `uploads.sweep` deletes expired, unheld rows; their R2 objects go with
  them (uploads/signals.py).

Personal data: the photo itself, and for evidence the capture time and position. Rows
cascade with their user; deleting a row deletes its R2 objects. Account deletion must be
blocked while a dispute is open (gdpr-data-request), since a cascade ignores legal holds.
Keys hold only IDs. Never log a key or put it on a span.
"""

import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

# Declared content type -> file extension of the stored copies (uploads/images.py).
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class Purpose(models.TextChoices):
    """What a photo is for, which decides who may see it and what is kept."""

    # Shown to other users (task pages, profiles): only a clean copy is kept, published
    # once a task claims it.
    TASK_PHOTO = "TASK_PHOTO"
    # Proof for a booking (no-access, damage): the original stays private, with its time
    # and GPS position kept server-side.
    EVIDENCE_PHOTO = "EVIDENCE_PHOTO"


class Status(models.TextChoices):
    PENDING = "PENDING"  # URL issued, waiting for the client's PUT and completeUpload
    PROCESSING = "PROCESSING"  # arrived, being checked and re-encoded
    READY = "READY"
    REJECTED = "REJECTED"  # not a valid image of the declared type; objects deleted


class Upload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="uploads"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    # What the client declared and the presigned PUT signed; R2 refuses any other.
    content_type = models.CharField(max_length=40)
    size_bytes = models.PositiveIntegerField()
    url_expires_at = models.DateTimeField()

    # Private bucket: where the presigned PUT lands. Only ever read once, by processing.
    upload_key = models.CharField(max_length=200)
    # The ETag `completeUpload` saw at `upload_key`: processing reads only that object.
    received_etag = models.CharField(max_length=100, blank=True)
    # Private bucket, evidence only: the bytes processing checked, and their SHA-256.
    original_key = models.CharField(max_length=200, blank=True)
    original_sha256 = models.CharField(max_length=64, blank=True)
    # Public bucket: the published copy of a claimed task photo. Empty until published.
    public_key = models.CharField(max_length=200, blank=True)
    # Private bucket: the metadata-free copy, for the owner (task photo, until published)
    # and the people allowed to see evidence.
    display_key = models.CharField(max_length=200, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # An `ErrorCode` name when status is REJECTED.
    rejection_code = models.CharField(max_length=40, blank=True)

    # When the server confirmed the object arrived: the trusted time of an evidence photo.
    received_at = models.DateTimeField(null=True, blank=True)
    # Evidence only, as the original's EXIF claims them. Untrusted (EXIF can be forged) and
    # server-side: no GraphQL field exposes them.
    exif_taken_at = models.DateTimeField(null=True, blank=True)
    exif_location = gis_models.PointField(srid=4326, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    # The lease of the processing run under way (services.process); empty when none is.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    # Set when the owning object (a task, a no-access report) claims the photo.
    attached_at = models.DateTimeField(null=True, blank=True)
    # Task photos: when publishing was last queued (the claim, or a sweep re-queue).
    publish_queued_at = models.DateTimeField(null=True, blank=True)
    # Claimed but kept private until the owner is visible (`publish_claimed`).
    publish_held = models.BooleanField(default=False)
    # Set by the owner when it closes (services.set_retention); the sweep deletes the row
    # after it. Empty: kept while the owner is open.
    delete_after = models.DateTimeField(null=True, blank=True)
    # An open dispute, chargeback or claim: never swept while set.
    legal_hold = models.BooleanField(default=False)

    class Meta:
        indexes: ClassVar = [
            models.Index(fields=["owner", "status"], name="upload_owner_status"),
            models.Index(fields=["status", "created_at"], name="upload_status_created"),
            models.Index(fields=["delete_after"], name="upload_delete_after"),
        ]

    def __str__(self) -> str:
        return f"Upload {self.pk} ({self.purpose}, {self.status})"

    @property
    def extension(self) -> str:
        return EXTENSIONS.get(self.content_type, "bin")

    @property
    def folder(self) -> str:
        return self.upload_key.rsplit("/", 1)[0]

    def public_key_for(self) -> str:
        return f"photos/{self.pk}.{self.extension}"

    def original_key_for(self) -> str:
        return f"{self.folder}/original.{self.extension}"

    def display_key_for(self) -> str:
        return f"{self.folder}/display.{self.extension}"

    def stored_objects(self) -> list[tuple[str, str]]:
        """Every R2 object this upload may have, as (bucket, key).

        Derived from the ID and type rather than the saved key fields, so a run that wrote
        objects and then failed before saving still leaves nothing behind. Deleting a
        missing object succeeds, so listing keys that were never written is harmless.
        """
        if not self.upload_key:
            return []
        private = settings.R2_PRIVATE_BUCKET
        if self.purpose == Purpose.TASK_PHOTO:
            return [
                (private, self.upload_key),
                (private, self.display_key_for()),
                (settings.R2_PUBLIC_BUCKET, self.public_key_for()),
            ]
        return [
            (private, self.upload_key),
            (private, self.original_key_for()),
            (private, self.display_key_for()),
        ]
