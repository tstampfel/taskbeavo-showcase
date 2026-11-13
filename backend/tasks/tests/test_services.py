"""Posting, drafting and editing tasks: every rule in tasks/services.py."""

import datetime as dt
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone

from accounts.tests.factories import make_user
from areas.models import ServiceArea
from care_contacts import services as care_contacts
from care_contacts.models import CareContact
from care_contacts.tests.factories import make_contact
from core.errors import ApiError
from tasks import services
from tasks.models import Task
from tasks.tests.factories import (
    OLD_TOWN,
    OSIJEK,
    VIENNA,
    changes,
    make_poster,
    ready_photo,
    window,
)
from uploads.models import Upload

pytestmark = pytest.mark.django_db


def code_of(call: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ApiError) as raised:
        call(*args, **kwargs)
    return str(raised.value.extensions["code"])


# Posting


def test_posting_publishes_an_open_task_in_dubrovnik() -> None:
    ana = make_poster()

    result = services.post_task(ana, changes())

    task = Task.objects.get()
    assert result.task == task
    assert result.warnings == []
    assert task.status == Task.Status.OPEN
    assert task.poster == ana
    assert task.service_area is not None and task.service_area.slug == "dubrovnik"
    assert task.published_at is not None
    assert task.moderation_verdict == "CLEAN"
    assert task.hold_reasons == []


def test_the_approximate_location_is_about_a_kilometre_off_and_stable() -> None:
    task = services.post_task(make_poster(), changes()).task
    assert task.location is not None and task.approximate_location is not None
    assert task.approximate_location != task.location
    assert abs(task.approximate_location.x - task.location.x) <= 0.005
    assert abs(task.approximate_location.y - task.location.y) <= 0.005
    assert services.approximate(task.location) == task.approximate_location


def test_access_details_are_encrypted_at_rest() -> None:
    task = services.post_task(make_poster(), changes()).task
    with connection.cursor() as cursor:
        cursor.execute("SELECT access_details FROM tasks WHERE id = %s", [task.pk])
        (stored,) = cursor.fetchone()
    assert "1234" not in stored
    assert Task.objects.get().access_details == "Gate code 1234, key under the mat"


def test_posting_needs_a_verified_phone() -> None:
    unverified = make_user("ivo")
    assert code_of(services.post_task, unverified, changes()) == "PHONE_NOT_VERIFIED"
    assert not Task.objects.exists()


@pytest.mark.parametrize("lat_lng", [OSIJEK, VIENNA])
def test_posting_outside_every_service_area_is_refused(lat_lng: tuple[float, float]) -> None:
    location = services.Location("Somewhere", *lat_lng)
    assert code_of(services.post_task, make_poster(), changes(location=location)) == (
        "OUT_OF_SERVICE_AREA"
    )
    assert not Task.objects.exists()


def test_an_inactive_area_doesnt_count() -> None:
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)
    assert code_of(services.post_task, make_poster(), changes()) == "OUT_OF_SERVICE_AREA"


@pytest.mark.parametrize("category", ["RIDES", "MEDICAL_CARE", "PERSONAL_CARE"])
def test_excluded_categories_are_refused(category: str) -> None:
    assert code_of(services.post_task, make_poster(), changes(category=category)) == (
        "CATEGORY_NOT_ALLOWED"
    )
    assert not Task.objects.exists()


def test_the_minimum_budget_is_ten_euros() -> None:
    poster = make_poster()
    assert code_of(services.post_task, poster, changes(budget_cents=999)) == (
        "BUDGET_BELOW_MINIMUM"
    )
    assert services.post_task(poster, changes(budget_cents=1_000)).task.budget_cents == 1_000


@pytest.mark.parametrize(
    ("start_days", "end_days"),
    [(-1, 1), (2, 1), (59, 61), (61, 62)],
    ids=["starts-in-the-past", "ends-before-it-starts", "ends-after-60-days", "too-far"],
)
def test_the_window_runs_from_now_to_60_days(start_days: int, end_days: int) -> None:
    now = timezone.now()
    fields = {
        "window_start": now + dt.timedelta(days=start_days),
        "window_end": now + dt.timedelta(days=end_days),
    }
    assert code_of(services.post_task, make_poster(), changes(**fields)) == "INVALID_TASK_DATES"


def test_a_window_ending_on_day_60_is_allowed() -> None:
    now = timezone.now()
    end = now + dt.timedelta(days=60) - dt.timedelta(minutes=1)
    task = services.post_task(
        make_poster(), changes(window_start=end - dt.timedelta(hours=2), window_end=end)
    ).task
    assert task.window_end == end


def test_a_flexible_task_needs_no_window_but_a_fixed_one_does() -> None:
    poster = make_poster()
    task = services.post_task(
        poster, changes(flexible_date=True, window_start=None, window_end=None)
    ).task
    assert task.flexible_date and task.window_start is None
    assert code_of(services.post_task, poster, changes(window_start=None, window_end=None)) == (
        "INVALID_TASK_DATES"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "   "},
        {"title": "x" * 101},
        {"description": "x" * 2_001},
        {"access_details": "x" * 501},
        {"category": None},
        {"budget_cents": None},
        {"location": services.Location("Gruž", 95.0, 18.0)},
    ],
)
def test_missing_or_invalid_details_are_refused(fields: dict[str, Any]) -> None:
    assert code_of(services.post_task, make_poster(), changes(**fields)) == ("INVALID_TASK_DETAILS")


# On-site contact


def test_the_poster_can_name_their_own_contact() -> None:
    ana = make_poster()
    contact = make_contact(ana)
    task = services.post_task(ana, changes(care_contact_id=str(contact.pk))).task
    assert task.care_contact == contact


@pytest.mark.parametrize("contact_id", ["other", "999999", "not-a-number"])
def test_a_contact_the_poster_doesnt_own_is_not_found(contact_id: str) -> None:
    ana = make_poster()
    if contact_id == "other":
        contact_id = str(make_contact(make_user("ivo")).pk)
    assert code_of(services.post_task, ana, changes(care_contact_id=contact_id)) == (
        "CARE_CONTACT_NOT_FOUND"
    )
    assert not Task.objects.exists()


def test_a_contact_named_by_an_open_task_cant_be_deleted() -> None:
    ana = make_poster()
    contact = make_contact(ana)
    services.post_task(ana, changes(care_contact_id=str(contact.pk)))

    assert code_of(care_contacts.delete_contact, ana, str(contact.pk)) == "CARE_CONTACT_IN_USE"
    assert CareContact.objects.filter(pk=contact.pk).exists()


def test_a_contact_named_by_a_draft_cant_be_deleted() -> None:
    ana = make_poster()
    contact = make_contact(ana)
    services.save_draft(ana, services.TaskChanges(care_contact_id=str(contact.pk)))
    assert code_of(care_contacts.delete_contact, ana, str(contact.pk)) == "CARE_CONTACT_IN_USE"


def test_deleting_a_contact_releases_it_from_closed_tasks() -> None:
    ana = make_poster()
    contact = make_contact(ana)
    task = services.post_task(ana, changes(care_contact_id=str(contact.pk))).task
    services.close_task(task)

    deleted = care_contacts.delete_contact(ana, str(contact.pk))

    assert deleted.pk == contact.pk
    assert not CareContact.objects.exists()
    task.refresh_from_db()
    assert task.care_contact is None
    assert task.status == Task.Status.CLOSED


def test_deleting_the_account_removes_tasks_and_their_contacts() -> None:
    ana = make_poster()
    contact = make_contact(ana)
    services.post_task(ana, changes(care_contact_id=str(contact.pk)))
    services.save_draft(ana, services.TaskChanges(care_contact_id=str(contact.pk)))

    ana.delete()

    assert not Task.objects.exists()
    assert not CareContact.objects.exists()


# Content screening


def test_clear_contact_details_are_masked_and_published() -> None:
    task = services.post_task(
        make_poster(), changes(description="Call me on 091 234 5678 for the gate.")
    ).task
    assert task.status == Task.Status.OPEN
    assert "091" not in task.description and "•••" in task.description
    assert task.moderation_verdict == "MASKED"
    assert task.moderation_kinds == ["PHONE"]


def test_contact_details_in_the_title_are_masked_too() -> None:
    task = services.post_task(make_poster(), changes(title="Lawn, ana@example.com")).task
    assert "ana@example.com" not in task.title
    assert task.moderation_verdict == "MASKED"


def test_doubtful_contact_details_hold_the_task_for_review() -> None:
    task = services.post_task(make_poster(), changes(description="Zovi 091\n234\n5678")).task
    assert task.status == Task.Status.PENDING_REVIEW
    assert task.hold_reasons == ["CONTACT_DETAILS"]
    assert task.published_at is not None


def test_a_bare_messenger_name_is_published() -> None:
    task = services.post_task(make_poster(), changes(description="Imam Viber, javi se")).task
    assert task.status == Task.Status.OPEN
    assert task.moderation_verdict == "FLAGGED"
    assert task.moderation_kinds == ["MESSENGER"]


def test_an_excluded_request_in_the_text_holds_the_task() -> None:
    task = services.post_task(
        make_poster(),
        changes(category="CHECK_IN", description="Visit and drive my mother to the doctor."),
    ).task
    assert task.status == Task.Status.PENDING_REVIEW
    assert task.hold_reasons == ["EXCLUDED_CATEGORY"]


def test_the_screening_never_logs_the_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    services.post_task(make_poster(), changes(description="Imam Viber, javi se. Marko"))
    assert caplog.records
    assert "Viber" not in caplog.text and "Marko" not in caplog.text


# Duplicates


def test_the_same_place_on_the_same_day_is_a_duplicate_warning() -> None:
    ana = make_poster()
    first = services.post_task(ana, changes())
    second = services.post_task(ana, changes(title="Trim the hedge"))

    assert first.warnings == []
    assert second.warnings == [services.TaskWarning.DUPLICATE_TASK]
    assert Task.objects.filter(status=Task.Status.OPEN).count() == 2


def test_another_day_another_place_or_another_poster_is_no_duplicate() -> None:
    ana = make_poster()
    services.post_task(ana, changes())
    start, end = window(days=5)
    assert services.post_task(ana, changes(window_start=start, window_end=end)).warnings == []
    old_town = services.Location("Stradun 1", *OLD_TOWN)
    assert services.post_task(ana, changes(location=old_town)).warnings == []
    assert services.post_task(make_poster("ivo"), changes()).warnings == []


# Drafts


def test_a_draft_saves_partial_fields_and_is_published_later() -> None:
    ana = make_poster()
    draft = services.save_draft(ana, services.TaskChanges(title="Lawn")).task
    assert draft.status == Task.Status.DRAFT and draft.published_at is None

    assert code_of(services.publish_draft, ana, str(draft.pk)) == "INVALID_TASK_DETAILS"

    full = changes()
    services.save_draft(ana, full, str(draft.pk))
    published = services.publish_draft(ana, str(draft.pk)).task
    assert published.status == Task.Status.OPEN
    assert published.title == "Mow the lawn"


def test_a_drafts_address_is_checked_when_saved() -> None:
    location = services.Location("Osijek", *OSIJEK)
    assert code_of(
        services.save_draft, make_user("ivo"), services.TaskChanges(location=location)
    ) == ("OUT_OF_SERVICE_AREA")
    assert not Task.objects.exists()


def test_publishing_rechecks_the_area_and_the_phone() -> None:
    ana = make_poster()
    draft = services.save_draft(ana, changes()).task
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)
    assert code_of(services.publish_draft, ana, str(draft.pk)) == "OUT_OF_SERVICE_AREA"

    unverified = make_user("ivo")
    other_draft = services.save_draft(unverified, services.TaskChanges(title="x")).task
    assert code_of(services.publish_draft, unverified, str(other_draft.pk)) == (
        "PHONE_NOT_VERIFIED"
    )


def test_drafts_are_only_text_screened_on_publishing() -> None:
    ana = make_poster()
    draft = services.save_draft(ana, changes(description="Zovi 091 234 5678")).task
    assert draft.description == "Zovi 091 234 5678"
    published = services.publish_draft(ana, str(draft.pk)).task
    assert "091" not in published.description


def test_someone_elses_draft_is_not_found() -> None:
    draft = services.save_draft(make_poster(), services.TaskChanges(title="x")).task
    ivo = make_poster("ivo")
    assert code_of(services.publish_draft, ivo, str(draft.pk)) == "TASK_NOT_FOUND"
    assert code_of(services.save_draft, ivo, services.TaskChanges(), str(draft.pk)) == (
        "TASK_NOT_FOUND"
    )


def test_a_published_task_isnt_saved_as_a_draft_or_published_twice() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    assert code_of(services.save_draft, ana, services.TaskChanges(), str(task.pk)) == (
        "TASK_NOT_EDITABLE"
    )
    assert code_of(services.publish_draft, ana, str(task.pk)) == "TASK_NOT_EDITABLE"


# Edits


def test_moving_a_task_out_of_the_area_is_refused_and_changes_nothing() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    before = (task.address, task.location, task.approximate_location)

    for lat_lng in (OSIJEK, VIENNA):
        moved = services.TaskChanges(location=services.Location("Elsewhere", *lat_lng))
        assert code_of(services.update_task, ana, str(task.pk), moved) == "OUT_OF_SERVICE_AREA"

    task.refresh_from_db()
    assert (task.address, task.location, task.approximate_location) == before


def test_moving_a_task_within_the_area_is_allowed() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    moved = services.TaskChanges(location=services.Location("Stradun 1", *OLD_TOWN))
    updated = services.update_task(ana, str(task.pk), moved).task
    assert updated.address == "Stradun 1"
    assert updated.location is not None and updated.location.y == OLD_TOWN[0]


def test_edits_apply_the_same_rules() -> None:
    ana = make_poster()
    task_id = str(services.post_task(ana, changes()).task.pk)
    assert code_of(services.update_task, ana, task_id, services.TaskChanges(budget_cents=500)) == (
        "BUDGET_BELOW_MINIMUM"
    )
    assert code_of(services.update_task, ana, task_id, services.TaskChanges(category="RIDES")) == (
        "CATEGORY_NOT_ALLOWED"
    )
    other_contact = str(make_contact(make_user("ivo")).pk)
    assert code_of(
        services.update_task, ana, task_id, services.TaskChanges(care_contact_id=other_contact)
    ) == ("CARE_CONTACT_NOT_FOUND")


def test_changed_text_is_screened_again_and_can_release_a_hold() -> None:
    ana = make_poster()
    task_id = str(services.post_task(ana, changes()).task.pk)

    held = services.update_task(
        ana, task_id, services.TaskChanges(description="Zovi 091\n234\n5678")
    ).task
    assert held.status == Task.Status.PENDING_REVIEW

    released = services.update_task(
        ana, task_id, services.TaskChanges(description="Mow the lawn, please.")
    ).task
    assert released.status == Task.Status.OPEN
    assert released.hold_reasons == []


def test_only_the_poster_edits_and_only_open_tasks() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    assert code_of(
        services.update_task, make_poster("ivo"), str(task.pk), services.TaskChanges(title="x")
    ) == ("TASK_NOT_FOUND")
    services.close_task(task)
    assert code_of(services.update_task, ana, str(task.pk), services.TaskChanges(title="x")) == (
        "TASK_NOT_EDITABLE"
    )


def test_editing_needs_a_verified_phone() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    ana.phone_verified_at = None
    assert code_of(services.update_task, ana, str(task.pk), services.TaskChanges(title="x")) == (
        "PHONE_NOT_VERIFIED"
    )


# Photos


def test_photos_are_claimed_in_order_when_the_task_is_saved() -> None:
    ana = make_poster()
    first, second = ready_photo(ana), ready_photo(ana)

    task = services.post_task(ana, changes(photo_ids=[second.pk, first.pk])).task

    assert [photo.upload_id for photo in task.task_photos.all()] == [second.pk, first.pk]
    assert all(upload.attached_at for upload in Upload.objects.all())


def test_someone_elses_photo_refuses_the_whole_task() -> None:
    ana = make_poster()
    foreign = ready_photo(make_user("ivo"))
    assert code_of(services.post_task, ana, changes(photo_ids=[foreign.pk])) == ("UPLOAD_NOT_FOUND")
    assert not Task.objects.exists()


def test_removed_photos_and_a_closed_tasks_photos_start_their_retention() -> None:
    ana = make_poster()
    kept, removed = ready_photo(ana), ready_photo(ana)
    task = services.post_task(ana, changes(photo_ids=[kept.pk, removed.pk])).task

    services.update_task(ana, str(task.pk), services.TaskChanges(photo_ids=[kept.pk]))
    removed.refresh_from_db()
    kept.refresh_from_db()
    assert removed.delete_after is not None
    assert kept.delete_after is None
    assert [photo.upload_id for photo in task.task_photos.all()] == [kept.pk]

    closed_at = timezone.now()
    services.close_task(task, closed_at=closed_at)
    kept.refresh_from_db()
    assert kept.delete_after == closed_at + dt.timedelta(days=30)


def test_at_most_ten_photos() -> None:
    ana = make_poster()
    photos = [ready_photo(ana).pk for _ in range(11)]
    assert code_of(services.post_task, ana, changes(photo_ids=photos)) == "INVALID_TASK_DETAILS"


# Who sees a task


def test_others_see_open_tasks_only() -> None:
    ana = make_poster()
    draft = services.save_draft(ana, services.TaskChanges(title="x")).task
    held = services.post_task(ana, changes(description="Zovi 091\n234\n5678")).task
    open_task = services.post_task(ana, changes(title="Trim")).task
    ivo = make_poster("ivo")

    assert services.visible_task(ivo, str(open_task.pk)) == open_task
    assert services.visible_task(None, str(open_task.pk)) == open_task
    for hidden in (draft, held):
        assert code_of(services.visible_task, ivo, str(hidden.pk)) == "TASK_NOT_FOUND"
        assert services.visible_task(ana, str(hidden.pk)) == hidden
    assert code_of(services.visible_task, ivo, "not-a-uuid") == "TASK_NOT_FOUND"


# Windows


def test_a_draft_whose_window_has_passed_cant_be_published() -> None:
    ana = make_poster()
    draft = services.save_draft(ana, changes()).task
    past = timezone.now() - dt.timedelta(days=2)
    Task.objects.filter(pk=draft.pk).update(
        window_start=past, window_end=past + dt.timedelta(hours=2)
    )
    assert code_of(services.publish_draft, ana, str(draft.pk)) == "INVALID_TASK_DATES"
    draft.refresh_from_db()
    assert draft.status == Task.Status.DRAFT


def test_a_draft_may_hold_half_a_window_but_publishing_needs_all_of_it() -> None:
    ana = make_poster()
    start, _end = window()
    fields = {"window_start": start, "window_end": None}
    draft = services.save_draft(ana, changes(**fields)).task
    assert draft.window_start == start and draft.window_end is None
    assert code_of(services.publish_draft, ana, str(draft.pk)) == "INVALID_TASK_DATES"

    far = timezone.now() + dt.timedelta(days=61)
    assert code_of(
        services.save_draft, ana, services.TaskChanges(window_end=far), str(draft.pk)
    ) == ("INVALID_TASK_DATES")


def test_a_flexible_task_with_a_half_window_cant_be_published() -> None:
    ana = make_poster()
    start, _end = window()
    draft = services.save_draft(ana, changes(window_end=None)).task
    services.save_draft(
        ana, services.TaskChanges(flexible_date=True, window_start=start), str(draft.pk)
    )
    assert code_of(services.publish_draft, ana, str(draft.pk)) == "INVALID_TASK_DATES"


def test_making_a_task_flexible_clears_its_window() -> None:
    ana = make_poster()
    task = services.post_task(ana, changes()).task
    updated = services.update_task(ana, str(task.pk), services.TaskChanges(flexible_date=True))
    assert updated.task.window_start is None and updated.task.window_end is None
    # A second task at the same place is a duplicate only of another flexible one.
    assert services.post_task(ana, changes()).warnings == []
