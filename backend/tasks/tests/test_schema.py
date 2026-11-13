"""Tasks over HTTP at /graphql: permissions, redaction of the exact place (rule e), the
service-area rule (rule j) and the rate limit (rule d)."""

import datetime as dt
from typing import Any

import pytest
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from care_contacts.tests.factories import make_contact
from tasks.models import Task
from tasks.tests.factories import GRUZ, OSIJEK, VIENNA, make_poster, ready_photo

pytestmark = pytest.mark.django_db

PUBLIC = """
id status category title description approximateLat approximateLng serviceAreaName
windowStart windowEnd flexibleDate budgetCents photos { id url } isMine createdAt publishedAt
"""
PRIVATE = "address lat lng accessDetails careContact { id name } textMasked holdReasons"
FIELDS = PUBLIC + PRIVATE
POST = f"""
mutation($input: TaskInput!) {{ postTask(input: $input) {{ id task {{ {FIELDS} }} warnings }} }}
"""
SAVE = f"""
mutation($id: ID, $input: TaskInput!) {{
  saveTaskDraft(id: $id, input: $input) {{ id task {{ {FIELDS} }} warnings }}
}}
"""
PUBLISH = (
    f"mutation($id: ID!) {{ publishTaskDraft(id: $id) {{ id task {{ {FIELDS} }} warnings }} }}"
)
UPDATE = f"""
mutation($id: ID!, $input: TaskInput!) {{
  updateTask(id: $id, input: $input) {{ id task {{ {FIELDS} }} warnings }}
}}
"""
TASK = f"query($id: ID!) {{ task(id: $id) {{ {FIELDS} }} }}"
MINE = f"{{ myTasks {{ {FIELDS} }} }}"


def task_input(**fields: Any) -> dict[str, Any]:
    start = (timezone.now() + dt.timedelta(days=3)).replace(microsecond=0)
    return {
        "category": "GARDEN",
        "title": "Mow the lawn",
        "description": "Mow the lawn and trim the hedge.",
        "location": {"address": "Ulica Ante Starčevića 1", "lat": GRUZ[0], "lng": GRUZ[1]},
        "accessDetails": "Gate code 1234",
        "windowStart": start.isoformat(),
        "windowEnd": (start + dt.timedelta(hours=2)).isoformat(),
        "budgetCents": 3500,
        **fields,
    }


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def post(client: Client, token: str, **fields: Any) -> dict[str, Any]:
    return gql(client, POST, {"input": task_input(**fields)}, token=token)


def test_posting_returns_the_full_task(client: Client) -> None:
    ana = make_poster()
    contact = make_contact(ana)

    body = post(client, sign_in(ana).access_token, careContactId=str(contact.pk))

    payload = body["data"]["postTask"]
    task = payload["task"]
    saved = Task.objects.get()
    assert task["id"] == str(saved.pk)
    assert task["status"] == "OPEN"
    assert task["category"] == "GARDEN"
    assert task["serviceAreaName"] == "Dubrovnik"
    assert task["budgetCents"] == 3500
    assert task["isMine"] is True
    assert task["address"] == "Ulica Ante Starčevića 1"
    assert (task["lat"], task["lng"]) == GRUZ
    assert task["accessDetails"] == "Gate code 1234"
    assert task["careContact"] == {"id": str(contact.pk), "name": "Mama Kata"}
    assert task["textMasked"] is False
    assert task["holdReasons"] == []
    assert (task["approximateLat"], task["approximateLng"]) != GRUZ
    assert payload["id"] == task["id"]
    assert payload["warnings"] == []


def test_signed_out_callers_cant_write(client: Client) -> None:
    assert error_codes(gql(client, POST, {"input": task_input()})) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, SAVE, {"input": {"title": "x"}})) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, MINE)) == ["UNAUTHENTICATED"]
    assert not Task.objects.exists()


def test_posting_needs_a_verified_phone(client: Client) -> None:
    token = sign_in(make_user("ivo")).access_token
    assert error_codes(post(client, token)) == ["PHONE_NOT_VERIFIED"]
    assert not Task.objects.exists()


def test_a_suspended_poster_cant_post_but_keeps_their_tasks(client: Client) -> None:
    ana = make_poster()
    token = sign_in(ana).access_token
    task_id = post(client, token)["data"]["postTask"]["task"]["id"]
    draft_id = gql(client, SAVE, {"input": {"title": "Paint"}}, token=token)["data"][
        "saveTaskDraft"
    ]["id"]
    ana.status = User.Status.SUSPENDED
    ana.save(update_fields=["status"])

    assert error_codes(post(client, token)) == ["ACCOUNT_SUSPENDED"]
    published = gql(client, PUBLISH, {"id": draft_id}, token=token)
    assert error_codes(published) == ["ACCOUNT_SUSPENDED"]
    # History stays reachable.
    assert [task["id"] for task in gql(client, MINE, token=token)["data"]["myTasks"]] == [
        draft_id,
        task_id,
    ]
    assert Task.objects.count() == 2


LIMITED = [
    (User.Status.SUSPENDED, "ACCOUNT_SUSPENDED"),
    (User.Status.DELETION_PENDING, "ACCOUNT_DELETION_PENDING"),
]


@pytest.mark.parametrize(("status", "code"), LIMITED)
@pytest.mark.parametrize(
    "change",
    [
        {"title": "Paint the fence"},
        {"budgetCents": 9000},
        {"location": {"address": "Lapad 3", "lat": GRUZ[0] + 0.001, "lng": GRUZ[1]}},
    ],
    ids=["title", "budget", "location"],
)
def test_a_limited_poster_cant_turn_an_open_task_into_new_work(
    client: Client, status: str, code: str, change: dict[str, Any]
) -> None:
    ana = make_poster()
    token = sign_in(ana).access_token
    task_id = post(client, token)["data"]["postTask"]["task"]["id"]
    ana.status = status
    ana.save(update_fields=["status"])

    body = gql(client, UPDATE, {"id": task_id, "input": change}, token=token)

    assert error_codes(body) == [code]
    task = Task.objects.get()
    assert (task.title, task.budget_cents, task.address) == (
        "Mow the lawn",
        3500,
        "Ulica Ante Starčevića 1",
    )


def test_a_limited_poster_still_changes_photos_and_access_details(client: Client) -> None:
    ana = make_poster()
    token = sign_in(ana).access_token
    task_id = post(client, token)["data"]["postTask"]["task"]["id"]
    ana.status = User.Status.SUSPENDED
    ana.save(update_fields=["status"])

    changed = gql(
        client,
        UPDATE,
        {"id": task_id, "input": {"accessDetails": "Key under the mat"}},
        token=token,
    )

    assert changed["data"]["updateTask"]["task"]["accessDetails"] == "Key under the mat"


@pytest.mark.parametrize("lat_lng", [OSIJEK, VIENNA], ids=["osijek", "vienna"])
def test_posting_outside_the_service_areas_is_refused(
    client: Client, lat_lng: tuple[float, float]
) -> None:
    location = {"address": "Somewhere", "lat": lat_lng[0], "lng": lat_lng[1]}
    body = post(client, sign_in(make_poster()).access_token, location=location)
    assert error_codes(body) == ["OUT_OF_SERVICE_AREA"]
    assert not Task.objects.exists()


def test_moving_a_task_out_of_the_area_is_refused(client: Client) -> None:
    token = sign_in(make_poster()).access_token
    task_id = post(client, token)["data"]["postTask"]["task"]["id"]
    location = {"address": "Osijek", "lat": OSIJEK[0], "lng": OSIJEK[1]}

    body = gql(client, UPDATE, {"id": task_id, "input": {"location": location}}, token=token)

    assert error_codes(body) == ["OUT_OF_SERVICE_AREA"]
    assert Task.objects.get().address == "Ulica Ante Starčevića 1"


@pytest.mark.parametrize("category", ["RIDES", "MEDICAL_CARE", "PERSONAL_CARE"])
def test_excluded_categories_arent_in_the_api(client: Client, category: str) -> None:
    """The TaskCategory enum lists only allowed categories, so GraphQL refuses the request
    before any resolver runs; the service refuses them too (CATEGORY_NOT_ALLOWED)."""
    body = post(client, sign_in(make_poster()).access_token, category=category)
    assert body.get("data") is None
    assert "TaskCategory" in body["errors"][0]["message"]
    assert not Task.objects.exists()


def test_a_minimum_budget_and_an_owned_contact_are_enforced(client: Client) -> None:
    token = sign_in(make_poster()).access_token
    assert error_codes(post(client, token, budgetCents=900)) == ["BUDGET_BELOW_MINIMUM"]
    foreign = str(make_contact(make_user("ivo")).pk)
    assert error_codes(post(client, token, careContactId=foreign)) == ["CARE_CONTACT_NOT_FOUND"]


def test_others_see_the_approximate_area_only(client: Client) -> None:
    ana = make_poster()
    contact = make_contact(ana)
    photo = ready_photo(ana, public_key="tasks/photo.jpg")
    body = post(
        client,
        sign_in(ana).access_token,
        careContactId=str(contact.pk),
        photoIds=[str(photo.pk)],
    )
    task_id = body["data"]["postTask"]["task"]["id"]

    for token in (sign_in(make_poster("ivo")).access_token, None):
        seen = gql(client, TASK, {"id": task_id}, token=token)["data"]["task"]
        assert seen["id"] == task_id
        assert seen["title"] == "Mow the lawn"
        assert seen["approximateLat"] and seen["approximateLng"]
        assert seen["serviceAreaName"] == "Dubrovnik"
        assert seen["isMine"] is False
        for private in ("address", "lat", "lng", "accessDetails", "careContact"):
            assert seen[private] is None
        assert seen["textMasked"] is None and seen["holdReasons"] is None
        assert seen["photos"] == [
            {"id": str(photo.pk), "url": "https://photos.test.taskbeavo.invalid/tasks/photo.jpg"}
        ]


def test_drafts_and_held_tasks_are_hidden_from_others(client: Client) -> None:
    ana_token = sign_in(make_poster()).access_token
    draft = gql(client, SAVE, {"input": {"title": "Lawn"}}, token=ana_token)
    draft_id = draft["data"]["saveTaskDraft"]["task"]["id"]
    held = post(client, ana_token, description="Zovi 091\n234\n5678")["data"]["postTask"]["task"]
    assert held["status"] == "PENDING_REVIEW"
    assert held["holdReasons"] == ["CONTACT_DETAILS"]

    ivo_token = sign_in(make_poster("ivo")).access_token
    for task_id in (draft_id, held["id"]):
        assert error_codes(gql(client, TASK, {"id": task_id}, token=ivo_token)) == [
            "TASK_NOT_FOUND"
        ]
        assert gql(client, TASK, {"id": task_id}, token=ana_token)["data"]["task"]["id"] == task_id


def test_masked_text_is_reported_to_the_poster(client: Client) -> None:
    token = sign_in(make_poster()).access_token
    task = post(client, token, description="Call 091 234 5678")["data"]["postTask"]["task"]
    assert task["textMasked"] is True
    assert "091" not in task["description"]


def test_a_draft_is_saved_then_published(client: Client) -> None:
    token = sign_in(make_poster()).access_token
    saved = gql(client, SAVE, {"input": {"title": "Lawn"}}, token=token)["data"]["saveTaskDraft"]
    assert saved["task"]["status"] == "DRAFT"
    task_id = saved["task"]["id"]

    full = task_input()
    gql(client, SAVE, {"id": task_id, "input": full}, token=token)
    published = gql(client, PUBLISH, {"id": task_id}, token=token)["data"]["publishTaskDraft"]

    assert published["task"]["status"] == "OPEN"
    assert published["task"]["id"] == task_id


def test_a_duplicate_is_a_warning(client: Client) -> None:
    token = sign_in(make_poster()).access_token
    post(client, token)
    second = post(client, token, title="Trim the hedge")["data"]["postTask"]
    assert second["warnings"] == ["DUPLICATE_TASK"]


def test_my_tasks_lists_the_posters_tasks_newest_first(client: Client) -> None:
    ana_token = sign_in(make_poster()).access_token
    first = post(client, ana_token)["data"]["postTask"]["task"]["id"]
    second = post(client, ana_token, title="Trim")["data"]["postTask"]["task"]["id"]
    post(client, sign_in(make_poster("ivo")).access_token)

    mine = gql(client, MINE, token=ana_token)["data"]["myTasks"]

    assert [task["id"] for task in mine] == [second, first]
    assert all(task["address"] for task in mine)


def test_posting_is_rate_limited(client: Client, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"postTask": 2}
    token = sign_in(make_poster()).access_token
    post(client, token)
    post(client, token, title="Trim")
    assert error_codes(post(client, token, title="Again")) == ["RATE_LIMITED"]
    assert Task.objects.count() == 2
