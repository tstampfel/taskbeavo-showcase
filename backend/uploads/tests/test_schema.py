"""Photo uploads end to end over /graphql: presign, the client's PUT to (fake) R2,
completeUpload, processing, and what each purpose keeps."""

import hashlib
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.test import Client
from django.utils import timezone
from PIL import Image
from PIL.ExifTags import IFD

from accounts.models import Session
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from config.schema import schema
from uploads import services, storage
from uploads.models import Purpose, Status, Upload
from uploads.tests.fakes import FakeR2, has_gps, photo_exif, photo_with_gps

pytestmark = pytest.mark.django_db

PRIVATE = "taskbeavo-private-test"
PUBLIC = "taskbeavo-public-test"

CREATE = """
mutation($input: CreateUploadUrlInput!) {
  createUploadUrl(input: $input) {
    id url method contentType expiresAt
    upload { id purpose status contentType sizeBytes url }
  }
}
"""
FIELDS = "id purpose status contentType sizeBytes width height rejectionCode url"
COMPLETE = f"mutation($id: ID!) {{ completeUpload(id: $id) {{ {FIELDS} }} }}"
UPLOAD = f"query($id: ID!) {{ upload(id: $id) {{ {FIELDS} }} }}"


def clean_copy(r2: FakeR2, upload_id: str) -> bytes:
    """The private clean copy processing stored (task photos stay private until claimed)."""
    return r2.body(PRIVATE, Upload.objects.get(pk=upload_id).display_key)


def create(
    client: Client, token: str, data: bytes, purpose: str = "TASK_PHOTO", ctype: str = "image/jpeg"
) -> dict[str, Any]:
    variables = {"input": {"purpose": purpose, "contentType": ctype, "sizeBytes": len(data)}}
    return gql(client, CREATE, variables, token=token)


def upload_photo(
    client: Client,
    r2: FakeR2,
    token: str,
    data: bytes,
    purpose: str = "TASK_PHOTO",
    ctype: str = "image/jpeg",
    django_capture_on_commit_callbacks: Any = None,
) -> dict[str, Any]:
    """What the web and mobile helpers do: presign, PUT, complete, then follow the upload
    until processing (run by the commit hook, as the Celery worker would) is done."""
    target = create(client, token, data, purpose, ctype)["data"]["createUploadUrl"]
    assert r2.client_put(target["url"], data, target["contentType"]) == 200
    with django_capture_on_commit_callbacks(execute=True):
        completed = gql(client, COMPLETE, {"id": target["id"]}, token=token)
    assert completed["data"]["completeUpload"]["status"] == "PROCESSING"
    return gql(client, UPLOAD, {"id": target["id"]}, token=token)["data"]["upload"]


@pytest.fixture
def token() -> str:
    return sign_in(make_user()).access_token


def test_signed_out_callers_are_refused(client: Client) -> None:
    data = photo_with_gps()
    assert error_codes(create(client, "", data)) == ["UNAUTHENTICATED"]
    some_id = "00000000-0000-0000-0000-000000000000"
    assert error_codes(gql(client, COMPLETE, {"id": some_id})) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, UPLOAD, {"id": some_id})) == ["UNAUTHENTICATED"]


def test_create_upload_url_presigns_a_put_into_the_private_bucket(
    client: Client, r2: FakeR2
) -> None:
    user = make_user()
    token = sign_in(user).access_token

    target = create(client, token, b"x" * 1234)["data"]["createUploadUrl"]

    upload = Upload.objects.get()
    assert target["id"] == str(upload.pk) == target["upload"]["id"]
    assert target["method"] == "PUT"
    assert target["contentType"] == "image/jpeg"
    assert target["upload"] == {
        "id": str(upload.pk),
        "purpose": "TASK_PHOTO",
        "status": "PENDING",
        "contentType": "image/jpeg",
        "sizeBytes": 1234,
        "url": None,
    }
    assert datetime.fromisoformat(target["expiresAt"]) == upload.url_expires_at
    [signed] = r2.signed.values()
    assert (signed.bucket, signed.content_type, signed.content_length) == (
        PRIVATE,
        "image/jpeg",
        1234,
    )
    assert signed.key == f"users/{user.pk}/task-photos/{upload.pk}/upload"
    assert "X-Amz-Expires=600" in target["url"]


def test_the_signed_put_only_accepts_the_declared_type_and_size(
    client: Client, r2: FakeR2, token: str
) -> None:
    data = photo_with_gps()
    target = create(client, token, data)["data"]["createUploadUrl"]

    assert r2.client_put(target["url"], data + b"more", "image/jpeg") == 403
    assert r2.client_put(target["url"], data, "image/png") == 403
    assert r2.keys_in(PRIVATE) == set()


@pytest.mark.parametrize("ctype", ["image/heic", "application/pdf", "image/svg+xml", ""])
def test_only_jpeg_png_and_webp_are_accepted(client: Client, token: str, ctype: str) -> None:
    assert error_codes(create(client, token, b"x", ctype=ctype)) == ["UPLOAD_TYPE_NOT_ALLOWED"]
    assert not Upload.objects.exists()


@pytest.mark.parametrize("size", [0, -1, 10 * 1024 * 1024 + 1])
def test_empty_or_oversized_files_are_refused(client: Client, token: str, size: int) -> None:
    variables = {"input": {"purpose": "TASK_PHOTO", "contentType": "image/jpeg", "sizeBytes": size}}
    assert error_codes(gql(client, CREATE, variables, token=token)) == ["UPLOAD_TOO_LARGE"]


def test_pending_uploads_per_user_are_capped(client: Client, token: str, settings: Any) -> None:
    settings.UPLOAD_MAX_PENDING_PER_USER = 2
    assert "errors" not in create(client, token, b"x")
    assert "errors" not in create(client, token, b"x")
    assert error_codes(create(client, token, b"x")) == ["TOO_MANY_PENDING_UPLOADS"]
    # Another user has their own allowance.
    other = sign_in(make_user("marko")).access_token
    assert "errors" not in create(client, other, b"x")


def test_create_upload_url_is_rate_limited(client: Client, token: str, settings: Any) -> None:
    assert settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES["createUploadUrl"] == 20
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"createUploadUrl": 2}
    settings.UPLOAD_MAX_PENDING_PER_USER = 10
    assert "errors" not in create(client, token, b"x")
    assert "errors" not in create(client, token, b"x")
    assert error_codes(create(client, token, b"x")) == ["RATE_LIMITED"]


def test_completing_before_the_put_arrived_is_refused_and_can_be_retried(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    data = photo_with_gps()
    target = create(client, token, data)["data"]["createUploadUrl"]

    body = gql(client, COMPLETE, {"id": target["id"]}, token=token)

    assert error_codes(body) == ["UPLOAD_NOT_RECEIVED"]
    assert Upload.objects.get().status == Status.PENDING
    r2.client_put(target["url"], data, "image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        gql(client, COMPLETE, {"id": target["id"]}, token=token)
    done = gql(client, UPLOAD, {"id": target["id"]}, token=token)
    assert done["data"]["upload"]["status"] == "READY"


def test_public_task_photos_have_no_gps_exif(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    original = photo_with_gps(size=(640, 480))
    assert has_gps(original)

    done = upload_photo(
        client,
        r2,
        token,
        original,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    upload = Upload.objects.get()
    assert done["status"] == "READY"
    assert (done["width"], done["height"]) == (640, 480)
    clean = clean_copy(r2, done["id"])
    image = Image.open(io.BytesIO(clean))
    assert image.format == "JPEG"
    assert not has_gps(clean)
    assert dict(image.getexif()) == {}
    assert "exif" not in image.info and "xmp" not in image.info
    assert b"TestPhone" not in clean
    # The upload, GPS and all, is gone: only the clean copy exists, and it stays private
    # until a task claims it. The owner sees it through a short-lived URL.
    base = f"users/{upload.owner.pk}/task-photos/{upload.pk}"
    assert r2.keys_in(PRIVATE) == {f"{base}/display.jpg"}
    assert r2.keys_in(PUBLIC) == set()
    assert done["url"].startswith(f"https://fake-r2.invalid/{PRIVATE}/{base}/display.jpg?")
    assert upload.original_key == "" and upload.public_key == ""
    assert upload.exif_location is None
    assert services.public_photo_url(upload) is None

    # postTask claims it: once the claim commits, the clean copy is published.
    with django_capture_on_commit_callbacks(execute=True):
        services.claim_uploads(upload.owner, [upload.pk], Purpose.TASK_PHOTO)

    upload.refresh_from_db()
    public_url = f"https://photos.test.taskbeavo.invalid/photos/{upload.pk}.jpg"
    assert services.public_photo_url(upload) == public_url
    assert r2.body(PUBLIC, f"photos/{upload.pk}.jpg") == clean
    assert gql(client, UPLOAD, {"id": done["id"]}, token=token)["data"]["upload"]["url"] == (
        public_url
    )


@pytest.mark.parametrize(
    ("fmt", "ctype", "ext"), [("PNG", "image/png", "png"), ("WEBP", "image/webp", "webp")]
)
def test_png_and_webp_task_photos_are_stripped_too(
    client: Client,
    r2: FakeR2,
    token: str,
    django_capture_on_commit_callbacks: Any,
    fmt: str,
    ctype: str,
    ext: str,
) -> None:
    original = photo_with_gps(fmt)
    assert has_gps(original)

    done = upload_photo(
        client,
        r2,
        token,
        original,
        ctype=ctype,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert done["status"] == "READY"
    clean = clean_copy(r2, done["id"])
    assert Upload.objects.get().display_key.endswith(f"/display.{ext}")
    assert Image.open(io.BytesIO(clean)).format == fmt
    assert not has_gps(clean)


def test_evidence_photos_keep_time_and_gps_server_side_only(
    client: Client, r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    user = make_user()
    token = sign_in(user).access_token
    original = photo_with_gps()
    before = timezone.now()

    done = upload_photo(
        client,
        r2,
        token,
        original,
        purpose="EVIDENCE_PHOTO",
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    upload = Upload.objects.get()
    assert done["status"] == "READY"
    # The trusted time is when the server confirmed the file; EXIF time and position are
    # kept server-side as what the file claims.
    assert upload.received_at is not None
    assert before <= upload.received_at <= timezone.now()
    assert upload.exif_taken_at == datetime(2026, 10, 30, 8, 15, tzinfo=UTC)
    assert upload.exif_location is not None
    assert upload.exif_location.y == pytest.approx(42.641, abs=1e-3)
    assert upload.exif_location.x == pytest.approx(18.11, abs=1e-3)
    # The checked original stays in the private bucket, at a key never presigned, with
    # its hash; the presigned upload key is gone; nothing goes public.
    base = f"users/{user.pk}/evidence/{upload.pk}"
    assert r2.keys_in(PRIVATE) == {f"{base}/original.jpg", f"{base}/display.jpg"}
    assert r2.body(PRIVATE, f"{base}/original.jpg") == original
    assert upload.original_sha256 == hashlib.sha256(original).hexdigest()
    assert r2.keys_in(PUBLIC) == set()
    # Viewers get the clean copy through a short-lived URL, never the original.
    assert not has_gps(r2.body(PRIVATE, f"{base}/display.jpg"))
    assert done["url"].startswith(f"https://fake-r2.invalid/{PRIVATE}/{base}/display.jpg?")
    assert r2.signed_gets[-1] == (PRIVATE, f"{base}/display.jpg", 300)
    # And the API has no field that could reveal them.
    upload_type = schema.get_type_by_name("Upload")
    assert upload_type is not None
    names = {field.name for field in upload_type.fields}  # type: ignore[union-attr]
    assert not names & {
        "receivedAt",
        "exifTakenAt",
        "exifLocation",
        "originalKey",
        "displayKey",
        "publicKey",
    }
    assert "captured" not in str(done) and "42.6" not in str(done)


def test_a_file_that_is_not_the_declared_image_is_rejected_and_deleted(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    png = photo_with_gps("PNG")

    done = upload_photo(
        client,
        r2,
        token,
        png,
        ctype="image/jpeg",
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert done["status"] == "REJECTED"
    assert done["rejectionCode"] == "UPLOAD_INVALID_IMAGE"
    assert done["url"] is None
    assert r2.keys_in(PRIVATE) == set() and r2.keys_in(PUBLIC) == set()


def test_garbage_bytes_are_rejected(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    done = upload_photo(
        client,
        r2,
        token,
        b"\xff\xd8\xff not really a jpeg",
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )
    assert (done["status"], done["rejectionCode"]) == ("REJECTED", "UPLOAD_INVALID_IMAGE")


def test_images_with_too_many_pixels_are_rejected_before_decoding(
    client: Client, r2: FakeR2, token: str, settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    settings.UPLOAD_MAX_PIXELS = 1000

    done = upload_photo(
        client,
        r2,
        token,
        photo_with_gps(size=(100, 100)),
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert (done["status"], done["rejectionCode"]) == ("REJECTED", "UPLOAD_TOO_LARGE")


def test_large_photos_are_scaled_down(
    client: Client, r2: FakeR2, token: str, settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    settings.UPLOAD_MAX_DIMENSION_PX = 100

    done = upload_photo(
        client,
        r2,
        token,
        photo_with_gps(size=(400, 200)),
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert (done["width"], done["height"]) == (100, 50)


def test_other_users_cannot_see_or_complete_an_upload(
    client: Client, r2: FakeR2, token: str
) -> None:
    data = photo_with_gps()
    target = create(client, token, data)["data"]["createUploadUrl"]
    r2.client_put(target["url"], data, "image/jpeg")
    other = sign_in(make_user("marko")).access_token

    assert error_codes(gql(client, UPLOAD, {"id": target["id"]}, token=other)) == [
        "UPLOAD_NOT_FOUND"
    ]
    assert error_codes(gql(client, COMPLETE, {"id": target["id"]}, token=other)) == [
        "UPLOAD_NOT_FOUND"
    ]
    assert error_codes(gql(client, UPLOAD, {"id": "not-a-uuid"}, token=other)) == [
        "UPLOAD_NOT_FOUND"
    ]
    assert Upload.objects.get().status == Status.PENDING


def test_the_owner_follows_an_upload_to_ready(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    done = upload_photo(
        client,
        r2,
        token,
        photo_with_gps(),
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    found = gql(client, UPLOAD, {"id": done["id"]}, token=token)["data"]["upload"]

    assert found["status"] == "READY"
    assert found["rejectionCode"] is None
    # A second completeUpload changes nothing.
    again = gql(client, COMPLETE, {"id": done["id"]}, token=token)["data"]["completeUpload"]
    assert again["status"] == "READY"


def test_no_upload_key_or_file_detail_reaches_a_span(
    client: Client, r2: FakeR2, token: str, spans: Any, django_capture_on_commit_callbacks: Any
) -> None:
    upload_photo(
        client,
        r2,
        token,
        photo_with_gps(),
        purpose="EVIDENCE_PHOTO",
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    upload = Upload.objects.get()
    [span] = [s for s in spans.get_finished_spans() if s.name == "uploads.process"]
    assert dict(span.attributes or {}) == {
        "upload.id": str(upload.pk),
        "upload.purpose": "EVIDENCE_PHOTO",
        "upload.status": "READY",
    }
    exported = str([(s.name, dict(s.attributes or {})) for s in spans.get_finished_spans()])
    assert "users/" not in exported and "photos/" not in exported
    assert settings.R2_PRIVATE_BUCKET not in exported


def test_exif_orientation_is_applied_before_stripping(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    image = Image.new("RGB", (60, 20))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90° clockwise
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)

    done = upload_photo(
        client,
        r2,
        token,
        out.getvalue(),
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert (done["width"], done["height"]) == (20, 60)
    clean = Image.open(io.BytesIO(clean_copy(r2, done["id"])))
    assert clean.getexif().get(0x0112) is None
    assert clean.getexif().get_ifd(IFD.GPSInfo) == {}


def test_a_put_after_processing_cannot_replace_the_evidence(
    client: Client, r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    """The presigned PUT stays valid until it expires; nothing PUT later is ever used."""
    user = make_user()
    token = sign_in(user).access_token
    original = photo_with_gps()
    target = create(client, token, original, "EVIDENCE_PHOTO")["data"]["createUploadUrl"]
    r2.client_put(target["url"], original, "image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        gql(client, COMPLETE, {"id": target["id"]}, token=token)

    forged = bytearray(original)
    forged[-3] ^= 0xFF
    assert r2.client_put(target["url"], bytes(forged), "image/jpeg") == 200
    with django_capture_on_commit_callbacks(execute=True):
        again = gql(client, COMPLETE, {"id": target["id"]}, token=token)

    upload = Upload.objects.get()
    assert again["data"]["completeUpload"]["status"] == "READY"
    base = f"users/{user.pk}/evidence/{upload.pk}"
    assert r2.body(PRIVATE, f"{base}/original.jpg") == original
    assert upload.original_sha256 == hashlib.sha256(original).hexdigest()
    # The forged object sits at the upload key only until the delayed delete runs, and
    # deleting the row removes it too.
    assert (PRIVATE, f"{base}/upload") in upload.stored_objects()


@pytest.mark.parametrize("purpose", ["EVIDENCE_PHOTO", "TASK_PHOTO"])
def test_a_put_after_complete_upload_is_rejected_not_processed(
    client: Client, r2: FakeR2, django_capture_on_commit_callbacks: Any, purpose: str
) -> None:
    """Processing reads only the object completeUpload confirmed (its ETag): a file PUT
    again before processing reads it is rejected and deleted, never kept."""
    user = make_user()
    token = sign_in(user).access_token
    original = photo_with_gps()
    target = create(client, token, original, purpose)["data"]["createUploadUrl"]
    r2.client_put(target["url"], original, "image/jpeg")
    gql(client, COMPLETE, {"id": target["id"]}, token=token)  # processing not run yet
    upload = Upload.objects.get()
    assert upload.received_etag == r2.head_object(Bucket=PRIVATE, Key=upload.upload_key)["ETag"]

    swapped = photo_with_gps(size=(64, 49))[: len(original)].ljust(len(original), b"\0")
    assert swapped != original
    assert r2.client_put(target["url"], swapped, "image/jpeg") == 200
    assert services.process(upload.pk) == Status.REJECTED

    done = gql(client, UPLOAD, {"id": target["id"]}, token=token)["data"]["upload"]
    assert (done["status"], done["rejectionCode"]) == ("REJECTED", "UPLOAD_CHANGED")
    assert done["url"] is None
    assert r2.keys_in(PRIVATE) == set() and r2.keys_in(PUBLIC) == set()
    upload.refresh_from_db()
    assert upload.original_sha256 == "" and upload.display_key == ""


def test_r2_refusing_if_match_is_not_the_only_guard(r2: FakeR2) -> None:
    """If R2 ignored If-Match, the ETag of what came back is still compared."""
    r2.put_object(Bucket=PRIVATE, Key="k", Body=b"new", ContentType="image/jpeg")
    with (
        mock.patch.object(
            r2, "get_object", return_value={"Body": io.BytesIO(b"new"), "ETag": '"other"'}
        ),
        pytest.raises(storage.ObjectChangedError),
    ):
        storage.read(PRIVATE, "k", 10, etag='"seen"')


def test_processing_schedules_a_second_delete_of_the_upload_key_after_the_url_expires(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    with mock.patch("uploads.tasks.delete_objects.apply_async") as later:
        upload_photo(
            client,
            r2,
            token,
            photo_with_gps(),
            django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
        )

    upload = Upload.objects.get()
    later.assert_called_once()
    (keys,), options = later.call_args.args[0], later.call_args.kwargs
    assert keys == [[PRIVATE, upload.upload_key]]
    assert options["eta"] == upload.url_expires_at + timedelta(seconds=60)


def test_an_image_library_crash_rejects_the_upload(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    with mock.patch("uploads.images.clean_copy", side_effect=RuntimeError("decoder bug")):
        done = upload_photo(
            client,
            r2,
            token,
            photo_with_gps(),
            django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
        )

    assert (done["status"], done["rejectionCode"]) == ("REJECTED", "UPLOAD_INVALID_IMAGE")
    assert r2.keys_in(PRIVATE) == set()


def test_evidence_photos_are_refused_from_web_sessions(client: Client) -> None:
    web = sign_in(make_user(), platform=Session.Platform.WEB).access_token
    app = sign_in(make_user("marko"), platform=Session.Platform.ANDROID).access_token

    assert error_codes(create(client, web, b"x", "EVIDENCE_PHOTO")) == ["EVIDENCE_NEEDS_APP"]
    assert "errors" not in create(client, web, b"x", "TASK_PHOTO")
    assert "errors" not in create(client, app, b"x", "EVIDENCE_PHOTO")
    assert Upload.objects.filter(purpose="EVIDENCE_PHOTO").count() == 1


def test_phone_jpegs_with_a_multi_picture_segment_are_accepted(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    """HDR and depth photos from recent phones are JPEGs that Pillow opens as MPO."""
    first, second = Image.new("RGB", (80, 60), (200, 0, 0)), Image.new("RGB", (40, 30))
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second], exif=photo_exif())
    mpo = out.getvalue()
    assert Image.open(io.BytesIO(mpo)).format == "MPO" and mpo[:2] == b"\xff\xd8"

    done = upload_photo(
        client,
        r2,
        token,
        mpo,
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )

    assert done["status"] == "READY"
    assert (done["width"], done["height"]) == (80, 60)
    clean = clean_copy(r2, done["id"])
    assert Image.open(io.BytesIO(clean)).format == "JPEG"
    assert not has_gps(clean)


def test_a_slow_photo_is_retried_not_rejected(
    client: Client, r2: FakeR2, token: str, django_capture_on_commit_callbacks: Any
) -> None:
    data = photo_with_gps()
    target = create(client, token, data)["data"]["createUploadUrl"]
    r2.client_put(target["url"], data, "image/jpeg")
    gql(client, COMPLETE, {"id": target["id"]}, token=token)  # commit hook not run

    with (
        mock.patch("uploads.images.clean_copy", side_effect=SoftTimeLimitExceeded()),
        pytest.raises(SoftTimeLimitExceeded),
    ):
        services.process(Upload.objects.get().pk)

    assert Upload.objects.get().status == Status.PROCESSING
    assert services.process(Upload.objects.get().pk) == Status.READY


DISCARD = "mutation($id: ID!) { discardUpload(id: $id) }"


def test_discard_upload_deletes_an_unused_upload_of_the_caller_only(
    client: Client, r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana_token = sign_in(make_user()).access_token
    done = upload_photo(
        client,
        r2,
        ana_token,
        photo_with_gps(),
        django_capture_on_commit_callbacks=django_capture_on_commit_callbacks,
    )
    marko_token = sign_in(make_user("marko")).access_token

    assert error_codes(gql(client, DISCARD, {"id": done["id"]}, token=marko_token)) == [
        "UPLOAD_NOT_FOUND"
    ]
    assert error_codes(gql(client, DISCARD, {"id": done["id"]})) == ["UNAUTHENTICATED"]
    with django_capture_on_commit_callbacks(execute=True):
        discarded = gql(client, DISCARD, {"id": done["id"]}, token=ana_token)

    assert discarded["data"]["discardUpload"] == done["id"]
    assert not Upload.objects.exists()
    assert r2.objects == {}
