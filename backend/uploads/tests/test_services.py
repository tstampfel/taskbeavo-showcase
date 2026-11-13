"""Claiming photos for their owning object, the sweep, deletion and the real R2 signer."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user
from core.errors import ErrorCode
from uploads import services, storage
from uploads.models import Purpose, Status, Upload
from uploads.tests.fakes import FakeR2, has_gps, photo_with_gps

pytestmark = pytest.mark.django_db

PRIVATE = "taskbeavo-private-test"
PUBLIC = "taskbeavo-public-test"


def ready_upload(
    r2: FakeR2, owner: User, purpose: Purpose = Purpose.TASK_PHOTO, capture: Any = None
) -> Upload:
    data = photo_with_gps()
    target = services.create_upload_url(owner, purpose, "image/jpeg", len(data), platform="ios")
    assert r2.client_put(target.url, data, "image/jpeg") == 200
    with capture(execute=True):
        services.complete_upload(owner, target.upload.pk)
    upload = Upload.objects.get(pk=target.upload.pk)
    assert upload.status == Status.READY
    return upload


def test_claiming_attaches_the_owners_ready_photos(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    first = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    second = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)

    claimed = services.claim_uploads(ana, [second.pk, first.pk], Purpose.TASK_PHOTO)

    assert [upload.pk for upload in claimed] == [first.pk, second.pk]
    assert Upload.objects.filter(attached_at__isnull=False).count() == 2


@pytest.mark.parametrize(
    "problem", ["other_owner", "wrong_purpose", "claimed", "pending", "unknown"]
)
def test_claiming_refuses_the_whole_set_for_any_bad_id(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, problem: str
) -> None:
    ana = make_user()
    good = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    if problem == "other_owner":
        bad = ready_upload(r2, make_user("marko"), capture=django_capture_on_commit_callbacks).pk
    elif problem == "wrong_purpose":
        bad = ready_upload(
            r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
        ).pk
    elif problem == "claimed":
        bad = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks).pk
        services.claim_uploads(ana, [bad], Purpose.TASK_PHOTO)
    elif problem == "pending":
        bad = services.create_upload_url(
            ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios"
        ).upload.pk
    else:
        bad = uuid4()

    with pytest.raises(services.UploadError) as refused:
        services.claim_uploads(ana, [good.pk, bad], Purpose.TASK_PHOTO)

    assert refused.value.code == ErrorCode.UPLOAD_NOT_FOUND
    good.refresh_from_db()
    assert good.attached_at is None


def test_sweep_deletes_abandoned_uploads_with_their_objects(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    claimed = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        services.claim_uploads(ana, [claimed.pk], Purpose.TASK_PHOTO)
    claimed.refresh_from_db()
    unclaimed = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    evidence = ready_upload(
        r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )
    pending = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios"
    ).upload
    r2.put_object(Bucket=PRIVATE, Key=pending.upload_key, Body=b"0123456789", ContentType="x")
    fresh = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    old = timezone.now() - timedelta(hours=49)
    Upload.objects.exclude(pk=fresh.pk).update(created_at=old)

    with django_capture_on_commit_callbacks(execute=True):
        deleted = services.sweep()

    assert deleted == 3
    assert set(Upload.objects.values_list("pk", flat=True)) == {claimed.pk, fresh.pk}
    # Only the claimed photo was published; the fresh one is still private.
    assert r2.keys_in(PUBLIC) == {claimed.public_key}
    assert r2.keys_in(PRIVATE) == {claimed.display_key, fresh.display_key}
    assert not Upload.objects.filter(pk__in=[unclaimed.pk, evidence.pk, pending.pk]).exists()


def test_sweep_requeues_uploads_stuck_in_processing(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    # The commit hook that would queue processing never runs: the message was lost.
    services.complete_upload(ana, target.upload.pk)
    Upload.objects.update(created_at=timezone.now() - timedelta(minutes=11))

    services.sweep()

    assert Upload.objects.get().status == Status.READY


def test_deleting_a_user_deletes_their_photos_from_r2(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    ready_upload(r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks)
    assert r2.objects

    # Account deletion cascades to the rows. (Not `ana.delete()` here: a test-only model
    # in core/tests/test_audit_and_transitions.py points at users without a table.)
    owner = Upload._meta.get_field("owner")
    assert isinstance(owner, models.ForeignKey)
    assert getattr(owner.remote_field, "on_delete", None) is models.CASCADE
    with django_capture_on_commit_callbacks(execute=True):
        Upload.objects.filter(owner=ana).delete()

    assert not Upload.objects.exists()
    assert r2.objects == {}


def test_storage_failures_are_retried_and_leave_the_upload_processing(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)

    with (
        mock.patch.object(r2, "put_object", side_effect=OSError("connection reset")),
        pytest.raises(storage.StorageError),
    ):
        services.process(target.upload.pk)

    assert Upload.objects.get().status == Status.PROCESSING
    assert services.process(target.upload.pk) == Status.READY


def test_the_real_client_signs_type_and_length_for_the_eu_endpoint() -> None:
    """botocore signs locally; no request leaves the machine."""
    storage.use_client(None)
    storage._r2_client.cache_clear()
    try:
        url = storage.presigned_put(PRIVATE, "users/1/task-photos/x/upload", "image/png", 4321)
    finally:
        storage._r2_client.cache_clear()

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "test-account.eu.r2.cloudflarestorage.com"
    assert parts.path == f"/{PRIVATE}/users/1/task-photos/x/upload"
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    assert query["X-Amz-Expires"] == ["600"]
    assert "x-amz-checksum-crc32" not in parts.query.lower()


def test_uploads_being_processed_count_towards_the_pending_cap(r2: FakeR2, settings: Any) -> None:
    settings.UPLOAD_MAX_PENDING_PER_USER = 1
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)  # PROCESSING; its commit hook never ran
    assert Upload.objects.get().status == Status.PROCESSING

    with pytest.raises(services.UploadError) as refused:
        services.create_upload_url(ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios")

    assert refused.value.code == ErrorCode.TOO_MANY_PENDING_UPLOADS


def test_sweep_gives_up_on_uploads_that_never_finished_processing(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)
    Upload.objects.update(created_at=timezone.now() - timedelta(hours=49))

    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep() == 1

    assert not Upload.objects.exists()
    assert r2.objects == {}


def test_objects_written_before_a_failed_run_are_deleted_with_the_row(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The evidence original lands, the display copy fails: no key was saved, yet the
    sweep still removes the original (keys are derived, not read from the row)."""
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.EVIDENCE_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)
    real_put = r2.put_object

    def fail_display(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Key"].endswith("/display.jpg"):
            raise OSError("connection reset")
        return real_put(**kwargs)

    with (
        mock.patch.object(r2, "put_object", side_effect=fail_display),
        pytest.raises(storage.StorageError),
    ):
        services.process(target.upload.pk)
    upload = Upload.objects.get()
    assert upload.original_key == "" and (PRIVATE, upload.original_key_for()) in r2.objects
    Upload.objects.update(created_at=timezone.now() - timedelta(hours=49))

    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep() == 1

    assert r2.objects == {}


def test_a_row_deleted_while_processing_takes_the_new_objects_with_it(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.TASK_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)
    real_put = r2.put_object

    def delete_row_then_put(**kwargs: Any) -> dict[str, Any]:
        with django_capture_on_commit_callbacks(execute=True):
            Upload.objects.filter(pk=target.upload.pk).delete()
        return real_put(**kwargs)

    with mock.patch.object(r2, "put_object", side_effect=delete_row_then_put):
        assert services.process(target.upload.pk) == "MISSING"

    assert r2.objects == {}


def test_the_sweep_never_deletes_a_photo_claimed_after_it_was_listed(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    Upload.objects.update(created_at=timezone.now() - timedelta(hours=49))
    now = timezone.now()
    assert list(services._sweepable(now).values_list("pk", flat=True)) == [upload.pk]

    # postTask claims it between the sweep's listing and its delete.
    services.claim_uploads(ana, [upload.pk], Purpose.TASK_PHOTO)

    assert services.delete_if_still_sweepable(upload.pk, now) == 0
    assert Upload.objects.filter(pk=upload.pk).exists()
    assert (PRIVATE, upload.display_key) in r2.objects


@pytest.fixture
def closed_at() -> datetime:
    return datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def test_task_photos_are_kept_30_days_after_their_task_closes(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, closed_at: datetime
) -> None:
    ana = make_user()
    upload = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [upload.pk], Purpose.TASK_PHOTO)

    services.set_retention([upload], closed_at=closed_at)

    upload.refresh_from_db()
    assert upload.delete_after == closed_at + timedelta(days=30)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep(closed_at + timedelta(days=29)) == 0
        assert services.sweep(closed_at + timedelta(days=30, seconds=1)) == 1
    assert r2.objects == {}


@pytest.mark.parametrize(
    ("outcome", "paid", "expected"),
    [
        (services.RetentionOutcome.CLOSED, None, datetime(2027, 5, 14, 12, 0, tzinfo=UTC)),
        (
            services.RetentionOutcome.NO_ACCESS_CALL_OUT,
            datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
            datetime(2028, 2, 29, 9, 0, tzinfo=UTC),
        ),
        (
            services.RetentionOutcome.PARTIAL_RELEASE,
            datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
            datetime(2028, 5, 1, 9, 0, tzinfo=UTC),
        ),
        (
            services.RetentionOutcome.REFUND,
            datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
            datetime(2028, 5, 1, 9, 0, tzinfo=UTC),
        ),
    ],
)
def test_evidence_is_kept_180_days_after_closing_or_18_months_after_a_payment_issue(
    r2: FakeR2,
    django_capture_on_commit_callbacks: Any,
    closed_at: datetime,
    outcome: services.RetentionOutcome,
    paid: datetime | None,
    expected: datetime,
) -> None:
    ana = make_user()
    upload = ready_upload(
        r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )

    services.set_retention([upload], closed_at=closed_at, outcome=outcome, paid_at=paid)

    upload.refresh_from_db()
    assert upload.delete_after == expected


def test_a_payment_issue_needs_the_payment_time(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, closed_at: datetime
) -> None:
    upload = ready_upload(
        r2, make_user(), Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )
    with pytest.raises(ValueError, match="paid_at"):
        services.set_retention(
            [upload], closed_at=closed_at, outcome=services.RetentionOutcome.REFUND
        )


def test_retention_is_never_shortened_and_follows_the_settings(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, closed_at: datetime, settings: Any
) -> None:
    upload = ready_upload(
        r2, make_user(), Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )
    settings.UPLOAD_EVIDENCE_RETENTION_DAYS = 7
    settings.UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS = 2
    paid = closed_at - timedelta(days=1)

    services.set_retention(
        [upload], closed_at=closed_at, outcome=services.RetentionOutcome.REFUND, paid_at=paid
    )
    services.set_retention([upload], closed_at=closed_at)  # 7 days: shorter, ignored

    upload.refresh_from_db()
    assert upload.delete_after == datetime(2027, 1, 14, 12, 0, tzinfo=UTC)


def test_a_legal_hold_keeps_uploads_until_resolved_plus_five_years(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, closed_at: datetime
) -> None:
    ana = make_user()
    evidence = ready_upload(
        r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )
    unclaimed = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [evidence.pk], Purpose.EVIDENCE_PHOTO)
    services.set_retention([evidence], closed_at=closed_at)
    services.place_legal_hold([evidence, unclaimed])
    Upload.objects.update(created_at=closed_at - timedelta(days=10))

    # Expired and abandoned, but held: the sweep leaves both alone.
    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep(closed_at + timedelta(days=400)) == 0
    assert Upload.objects.count() == 2

    resolved = datetime(2027, 3, 1, 10, 0, tzinfo=UTC)
    services.lift_legal_hold([evidence], resolved_at=resolved)
    evidence.refresh_from_db()
    assert evidence.legal_hold is False
    assert evidence.delete_after == datetime(2032, 3, 1, 10, 0, tzinfo=UTC)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep(datetime(2032, 2, 28, tzinfo=UTC)) == 0
        assert services.sweep(datetime(2032, 3, 2, tzinfo=UTC)) == 1
    assert set(Upload.objects.values_list("pk", flat=True)) == {unclaimed.pk}


def test_retention_settings_have_the_agreed_defaults() -> None:
    from django.conf import settings as django_settings

    assert django_settings.UPLOAD_UNATTACHED_RETENTION_HOURS == 48
    assert django_settings.UPLOAD_TASK_PHOTO_RETENTION_DAYS == 30
    assert django_settings.UPLOAD_EVIDENCE_RETENTION_DAYS == 180
    assert django_settings.UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS == 18
    assert django_settings.UPLOAD_LEGAL_HOLD_RETENTION_YEARS == 5


def _processing_evidence(r2: FakeR2) -> Upload:
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.EVIDENCE_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(ana, target.upload.pk)  # its commit hook never ran
    return Upload.objects.get(pk=target.upload.pk)


def test_a_second_run_never_touches_storage_while_the_first_holds_the_lease(
    r2: FakeR2,
) -> None:
    upload = _processing_evidence(r2)
    Upload.objects.filter(pk=upload.pk).update(processing_started_at=timezone.now())

    with mock.patch.object(r2, "get_object") as read, mock.patch.object(r2, "put_object") as put:
        assert services.process(upload.pk) == Status.PROCESSING

    read.assert_not_called()
    put.assert_not_called()


def test_an_expired_lease_is_taken_over(r2: FakeR2, settings: Any) -> None:
    upload = _processing_evidence(r2)
    expired = timezone.now() - timedelta(seconds=settings.UPLOAD_PROCESSING_LEASE_SECONDS + 1)
    Upload.objects.filter(pk=upload.pk).update(processing_started_at=expired)

    assert services.process(upload.pk) == Status.READY
    assert Upload.objects.get().processing_started_at is None


def test_a_run_that_lost_its_lease_does_not_save_over_the_new_one(r2: FakeR2) -> None:
    """The original swapped after the first read must not replace what the winner kept."""
    upload = _processing_evidence(r2)
    real_put = r2.put_object
    taken_over = timezone.now() + timedelta(seconds=1)

    def lose_lease_then_put(**kwargs: Any) -> dict[str, Any]:
        # The run outlived its lease; another run took the row over meanwhile.
        Upload.objects.filter(pk=upload.pk).update(processing_started_at=taken_over)
        return real_put(**kwargs)

    with mock.patch.object(r2, "put_object", side_effect=lose_lease_then_put):
        assert services.process(upload.pk) == Status.PROCESSING

    current = Upload.objects.get()
    assert (current.original_sha256, current.processed_at) == ("", None)
    assert current.processing_started_at == taken_over


def test_a_failed_run_gives_its_lease_back_for_the_retry(r2: FakeR2) -> None:
    upload = _processing_evidence(r2)

    with (
        mock.patch.object(r2, "get_object", side_effect=OSError("connection reset")),
        pytest.raises(storage.StorageError),
    ):
        services.process(upload.pk)

    assert Upload.objects.get().processing_started_at is None
    assert services.process(upload.pk) == Status.READY


def test_unclaimed_ready_photos_count_towards_the_pending_cap(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, settings: Any
) -> None:
    """READY photos nothing claims can't pile up: they hold a slot until claimed or swept."""
    settings.UPLOAD_MAX_PENDING_PER_USER = 2
    ana = make_user()
    first = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)

    with pytest.raises(services.UploadError) as refused:
        services.create_upload_url(ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios")
    assert refused.value.code == ErrorCode.TOO_MANY_PENDING_UPLOADS

    services.claim_uploads(ana, [first.pk], Purpose.TASK_PHOTO)
    services.create_upload_url(ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios")


def test_only_claimed_task_photos_are_published_and_evidence_never(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    evidence = ready_upload(
        r2, ana, Purpose.EVIDENCE_PHOTO, capture=django_capture_on_commit_callbacks
    )

    # Publishing an unclaimed photo (a stray or forged task message) does nothing.
    assert services.publish(photo.pk) == "SKIPPED"
    assert r2.keys_in(PUBLIC) == set()

    with django_capture_on_commit_callbacks(execute=True):
        services.claim_uploads(ana, [evidence.pk], Purpose.EVIDENCE_PHOTO)
    assert services.publish(evidence.pk) == "SKIPPED"
    assert r2.keys_in(PUBLIC) == set()

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)
    assert len(callbacks) == 1
    photo.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {f"photos/{photo.pk}.jpg"}
    assert photo.public_key == f"photos/{photo.pk}.jpg"
    # Running it again (a retry, a sweep re-queue) changes nothing.
    assert services.publish(photo.pk) == "SKIPPED"
    assert r2.keys_in(PUBLIC) == {photo.public_key}


def test_publishing_retries_storage_errors_and_the_sweep_requeues_it(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)  # its hook isn't run
    with (
        mock.patch.object(r2, "copy_object", side_effect=RuntimeError("R2 down")),
        pytest.raises(storage.StorageError),
    ):
        services.publish(photo.pk)
    photo.refresh_from_db()
    assert photo.public_key == "" and services.public_photo_url(photo) is None
    assert r2.keys_in(PUBLIC) == set()

    # A sweep while the retry chain may still run doesn't start a second one.
    with mock.patch("uploads.tasks.publish_upload.delay") as queued:
        services.sweep()
    queued.assert_not_called()

    # Every retry was lost; once the chain is surely over, the sweep tries again, and
    # not again before another hour has passed.
    Upload.objects.update(publish_queued_at=timezone.now() - timedelta(minutes=61))
    with mock.patch("uploads.tasks.publish_upload.delay") as queued:
        services.sweep()
        services.sweep()
    queued.assert_called_once_with(str(photo.pk))
    Upload.objects.update(publish_queued_at=timezone.now() - timedelta(minutes=61))
    services.sweep()

    photo.refresh_from_db()
    assert services.public_photo_url(photo) == (
        f"https://photos.test.taskbeavo.invalid/photos/{photo.pk}.jpg"
    )


def test_publish_task_is_retried_on_storage_errors() -> None:
    from uploads.tasks import publish_upload

    assert publish_upload.name == "uploads.publish"
    assert storage.StorageError in publish_upload.autoretry_for  # type: ignore[attr-defined]


def test_a_row_deleted_while_publishing_takes_the_public_copy_with_it(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)  # publishing not run yet
    real_copy = r2.copy_object

    def copy_then_row_deleted(**kwargs: Any) -> dict[str, Any]:
        result = real_copy(**kwargs)
        with django_capture_on_commit_callbacks(execute=True):
            Upload.objects.filter(pk=photo.pk).delete()
        return result

    with mock.patch.object(r2, "copy_object", side_effect=copy_then_row_deleted):
        assert services.publish(photo.pk) == "MISSING"

    assert r2.keys_in(PUBLIC) == set()
    assert r2.keys_in(PRIVATE) == set()


def test_deleting_a_published_photo_removes_its_public_and_private_copies(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    published = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        services.claim_uploads(ana, [published.pk], Purpose.TASK_PHOTO)
    unpublished = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    assert r2.keys_in(PUBLIC) and r2.keys_in(PRIVATE)

    with django_capture_on_commit_callbacks(execute=True):
        Upload.objects.filter(pk__in=[published.pk, unpublished.pk]).delete()

    assert r2.keys_in(PUBLIC) == set()
    assert r2.keys_in(PRIVATE) == set()


def test_a_failed_cleanup_delete_falls_back_to_the_retried_task(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """R2 refuses the delete of a deleted row's fresh public copy: the copy still goes."""
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)  # publishing not run yet
    real_copy, real_delete = r2.copy_object, r2.delete_object
    failures = iter([])

    def copy_then_row_deleted(**kwargs: Any) -> dict[str, Any]:
        nonlocal failures
        result = real_copy(**kwargs)
        with django_capture_on_commit_callbacks(execute=True):
            Upload.objects.filter(pk=photo.pk).delete()
        # R2 is down for publish's own cleanup delete.
        failures = iter([RuntimeError("R2 down")])
        return result

    def delete_failing_once(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Bucket"] == PUBLIC:
            failure = next(failures, None)
            if failure:
                raise failure
        return real_delete(**kwargs)

    with (
        mock.patch.object(r2, "copy_object", side_effect=copy_then_row_deleted),
        mock.patch.object(r2, "delete_object", side_effect=delete_failing_once),
        mock.patch("uploads.tasks.delete_objects.delay") as retried,
        # The signal's delayed second delete would run at once under eager Celery.
        mock.patch("uploads.tasks.delete_objects.apply_async"),
    ):
        assert services.publish(photo.pk) == "MISSING"

    # The last call; the first is the deletion signal's own (mocked here too).
    assert retried.call_args.args == ([[PUBLIC, f"photos/{photo.pk}.jpg"]],)
    assert r2.keys_in(PUBLIC) == {f"photos/{photo.pk}.jpg"}
    # The Celery task runs (and retries) the delete.
    from uploads.tasks import delete_objects

    delete_objects(*retried.call_args.args)
    assert r2.keys_in(PUBLIC) == set()


def test_a_publish_that_dies_after_copying_cannot_leave_a_deleted_photo_public(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The worker copies, the row is deleted, and the worker dies before its re-check."""
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)  # publishing not run yet
    public_key = f"photos/{photo.pk}.jpg"

    before = timezone.now()
    with (
        mock.patch("uploads.tasks.delete_objects.apply_async") as later,
        django_capture_on_commit_callbacks(execute=True),
    ):
        Upload.objects.filter(pk=photo.pk).delete()
    # The copy lands after the signal's first delete, and nothing re-checks the row.
    r2.copy_object(
        Bucket=PUBLIC, Key=public_key, CopySource={"Bucket": PRIVATE, "Key": photo.display_key}
    )
    assert r2.keys_in(PUBLIC) == {public_key}

    # `delay` is `apply_async` too: the last call is the delayed second delete.
    (keys,), options = later.call_args.args[0], later.call_args.kwargs
    assert keys == [list(key) for key in photo.stored_objects()]
    assert [PUBLIC, public_key] in keys
    assert options["eta"] >= before + services.PUBLISH_RETRY_WINDOW
    from uploads.tasks import delete_objects, publish_upload

    delete_objects(keys)
    assert r2.keys_in(PUBLIC) == set()
    # No run can still be copying by then.
    assert (publish_upload.time_limit or 0) * 20 < services.PUBLISH_RETRY_WINDOW.total_seconds()
    assert publish_upload.time_limit


def test_a_run_that_finds_the_photo_published_meanwhile_reports_skipped(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    photo = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    services.claim_uploads(ana, [photo.pk], Purpose.TASK_PHOTO)  # publishing not run yet
    real_copy = r2.copy_object

    def copy_while_another_run_finishes(**kwargs: Any) -> dict[str, Any]:
        Upload.objects.filter(pk=photo.pk).update(public_key=f"photos/{photo.pk}.jpg")
        return real_copy(**kwargs)

    with mock.patch.object(r2, "copy_object", side_effect=copy_while_another_run_finishes):
        assert services.publish(photo.pk) == "SKIPPED"


def test_the_owner_can_discard_unused_uploads_to_free_the_cap(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, settings: Any
) -> None:
    settings.UPLOAD_MAX_PENDING_PER_USER = 1
    ana = make_user()
    unused = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    assert r2.keys_in(PRIVATE)

    with django_capture_on_commit_callbacks(execute=True):
        assert services.discard_upload(ana, unused.pk) == unused.pk

    assert not Upload.objects.exists()
    assert r2.objects == {}
    services.create_upload_url(ana, Purpose.TASK_PHOTO, "image/jpeg", 10, platform="ios")


@pytest.mark.parametrize("problem", ["claimed", "held", "retained", "other_owner"])
def test_attached_or_retained_uploads_cannot_be_discarded(
    r2: FakeR2, django_capture_on_commit_callbacks: Any, problem: str
) -> None:
    ana = make_user()
    upload = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    caller = ana
    if problem == "claimed":
        services.claim_uploads(ana, [upload.pk], Purpose.TASK_PHOTO)
    elif problem == "held":
        services.place_legal_hold([upload])
    elif problem == "retained":
        services.set_retention([upload], closed_at=timezone.now())
    else:
        caller = make_user("marko")

    with pytest.raises(services.UploadError) as refused:
        services.discard_upload(caller, upload.pk)

    assert refused.value.code == ErrorCode.UPLOAD_NOT_FOUND
    assert Upload.objects.filter(pk=upload.pk).exists()


def test_a_row_without_an_etag_is_never_read(r2: FakeR2) -> None:
    r2.put_object(Bucket=PRIVATE, Key="k", Body=b"x", ContentType="image/jpeg")
    with (
        mock.patch.object(r2, "get_object") as get,
        pytest.raises(storage.ObjectChangedError),
    ):
        storage.read(PRIVATE, "k", 10, etag="")
    get.assert_not_called()


def test_a_put_after_discarding_a_pending_upload_is_deleted_after_the_url_expires(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The presigned PUT outlives the row; whatever it creates later is still removed."""
    ana = make_user()
    data = photo_with_gps()
    target = services.create_upload_url(
        ana, Purpose.EVIDENCE_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    upload = target.upload

    with (
        mock.patch("uploads.tasks.delete_objects.apply_async") as later,
        django_capture_on_commit_callbacks(execute=True),
    ):
        services.discard_upload(ana, upload.pk)
    # The PUT was in flight (or sent on purpose) and lands after the discard.
    assert r2.client_put(target.url, data, "image/jpeg") == 200
    assert (PRIVATE, upload.upload_key) in r2.objects
    # Nothing can use it: the row is gone.
    with pytest.raises(services.UploadError):
        services.complete_upload(ana, upload.pk)

    (keys,), options = later.call_args.args[0], later.call_args.kwargs
    assert [PRIVATE, upload.upload_key] in keys
    assert options["eta"] > upload.url_expires_at
    from uploads.tasks import delete_objects

    delete_objects(keys)
    assert r2.objects == {}


def _evidence_being_processed(r2: FakeR2, owner: User) -> tuple[Upload, bytes]:
    data = photo_with_gps()
    target = services.create_upload_url(
        owner, Purpose.EVIDENCE_PHOTO, "image/jpeg", len(data), platform="ios"
    )
    r2.client_put(target.url, data, "image/jpeg")
    services.complete_upload(owner, target.upload.pk)  # processing not run yet
    return Upload.objects.get(pk=target.upload.pk), data


def test_discarding_evidence_while_processing_falls_back_to_the_retried_delete(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The owner discards while processing writes the original (GPS and all), and R2
    refuses the run's cleanup delete: the retried task still removes it."""
    ana = make_user()
    upload, data = _evidence_being_processed(r2, ana)
    real_put, real_delete = r2.put_object, r2.delete_object
    failures = iter([])

    def put_then_discarded(**kwargs: Any) -> dict[str, Any]:
        nonlocal failures
        result = real_put(**kwargs)
        if kwargs["Key"].endswith("/display.jpg"):
            with django_capture_on_commit_callbacks(execute=True):
                services.discard_upload(ana, upload.pk)
            failures = iter([RuntimeError("R2 down")])
        return result

    def delete_failing_once(**kwargs: Any) -> dict[str, Any]:
        failure = next(failures, None)
        if failure:
            raise failure
        return real_delete(**kwargs)

    with (
        mock.patch.object(r2, "put_object", side_effect=put_then_discarded),
        mock.patch.object(r2, "delete_object", side_effect=delete_failing_once),
        mock.patch("uploads.tasks.delete_objects.delay") as retried,
        mock.patch("uploads.tasks.delete_objects.apply_async"),
    ):
        assert services.process(upload.pk) == "MISSING"

    original = upload.original_key_for()
    assert r2.body(PRIVATE, original) == data and has_gps(data)
    keys = retried.call_args.args[0]
    assert [PRIVATE, original] in keys
    from uploads.tasks import delete_objects

    delete_objects(keys)
    assert (PRIVATE, original) not in r2.objects
    assert r2.objects == {}


def test_a_processing_run_that_dies_after_writing_cannot_leave_an_evidence_original(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The run writes the original, the row is discarded, and the worker dies before its
    re-check: the deletion signal's second delete removes every key the row derives."""
    ana = make_user()
    upload, data = _evidence_being_processed(r2, ana)
    before = timezone.now()
    with (
        mock.patch("uploads.tasks.delete_objects.apply_async") as later,
        django_capture_on_commit_callbacks(execute=True),
    ):
        services.discard_upload(ana, upload.pk)
    # The dying run's writes land after the signal's first delete.
    original = upload.original_key_for()
    r2.put_object(Bucket=PRIVATE, Key=original, Body=data, ContentType="image/jpeg")
    r2.put_object(
        Bucket=PRIVATE, Key=upload.display_key_for(), Body=b"clean", ContentType="image/jpeg"
    )
    assert has_gps(r2.body(PRIVATE, original))

    (keys,), options = later.call_args.args[0], later.call_args.kwargs
    assert sorted(map(tuple, keys)) == sorted(upload.stored_objects())
    lease = timedelta(seconds=settings.UPLOAD_PROCESSING_LEASE_SECONDS)
    assert options["eta"] >= before + max(lease, services.PUBLISH_RETRY_WINDOW)
    from uploads.tasks import delete_objects

    delete_objects(keys)
    assert r2.objects == {}


# Owners that aren't public yet: claim without publishing, publish later, unpublish.


def claimed_private(r2: FakeR2, owner: User, capture: Any) -> Upload:
    upload = ready_upload(r2, owner, capture=capture)
    with capture(execute=True):
        services.claim_uploads(owner, [upload.pk], Purpose.TASK_PHOTO, publish=False)
    upload.refresh_from_db()
    return upload


def test_claiming_without_publishing_keeps_the_photo_private(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = claimed_private(r2, ana, django_capture_on_commit_callbacks)

    assert upload.attached_at is not None
    assert upload.publish_held and not upload.public_key
    assert r2.keys_in(PUBLIC) == set()
    # The sweep neither publishes nor deletes it, however old.
    Upload.objects.update(created_at=timezone.now() - timedelta(days=30))
    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep() == 0
    upload.refresh_from_db()
    assert upload.publish_held and not upload.public_key
    assert r2.keys_in(PUBLIC) == set()
    assert services.publish(upload.pk) == "SKIPPED"


def test_publish_claimed_publishes_after_the_commit(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = claimed_private(r2, ana, django_capture_on_commit_callbacks)

    with django_capture_on_commit_callbacks(execute=True):
        services.publish_claimed([upload])

    upload.refresh_from_db()
    assert not upload.publish_held
    assert upload.public_key and r2.keys_in(PUBLIC) == {upload.public_key}
    # Publishing again is harmless.
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        services.publish_claimed([upload])
    assert callbacks == []


def test_unpublish_deletes_the_public_copy_until_published_again(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        services.claim_uploads(ana, [upload.pk], Purpose.TASK_PHOTO)
    upload.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {upload.public_key}

    with django_capture_on_commit_callbacks(execute=True):
        services.unpublish([upload])

    upload.refresh_from_db()
    assert upload.publish_held and not upload.public_key
    assert r2.keys_in(PUBLIC) == set()
    assert r2.keys_in(PRIVATE) == {upload.display_key}
    Upload.objects.update(publish_queued_at=timezone.now() - timedelta(days=1))
    with django_capture_on_commit_callbacks(execute=True):
        services.sweep()
    assert r2.keys_in(PUBLIC) == set()

    with django_capture_on_commit_callbacks(execute=True):
        services.publish_claimed([upload])
    upload.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {upload.public_key}


def test_a_publishing_run_overtaken_by_unpublish_deletes_its_copy(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = ready_upload(r2, ana, capture=django_capture_on_commit_callbacks)
    # Claimed for publishing, but the queued run hasn't started yet.
    with django_capture_on_commit_callbacks(execute=False):
        services.claim_uploads(ana, [upload.pk], Purpose.TASK_PHOTO)
    copy = storage.copy

    def copy_then_unpublish(*args: Any) -> None:
        copy(*args)
        Upload.objects.filter(pk=upload.pk).update(publish_held=True)

    with mock.patch.object(storage, "copy", copy_then_unpublish):
        assert services.publish(upload.pk) == "SKIPPED"

    assert r2.keys_in(PUBLIC) == set()
    upload.refresh_from_db()
    assert not upload.public_key


def test_keep_until_sets_replaces_and_clears_the_date(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = claimed_private(r2, ana, django_capture_on_commit_callbacks)
    later = timezone.now() + timedelta(days=30)
    sooner = timezone.now() + timedelta(days=10)

    services.keep_until([upload], later)
    services.keep_until([upload], sooner)
    upload.refresh_from_db()
    assert upload.delete_after == sooner

    services.keep_until([upload], None)
    upload.refresh_from_db()
    assert upload.delete_after is None

    services.keep_until([upload], timezone.now() - timedelta(minutes=1))
    with django_capture_on_commit_callbacks(execute=True):
        assert services.sweep() == 1
    assert not Upload.objects.exists()


def test_keep_until_leaves_held_uploads_alone(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_user()
    upload = claimed_private(r2, ana, django_capture_on_commit_callbacks)
    services.place_legal_hold([upload])
    services.keep_until([upload], timezone.now())
    upload.refresh_from_db()
    assert upload.delete_after is None
