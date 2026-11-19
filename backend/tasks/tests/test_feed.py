"""The helper feed, the public browse list and public task listings (BEAV-89): only open
tasks in active areas and the helper's own areas (rule j), a neutral newest-first order
(rule k), and never the exact place or access details, to anyone (rule e)."""

import datetime as dt
import json
from typing import Any

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import sign_in
from accounts.tests.test_schema import error_codes, gql
from areas.models import ServiceArea
from bookings.models import Booking
from bookings.tests.factories import make_booking
from offers import services as offers
from offers.tests.factories import make_helper, open_task
from offers.tests.test_business_posters import become_business, make_trader
from tasks import feed
from tasks.models import Task
from tasks.services import Location
from tasks.tests.factories import make_poster, window

pytestmark = pytest.mark.django_db

SECRET_ADDRESS = "Ulica Ante Starčevića 1, Dubrovnik"
SECRET_ACCESS = "Gate code 1234, key under the mat"

LISTING = """
id category title description approximateLat approximateLng serviceAreaName windowStart
windowEnd flexibleDate budgetCents photos { id url } isMine publishedAt
"""
FEED = f"""
query($first: Int, $after: String, $category: TaskCategory) {{
  taskFeed(first: $first, after: $after, category: $category) {{
    id endCursor hasMore items {{ {LISTING} }}
  }}
}}
"""
OPEN_TASKS = f"""
query($areaSlug: String, $first: Int, $after: String) {{
  openTasks(areaSlug: $areaSlug, first: $first, after: $after) {{
    id endCursor hasMore items {{ {LISTING} }}
  }}
}}
"""
LISTING_QUERY = f"query($id: ID!) {{ taskListing(id: $id) {{ {LISTING} }} }}"


def split_area() -> ServiceArea:
    split = Polygon.from_bbox((16.40, 43.48, 16.50, 43.54))
    return ServiceArea.objects.create(
        name="Split", slug="split", active=True, polygon=MultiPolygon(split, srid=4326)
    )


def task_in_split(**fields: Any) -> Task:
    split_area()
    return open_task(location=Location("Riva 1, Split", 43.508, 16.44), **fields)


def precise_task(**fields: Any) -> Task:
    """A task whose exact coordinates can't coincide with the approximate grid."""
    return open_task(location=Location(SECRET_ADDRESS, 42.661373, 18.085331), **fields)


def ids(page: feed.Page) -> list[str]:
    return [str(task.pk) for task in page.tasks]


def published(task: Task, at: dt.datetime) -> Task:
    Task.objects.filter(pk=task.pk).update(published_at=at)
    task.refresh_from_db()
    return task


# --- which tasks the helper's feed holds ---------------------------------------------


def test_the_feed_holds_open_tasks_in_the_helpers_areas_only() -> None:
    here = open_task()
    elsewhere = task_in_split()
    marko = make_helper()

    assert ids(feed.helper_feed(marko)) == [str(here.pk)]
    assert str(elsewhere.pk) in ids(feed.browse())


def test_the_feed_follows_the_areas_the_helper_selected() -> None:
    open_task()
    in_split = task_in_split()
    marko = make_helper()
    marko.helper_profile.service_areas.set([ServiceArea.objects.get(slug="split")])

    assert ids(feed.helper_feed(marko)) == [str(in_split.pk)]


def test_a_switched_off_area_leaves_the_feed_and_the_browse_list() -> None:
    open_task()
    marko = make_helper()
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)

    assert feed.helper_feed(marko).tasks == []
    assert feed.browse().tasks == []


def test_a_user_without_a_helper_profile_gets_an_empty_feed() -> None:
    open_task()

    assert feed.helper_feed(make_poster("ivo")).tasks == []


def test_the_feed_leaves_out_the_helpers_own_tasks() -> None:
    marko = make_helper()
    open_task(poster=marko)

    assert feed.helper_feed(marko).tasks == []


def test_booked_closed_and_past_tasks_are_left_out() -> None:
    booked = make_booking(Booking.Status.FUNDS_HELD).task
    closed = open_task()
    Task.objects.filter(pk=closed.pk).update(status=Task.Status.CLOSED)
    past = open_task()
    Task.objects.filter(pk=past.pk).update(window_end=timezone.now() - dt.timedelta(hours=1))
    stale = open_task(flexible_date=True, window_start=None, window_end=None)
    published(stale, timezone.now() - dt.timedelta(days=31))
    fresh = open_task()
    marko = make_helper("marko2")

    assert ids(feed.helper_feed(marko)) == [str(fresh.pk)]
    assert str(booked.pk) not in ids(feed.browse())


def test_a_failed_checkout_puts_the_task_back() -> None:
    booking = make_booking(Booking.Status.PAYMENT_FAILED)

    assert ids(feed.browse()) == [str(booking.task.pk)]


def test_a_suspended_posters_tasks_are_left_out() -> None:
    task = open_task()
    User.objects.filter(pk=task.poster_id).update(status=User.Status.SUSPENDED)

    assert feed.browse().tasks == []


def test_a_business_posters_tasks_reach_registered_helpers_only() -> None:
    task = open_task()
    become_business(task.poster)
    private = make_helper()
    trader = make_trader()

    assert feed.helper_feed(private).tasks == []
    assert ids(feed.helper_feed(trader)) == [str(task.pk)]


def test_a_helper_who_already_offered_still_sees_the_task() -> None:
    task = open_task()
    marko = make_helper()
    offers.send_offer(marko, str(task.pk), 3_000)

    assert ids(feed.helper_feed(marko)) == [str(task.pk)]


def test_the_feed_filters_by_category() -> None:
    garden = open_task()
    errands = open_task(category="ERRANDS")
    marko = make_helper()

    assert ids(feed.helper_feed(marko, category="GARDEN")) == [str(garden.pk)]
    assert ids(feed.helper_feed(marko, category="ERRANDS")) == [str(errands.pk)]


# --- order and pages (rule k) ---------------------------------------------------------


def test_newest_published_first_whatever_the_date_budget_or_offers() -> None:
    now = timezone.now()
    old = published(open_task(budget_cents=9_000), now - dt.timedelta(hours=3))
    new = published(open_task(budget_cents=1_000), now - dt.timedelta(hours=1))
    start, end = window(days=1)
    middle = published(open_task(window_start=start, window_end=end), now - dt.timedelta(hours=2))
    offers.send_offer(make_helper("other"), str(old.pk), 3_000)

    expected = [str(new.pk), str(middle.pk), str(old.pk)]
    assert ids(feed.helper_feed(make_helper())) == expected
    assert ids(feed.browse()) == expected


def test_pages_follow_the_cursor_without_gaps_or_repeats() -> None:
    now = timezone.now()
    tasks = [published(open_task(), now - dt.timedelta(minutes=i)) for i in range(5)]
    # Two tasks published at the same moment are ordered by id.
    published(tasks[4], tasks[3].published_at or now)
    marko = make_helper()

    first = feed.helper_feed(marko, first=2)
    second = feed.helper_feed(marko, first=2, after=first.end_cursor)
    third = feed.helper_feed(marko, first=2, after=second.end_cursor)

    seen = ids(first) + ids(second) + ids(third)
    assert sorted(seen) == sorted(str(t.pk) for t in tasks)
    assert len(set(seen)) == 5
    assert (first.has_more, second.has_more, third.has_more) == (True, True, False)


def test_page_size_is_capped() -> None:
    open_task()
    assert len(feed.browse(first=0).tasks) == 1
    assert feed.MAX_PAGE_SIZE == 50


# --- over GraphQL ---------------------------------------------------------------------


def _assert_no_exact_place(body: dict[str, Any], task: Task) -> None:
    text = json.dumps(body, ensure_ascii=False)
    assert SECRET_ADDRESS not in text
    assert "Starčevića" not in text
    assert SECRET_ACCESS not in text
    assert "1234" not in text
    assert task.location is not None
    assert "42.661373" not in text
    assert "18.085331" not in text


def test_the_feed_over_graphql_shows_the_approximate_area_only(client: Client) -> None:
    task = precise_task()
    marko = make_helper()

    body = gql(client, FEED, {"first": 10}, token=sign_in(marko).access_token)

    page = body["data"]["taskFeed"]
    assert [item["id"] for item in page["items"]] == [str(task.pk)]
    item = page["items"][0]
    assert item["serviceAreaName"] == "Dubrovnik"
    assert item["approximateLat"] is not None
    assert item["isMine"] is False
    assert page["hasMore"] is False
    _assert_no_exact_place(body, task)


def test_the_exact_place_fields_dont_exist_on_listings(client: Client) -> None:
    """The type has no exact-place fields at all, so no viewer can ask for them."""
    task = precise_task()
    for field in ("address", "lat", "lng", "accessDetails", "onSiteContact"):
        query = f'{{ taskListing(id: "{task.pk}") {{ id {field} }} }}'
        body = gql(client, query)
        assert "errors" in body and "Cannot query field" in body["errors"][0]["message"]


def test_the_feed_needs_a_signed_in_user(client: Client) -> None:
    assert error_codes(gql(client, FEED)) == ["UNAUTHENTICATED"]


def test_a_bad_cursor_is_refused(client: Client) -> None:
    marko = make_helper()
    body = gql(client, FEED, {"after": "not-a-cursor"}, token=sign_in(marko).access_token)
    assert error_codes(body) == ["INVALID_CURSOR"]


def test_open_tasks_are_public_and_redacted(client: Client) -> None:
    task = precise_task()

    body = gql(client, OPEN_TASKS, {"areaSlug": "dubrovnik"})

    assert [item["id"] for item in body["data"]["openTasks"]["items"]] == [str(task.pk)]
    _assert_no_exact_place(body, task)


def test_open_tasks_in_an_unknown_area_are_refused(client: Client) -> None:
    body = gql(client, OPEN_TASKS, {"areaSlug": "zagreb"})
    assert error_codes(body) == ["SERVICE_AREA_NOT_FOUND"]


def test_open_tasks_by_area(client: Client) -> None:
    open_task()
    in_split = task_in_split()

    body = gql(client, OPEN_TASKS, {"areaSlug": "split"})

    assert [item["id"] for item in body["data"]["openTasks"]["items"]] == [str(in_split.pk)]


def test_a_public_listing_is_redacted_for_everyone(client: Client) -> None:
    """Signed out, a helper who offered, and the poster: approximate area only."""
    task = precise_task()
    marko = make_helper()
    offers.send_offer(marko, str(task.pk), 3_000)

    for token in (None, sign_in(marko).access_token, sign_in(task.poster).access_token):
        body = gql(client, LISTING_QUERY, {"id": str(task.pk)}, token=token)
        assert body["data"]["taskListing"]["id"] == str(task.pk)
        _assert_no_exact_place(body, task)


def test_the_booked_helper_gets_a_listing_without_the_exact_place(client: Client) -> None:
    booking = make_booking(
        Booking.Status.FUNDS_HELD, location=Location(SECRET_ADDRESS, 42.661373, 18.085331)
    )
    token = sign_in(booking.helper).access_token

    body = gql(client, LISTING_QUERY, {"id": str(booking.task.pk)}, token=token)

    assert body["data"]["taskListing"]["id"] == str(booking.task.pk)
    _assert_no_exact_place(body, booking.task)


def test_a_closed_or_draft_task_has_no_public_listing(client: Client) -> None:
    task = precise_task()
    Task.objects.filter(pk=task.pk).update(status=Task.Status.CLOSED)

    assert error_codes(gql(client, LISTING_QUERY, {"id": str(task.pk)})) == ["TASK_NOT_FOUND"]
    mine = gql(client, LISTING_QUERY, {"id": str(task.pk)}, token=sign_in(task.poster).access_token)
    assert mine["data"]["taskListing"]["isMine"] is True


def test_the_feed_runs_a_fixed_number_of_queries(
    client: Client, django_assert_max_num_queries: Any
) -> None:
    for _ in range(5):
        open_task()
    token = sign_in(make_helper()).access_token

    with django_assert_max_num_queries(15):
        body = gql(client, FEED, {"first": 50}, token=token)
    assert len(body["data"]["taskFeed"]["items"]) == 5
