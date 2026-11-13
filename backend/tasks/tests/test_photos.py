"""Task photos are public only while the task is OPEN; a draft's photos expire."""

import datetime as dt
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from care_contacts.tests.factories import make_contact
from tasks import services
from tasks.models import Task
from tasks.tests.factories import changes, make_poster, ready_photo
from uploads.models import Upload
from uploads.tests.fakes import FakeR2

pytestmark = pytest.mark.django_db

PRIVATE = "taskbeavo-private-test"
PUBLIC = "taskbeavo-public-test"
HELD_TEXT = "Zovi 091\n234\n5678"


def stored_photo(r2: FakeR2, owner: Any) -> Upload:
    upload = ready_photo(owner)
    r2.put_object(Bucket=PRIVATE, Key=upload.display_key, Body=b"jpeg", ContentType="image/jpeg")
    return upload


def test_a_drafts_photos_stay_private_and_expire_unless_published(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_poster()
    photo = stored_photo(r2, ana)

    with django_capture_on_commit_callbacks(execute=True):
        draft = services.save_draft(ana, changes(photo_ids=[photo.pk])).task

    photo.refresh_from_db()
    assert photo.attached_at is not None and photo.publish_held
    assert r2.keys_in(PUBLIC) == set()
    first_expiry = photo.delete_after
    assert first_expiry is not None
    expected = timezone.now() + dt.timedelta(days=30)
    assert abs(first_expiry - expected) < dt.timedelta(minutes=1)

    # Saving again pushes the date out; publishing clears it and publishes the photo.
    Upload.objects.filter(pk=photo.pk).update(delete_after=timezone.now())
    services.save_draft(ana, services.TaskChanges(title="Lawn"), str(draft.pk))
    photo.refresh_from_db()
    assert photo.delete_after is not None and photo.delete_after > timezone.now()

    with django_capture_on_commit_callbacks(execute=True):
        services.publish_draft(ana, str(draft.pk))
    photo.refresh_from_db()
    assert photo.delete_after is None
    assert photo.public_key and r2.keys_in(PUBLIC) == {photo.public_key}


def test_a_posted_open_tasks_photos_are_published(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_poster()
    photo = stored_photo(r2, ana)
    with django_capture_on_commit_callbacks(execute=True):
        services.post_task(ana, changes(photo_ids=[photo.pk]))
    photo.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {photo.public_key}


def test_a_held_tasks_photos_stay_private(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_poster()
    photo = stored_photo(r2, ana)
    with django_capture_on_commit_callbacks(execute=True):
        task = services.post_task(ana, changes(description=HELD_TEXT, photo_ids=[photo.pk])).task
    assert task.status == Task.Status.PENDING_REVIEW
    photo.refresh_from_db()
    assert photo.publish_held and not photo.public_key
    assert photo.delete_after is None
    assert r2.keys_in(PUBLIC) == set()


def test_holding_an_open_task_unpublishes_and_releasing_republishes(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_poster()
    photo = stored_photo(r2, ana)
    with django_capture_on_commit_callbacks(execute=True):
        task_id = str(services.post_task(ana, changes(photo_ids=[photo.pk])).task.pk)
    assert r2.keys_in(PUBLIC)

    with django_capture_on_commit_callbacks(execute=True):
        held = services.update_task(ana, task_id, services.TaskChanges(description=HELD_TEXT))
    assert held.task.status == Task.Status.PENDING_REVIEW
    assert r2.keys_in(PUBLIC) == set()

    with django_capture_on_commit_callbacks(execute=True):
        released = services.update_task(
            ana, task_id, services.TaskChanges(description="Mow the lawn, please.")
        )
    assert released.task.status == Task.Status.OPEN
    photo.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {photo.public_key}


def test_photos_added_to_an_open_task_are_published(
    r2: FakeR2, django_capture_on_commit_callbacks: Any
) -> None:
    ana = make_poster()
    task_id = str(services.post_task(ana, changes()).task.pk)
    photo = stored_photo(r2, ana)
    with django_capture_on_commit_callbacks(execute=True):
        services.update_task(ana, task_id, services.TaskChanges(photo_ids=[photo.pk]))
    photo.refresh_from_db()
    assert r2.keys_in(PUBLIC) == {photo.public_key}


def test_task_writes_lock_the_contact_before_the_task() -> None:
    """The order delete_contact uses, so the two can't deadlock."""
    ana = make_poster()
    contact = make_contact(ana)
    task_id = str(services.post_task(ana, changes()).task.pk)

    with CaptureQueriesContext(connection) as queries:
        services.update_task(ana, task_id, services.TaskChanges(care_contact_id=str(contact.pk)))

    locks = [q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]]
    assert '"care_contacts"' in locks[0].split("WHERE")[0]
    assert any('FROM "tasks"' in sql for sql in locks[1:])
