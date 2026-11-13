"""Offer rules: who may send, the service area (rule j), price, message filter, the 7-day
lifetime capped at the task's date, renewal, Needs update, withdrawal and expiry."""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.states import AccountState, account_state
from accounts.tests.factories import make_user
from areas.models import ServiceArea
from core.errors import ApiError
from core.models import Outbox
from helpers.models import HelperProfile
from helpers.tests.business import give_business_address
from legal.models import LegalDocument
from legal.tests.factories import publish_version
from offers import services
from offers.models import Offer, OfferEvent
from offers.tests.factories import make_helper, open_task
from payments.models import ConnectedAccount
from tasks import services as tasks
from tasks.models import Task
from tasks.tests.factories import OLD_TOWN, VIENNA, make_poster, window

pytestmark = pytest.mark.django_db


def code_of(call: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ApiError) as raised:
        call(*args, **kwargs)
    return raised.value.code.name


def events(offer: Offer) -> list[str]:
    return list(OfferEvent.objects.filter(offer=offer).values_list("type", flat=True))


# --- sending ---------------------------------------------------------------------------


def test_a_helper_sends_an_offer_that_lasts_seven_days() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    now = timezone.now()

    offer = services.send_offer(marko, str(task.pk), 3_000, "Can come Saturday at 9.", now=now)

    assert offer.status == Offer.Status.SENT
    assert (offer.price_cents, offer.message) == (3_000, "Can come Saturday at 9.")
    assert offer.expires_at == now + dt.timedelta(days=7)
    assert events(offer) == ["sent"]


def test_the_expiry_never_passes_the_task_date() -> None:
    start, end = window(days=2)
    task = open_task(window_start=start, window_end=end)

    offer = services.send_offer(make_helper(), str(task.pk), 3_000)

    assert offer.expires_at == end


def test_a_flexible_task_caps_offers_at_its_30_day_open_period() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    assert task.published_at is not None
    later = task.published_at + dt.timedelta(days=27)

    offer = services.send_offer(make_helper(), str(task.pk), 3_000, now=later)

    assert offer.expires_at == task.published_at + dt.timedelta(days=30)


def test_signed_out_and_unverified_users_cannot_send() -> None:
    task = open_task()
    assert code_of(services.send_offer, None, str(task.pk), 3_000) == "UNAUTHENTICATED"
    assert code_of(services.send_offer, make_user("ivo"), str(task.pk), 3_000) == (
        "PHONE_NOT_VERIFIED"
    )


def test_a_user_without_a_helper_profile_cannot_send() -> None:
    task = open_task()
    luka = make_poster("luka")
    assert code_of(services.send_offer, luka, str(task.pk), 3_000) == "HELPER_PROFILE_INCOMPLETE"


@pytest.mark.parametrize(
    "status", [ConnectedAccount.Status.NEEDS_INFO, ConnectedAccount.Status.VERIFYING]
)
def test_a_payout_account_that_is_not_active_blocks_offers(status: str) -> None:
    task = open_task()
    marko = make_helper()
    ConnectedAccount.objects.filter(helper__user=marko).update(status=status)

    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == "PAYOUT_ACCOUNT_NOT_READY"


def test_an_unverified_company_cannot_send() -> None:
    task = open_task()
    marko = make_helper()
    HelperProfile.objects.filter(user=marko).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.COMPANY,
        business_name="Beavo d.o.o.",
        business_oib="00000000948",
    )

    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == "HELPER_PROFILE_INCOMPLETE"


def test_an_obrt_without_its_registered_business_address_cannot_send() -> None:
    """The contract confirmation shows it to the poster; the home address never stands in
    for it (user decision 2026-11-04)."""
    task = open_task()
    marko = make_helper()
    profile = HelperProfile.objects.get(user=marko)
    assert profile.address_street  # A home address alone isn't enough.
    HelperProfile.objects.filter(user=marko).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.OBRT,
        business_name="Obrt Marko",
        business_oib=profile.oib,
    )

    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == ("BUSINESS_ADDRESS_REQUIRED")

    give_business_address(marko)
    assert services.send_offer(marko, str(task.pk), 3_000).status == Offer.Status.SENT


def test_nobody_offers_on_their_own_task() -> None:
    marko = make_helper()
    task = open_task(poster=marko)
    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == "OFFER_ON_OWN_TASK"


def test_only_open_tasks_take_offers() -> None:
    marko = make_helper()
    draft = tasks.save_draft(make_poster("ana"), tasks.TaskChanges(title="Lawn")).task
    closed = tasks.close_task(open_task())

    assert code_of(services.send_offer, marko, str(draft.pk), 3_000) == "TASK_NOT_FOUND"
    assert code_of(services.send_offer, marko, str(closed.pk), 3_000) == "TASK_NOT_FOUND"
    assert code_of(services.send_offer, marko, "not-a-uuid", 3_000) == "TASK_NOT_FOUND"


def test_a_task_whose_date_has_passed_takes_no_offers() -> None:
    start, end = window(days=1)
    task = open_task(window_start=start, window_end=end)

    later = end + dt.timedelta(minutes=1)
    assert code_of(services.send_offer, make_helper(), str(task.pk), 3_000, now=later) == (
        "TASK_NOT_OPEN_FOR_OFFERS"
    )


def move_to_vienna(task: Task) -> None:
    """The task's stored place ends up outside every active area (an area redrawn or
    switched off, or a place changed by staff); posting and update_task refuse it."""
    Task.objects.filter(pk=task.pk).update(location=Point(VIENNA[1], VIENNA[0], srid=4326))


def test_the_service_area_is_checked_again_when_sending() -> None:
    task = open_task()
    marko = make_helper()
    # A second area keeps the helper's profile complete while Dubrovnik is switched off.
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)
    split = Polygon.from_bbox((16.40, 43.48, 16.50, 43.54))
    ServiceArea.objects.create(
        name="Split", slug="split", active=True, polygon=MultiPolygon(split, srid=4326)
    ).helpers.add(marko.helper_profile)

    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == "OUT_OF_SERVICE_AREA"
    assert not Offer.objects.exists()


def test_a_task_moved_outside_every_area_gets_out_of_service_area() -> None:
    """A stored place outside every active area (moved by staff, or an area redrawn)."""
    task = open_task()
    move_to_vienna(task)

    assert code_of(services.send_offer, make_helper(), str(task.pk), 3_000) == (
        "OUT_OF_SERVICE_AREA"
    )


def test_helpers_offer_only_inside_their_own_service_areas() -> None:
    task = open_task()
    marko = make_helper()
    split = Polygon.from_bbox((16.40, 43.48, 16.50, 43.54))
    area = ServiceArea.objects.create(
        name="Split", slug="split", active=True, polygon=MultiPolygon(split, srid=4326)
    )
    marko.helper_profile.service_areas.set([area])

    assert code_of(services.send_offer, marko, str(task.pk), 3_000) == ("TASK_OUTSIDE_HELPER_AREAS")


def test_the_price_is_at_least_ten_euros() -> None:
    task = open_task()
    marko = make_helper()

    assert code_of(services.send_offer, marko, str(task.pk), 999) == "OFFER_PRICE_BELOW_MINIMUM"
    assert code_of(services.send_offer, marko, str(task.pk), 1_000_001) == "INVALID_OFFER_DETAILS"
    assert services.send_offer(marko, str(task.pk), 1_000).price_cents == 1_000


def test_a_long_message_is_refused() -> None:
    task = open_task()
    assert code_of(services.send_offer, make_helper(), str(task.pk), 3_000, "x" * 1_001) == (
        "INVALID_OFFER_DETAILS"
    )


def test_contact_details_in_the_message_are_masked() -> None:
    task = open_task()

    offer = services.send_offer(make_helper(), str(task.pk), 3_000, "Call me on 091 234 5678")

    assert "234" not in offer.message
    assert offer.moderation_verdict == "MASKED"


def test_a_doubtful_contact_detail_is_refused() -> None:
    task = open_task()
    text = "My numbers: 091 234 5678 098 765 4321"

    assert code_of(services.send_offer, make_helper(), str(task.pk), 3_000, text) == (
        "OFFER_MESSAGE_CONTACT_DETAILS"
    )


def test_one_live_offer_per_helper_and_task() -> None:
    task = open_task()
    marko = make_helper()
    services.send_offer(marko, str(task.pk), 3_000)

    assert code_of(services.send_offer, marko, str(task.pk), 2_500) == "OFFER_ALREADY_SENT"
    # Other helpers may offer too.
    services.send_offer(make_helper("ivan"), str(task.pk), 2_500)


def test_a_new_offer_may_follow_a_withdrawn_one() -> None:
    task = open_task()
    marko = make_helper()
    first = services.send_offer(marko, str(task.pk), 3_000)
    services.withdraw_offer(marko, str(first.pk))

    assert services.send_offer(marko, str(task.pk), 2_800).status == Offer.Status.SENT


# --- changing --------------------------------------------------------------------------


def test_the_helper_changes_price_and_message() -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000, "Saturday")

    changed = services.update_offer(marko, str(offer.pk), price_cents=2_800, message="Sunday")

    assert (changed.price_cents, changed.message, changed.expires_at) == (
        2_800,
        "Sunday",
        offer.expires_at,
    )
    assert events(changed) == ["sent", "updated"]


def test_only_the_offers_helper_changes_it() -> None:
    offer = services.send_offer(make_helper(), str(open_task().pk), 3_000)
    assert code_of(
        services.update_offer, make_helper("ivan"), str(offer.pk), price_cents=2_000
    ) == ("OFFER_NOT_FOUND")


def test_changing_checks_price_and_service_area() -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    assert code_of(services.update_offer, marko, str(offer.pk), price_cents=500) == (
        "OFFER_PRICE_BELOW_MINIMUM"
    )
    move_to_vienna(task)
    assert code_of(services.update_offer, marko, str(offer.pk), price_cents=2_000) == (
        "OUT_OF_SERVICE_AREA"
    )


def test_a_withdrawn_or_expired_offer_cannot_be_changed() -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    later = offer.expires_at + dt.timedelta(seconds=1)
    assert code_of(services.update_offer, marko, str(offer.pk), price_cents=2_000, now=later) == (
        "OFFER_NOT_EDITABLE"
    )
    services.withdraw_offer(marko, str(offer.pk))
    assert code_of(services.update_offer, marko, str(offer.pk), price_cents=2_000) == (
        "OFFER_NOT_EDITABLE"
    )


# --- needs update ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"location": tasks.Location("Stradun 1, Dubrovnik", *OLD_TOWN)},
        {"window_start": window(days=5)[0], "window_end": window(days=5)[1]},
        {"description": "Paint the fence as well as mowing the lawn."},
        {"category": "CLEANING"},
    ],
    ids=["address", "date", "scope", "category"],
)
def test_a_big_task_change_marks_offers_needs_update(change: dict[str, Any]) -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(**change))

    offer.refresh_from_db()
    assert offer.status == Offer.Status.NEEDS_UPDATE
    assert events(offer) == ["sent", "needs_update"]


@pytest.mark.parametrize(
    "change",
    [{"title": "Mow my lawn"}, {"budget_cents": 4_000}, {"access_details": "Key with Mia"}],
    ids=["title", "budget", "access"],
)
def test_a_small_task_change_leaves_offers_alone(change: dict[str, Any]) -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)

    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(**change))

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT


def test_the_same_values_again_are_not_a_change() -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)

    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(description=task.description))

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT


def test_the_helper_confirms_or_changes_an_offer_that_needs_an_update() -> None:
    task = open_task()
    marko = make_helper()
    ivan = make_helper("ivan")
    confirmed = services.send_offer(marko, str(task.pk), 3_000)
    changed = services.send_offer(ivan, str(task.pk), 3_200)
    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(category="CLEANING"))

    assert services.update_offer(marko, str(confirmed.pk)).status == Offer.Status.SENT
    after = services.update_offer(ivan, str(changed.pk), price_cents=3_600)
    assert (after.status, after.price_cents) == (Offer.Status.SENT, 3_600)


def test_withdrawn_offers_are_not_marked() -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    services.withdraw_offer(marko, str(offer.pk))

    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(category="CLEANING"))

    offer.refresh_from_db()
    assert offer.status == Offer.Status.WITHDRAWN


# --- renewing --------------------------------------------------------------------------


def test_renewing_restarts_the_seven_days_from_now() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    later = timezone.now() + dt.timedelta(days=6)
    Offer.objects.filter(pk=offer.pk).update(expiry_reminder_queued_at=later)

    renewed = services.renew_offer(marko, str(offer.pk), now=later)

    assert renewed.expires_at == later + dt.timedelta(days=7)
    assert renewed.expiry_reminder_queued_at is None
    assert events(renewed) == ["sent", "renewed"]


def test_renewing_never_passes_the_task_date() -> None:
    start, end = window(days=10)
    task = open_task(window_start=start, window_end=end)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    renewed = services.renew_offer(marko, str(offer.pk), now=timezone.now() + dt.timedelta(days=6))

    assert renewed.expires_at == end


def test_an_expired_offer_can_be_renewed() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    later = offer.expires_at + dt.timedelta(hours=1)
    services.expire_offers(later)

    renewed = services.renew_offer(marko, str(offer.pk), now=later)

    assert renewed.status == Offer.Status.SENT
    assert renewed.closed_at is None
    assert renewed.expires_at == later + dt.timedelta(days=7)


def test_an_expired_offer_is_not_renewed_beside_a_newer_live_one() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    old = services.send_offer(marko, str(task.pk), 3_000)
    later = old.expires_at + dt.timedelta(hours=1)
    services.expire_offers(later)
    services.send_offer(marko, str(task.pk), 3_100, now=later)

    assert code_of(services.renew_offer, marko, str(old.pk), now=later) == "OFFER_ALREADY_SENT"


def test_renewal_rechecks_area_date_and_withdrawal() -> None:
    start, end = window(days=2)
    task = open_task(window_start=start, window_end=end)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    after_date = end + dt.timedelta(minutes=1)
    assert code_of(services.renew_offer, marko, str(offer.pk), now=after_date) == (
        "TASK_NOT_OPEN_FOR_OFFERS"
    )
    move_to_vienna(task)
    assert code_of(services.renew_offer, marko, str(offer.pk)) == "OUT_OF_SERVICE_AREA"
    services.withdraw_offer(marko, str(offer.pk))
    assert code_of(services.renew_offer, marko, str(offer.pk)) == "OFFER_NOT_EDITABLE"


# --- withdrawing -----------------------------------------------------------------------


def test_withdrawing_writes_the_event_and_outbox_row_in_one_transaction() -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    withdrawn = services.withdraw_offer(marko, str(offer.pk))

    assert withdrawn.status == Offer.Status.WITHDRAWN
    assert withdrawn.closed_at is not None
    assert events(withdrawn) == ["sent", "withdrawn"]
    row = Outbox.objects.get(topic="offer.withdrawn")
    assert row.aggregate_id == str(offer.pk)
    assert row.payload == {"offer_id": str(offer.pk), "task_id": str(task.pk)}


def test_a_failed_withdrawal_leaves_no_outbox_row(monkeypatch: pytest.MonkeyPatch) -> None:
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(services, "_event", broken)
    with pytest.raises(RuntimeError):
        services.withdraw_offer(marko, str(offer.pk))

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT
    assert not Outbox.objects.filter(topic="offer.withdrawn").exists()


def test_withdrawing_needs_only_the_offers_helper() -> None:
    """A helper whose payout account Stripe paused can still pull an offer back."""
    task = open_task()
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    ConnectedAccount.objects.filter(helper__user=marko).update(
        status=ConnectedAccount.Status.NEEDS_INFO
    )

    assert code_of(services.withdraw_offer, make_helper("ivan"), str(offer.pk)) == (
        "OFFER_NOT_FOUND"
    )
    assert code_of(services.withdraw_offer, None, str(offer.pk)) == "UNAUTHENTICATED"
    assert services.withdraw_offer(marko, str(offer.pk)).status == Offer.Status.WITHDRAWN
    assert code_of(services.withdraw_offer, marko, str(offer.pk)) == "OFFER_NOT_EDITABLE"


# --- expiry and the reminder -----------------------------------------------------------


def test_offers_past_their_expiry_expire() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)

    assert services.expire_offers(offer.expires_at - dt.timedelta(seconds=1)) == 0
    assert services.expire_offers(offer.expires_at) == 1

    offer.refresh_from_db()
    assert offer.status == Offer.Status.EXPIRED
    assert events(offer) == ["sent", "expired"]
    assert services.expire_offers(offer.expires_at) == 0


def test_the_poster_reminder_is_queued_once_a_day_before_expiry() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    two_days_before = offer.expires_at - dt.timedelta(days=2)
    a_day_before = offer.expires_at - dt.timedelta(hours=23)

    assert services.queue_expiry_reminders(two_days_before) == 0
    assert services.queue_expiry_reminders(a_day_before) == 1
    assert services.queue_expiry_reminders(a_day_before) == 0

    row = Outbox.objects.get(topic="offer.expiring")
    assert (row.aggregate_id, row.payload) == (str(offer.pk), {"task_id": str(task.pk)})


def test_no_reminder_for_withdrawn_offers_or_closed_tasks() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    withdrawn = services.send_offer(marko, str(task.pk), 3_000)
    services.withdraw_offer(marko, str(withdrawn.pk))
    other = open_task(window_start=None, window_end=None, flexible_date=True)
    on_closed = services.send_offer(make_helper("ivan"), str(other.pk), 3_000)
    tasks.close_task(other)

    assert services.queue_expiry_reminders(on_closed.expires_at - dt.timedelta(hours=1)) == 0


def test_renewing_allows_another_reminder() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    services.queue_expiry_reminders(offer.expires_at - dt.timedelta(hours=1))

    renewed = services.renew_offer(marko, str(offer.pk))

    assert services.queue_expiry_reminders(renewed.expires_at - dt.timedelta(hours=1)) == 1


def test_mark_needs_update_runs_inside_the_task_transaction() -> None:
    task = open_task()
    services.send_offer(make_helper(), str(task.pk), 3_000)
    with transaction.atomic():
        locked = Task.objects.select_for_update().get(pk=task.pk)
        assert services.mark_needs_update(locked, task.poster) == 1


# --- review round 1 --------------------------------------------------------------------


def test_moving_the_date_earlier_caps_the_offer_expiry() -> None:
    start, end = window(days=10)
    task = open_task(window_start=start, window_end=end)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    assert offer.expires_at < end
    new_start, new_end = window(days=2)

    tasks.update_task(
        task.poster, str(task.pk), tasks.TaskChanges(window_start=new_start, window_end=new_end)
    )

    offer.refresh_from_db()
    assert (offer.status, offer.expires_at) == (Offer.Status.NEEDS_UPDATE, new_end)
    confirmed = services.update_offer(marko, str(offer.pk))
    assert (confirmed.status, confirmed.expires_at) == (Offer.Status.SENT, new_end)


def test_a_flexible_task_given_a_date_caps_its_offers() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    start, end = window(days=2)

    tasks.update_task(
        task.poster,
        str(task.pk),
        tasks.TaskChanges(flexible_date=False, window_start=start, window_end=end),
    )

    offer.refresh_from_db()
    assert offer.expires_at == end


def test_a_moved_expiry_may_remind_again() -> None:
    start, end = window(days=10)
    task = open_task(window_start=start, window_end=end)
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    Offer.objects.filter(pk=offer.pk).update(expiry_reminder_queued_at=timezone.now())
    new_start, new_end = window(days=4)

    tasks.update_task(
        task.poster, str(task.pk), tasks.TaskChanges(window_start=new_start, window_end=new_end)
    )

    offer.refresh_from_db()
    assert offer.expiry_reminder_queued_at is None


def flexible_offer_needing_update() -> Offer:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(category="CLEANING"))
    offer.refresh_from_db()
    assert offer.status == Offer.Status.NEEDS_UPDATE
    return offer


def test_renewing_an_expired_offer_that_needed_an_update_keeps_needs_update() -> None:
    offer = flexible_offer_needing_update()
    later = offer.expires_at + dt.timedelta(hours=1)
    services.expire_offers(later)

    renewed = services.renew_offer(offer.helper, str(offer.pk), now=later)

    assert renewed.status == Offer.Status.NEEDS_UPDATE


def test_renewing_past_expiry_before_the_beat_task_keeps_needs_update() -> None:
    offer = flexible_offer_needing_update()
    later = offer.expires_at + dt.timedelta(hours=1)

    assert services.renew_offer(offer.helper, str(offer.pk), now=later).status == (
        Offer.Status.NEEDS_UPDATE
    )
    assert services.renew_offer(offer.helper, str(offer.pk)).status == Offer.Status.NEEDS_UPDATE


def test_a_task_changed_after_an_offer_expired_needs_an_update_on_renewal() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    marko = make_helper()
    offer = services.send_offer(marko, str(task.pk), 3_000)
    later = offer.expires_at + dt.timedelta(hours=1)
    services.expire_offers(later)

    tasks.update_task(task.poster, str(task.pk), tasks.TaskChanges(category="CLEANING"))
    offer.refresh_from_db()
    assert offer.status == Offer.Status.EXPIRED

    assert services.renew_offer(marko, str(offer.pk), now=later).status == (
        Offer.Status.NEEDS_UPDATE
    )


def test_a_confirmed_offer_renews_as_sent() -> None:
    offer = flexible_offer_needing_update()
    services.update_offer(offer.helper, str(offer.pk))

    assert services.renew_offer(offer.helper, str(offer.pk)).status == Offer.Status.SENT


def test_closing_a_task_ends_its_live_offers() -> None:
    task = open_task()
    marko = make_helper()
    live = services.send_offer(marko, str(task.pk), 3_000)
    ivan = make_helper("ivan")
    withdrawn = services.send_offer(ivan, str(task.pk), 3_000)
    services.withdraw_offer(ivan, str(withdrawn.pk))

    tasks.close_task(task)

    live.refresh_from_db()
    withdrawn.refresh_from_db()
    assert (live.status, withdrawn.status) == (Offer.Status.EXPIRED, Offer.Status.WITHDRAWN)
    assert live.closed_at is not None
    assert events(live) == ["sent", "expired"]
    assert code_of(services.withdraw_offer, marko, str(live.pk)) == "OFFER_NOT_EDITABLE"


def test_a_task_covered_by_two_areas_counts_for_either() -> None:
    """A point on a shared boundary, or in overlapping areas: `assert_in_service_area`
    returns one of them, but the helper's other area covers it too."""
    task = open_task()
    marko = make_helper()
    around = Polygon.from_bbox((18.0, 42.6, 18.2, 42.7))
    overlap = ServiceArea.objects.create(
        name="Gruž", slug="gruz", active=True, polygon=MultiPolygon(around, srid=4326)
    )
    marko.helper_profile.service_areas.set([overlap])

    assert services.send_offer(marko, str(task.pk), 3_000).status == Offer.Status.SENT


def test_no_reminder_for_an_offer_whose_lifetime_was_a_day_or_less() -> None:
    start = timezone.now() + dt.timedelta(hours=1)
    task = open_task(window_start=start, window_end=start + dt.timedelta(hours=20))
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    assert offer.expires_at - offer.lifetime_started_at < dt.timedelta(days=1)

    assert services.queue_expiry_reminders(offer.expires_at - dt.timedelta(hours=1)) == 0


# --- a poster in limited mode (accounts.states; review BEAV-77 R1-6) --------------------

LIMITED_STATES = [User.Status.SUSPENDED, User.Status.DELETION_PENDING]


def _limit(poster: User, status: str) -> None:
    User.objects.filter(pk=poster.pk).update(status=status)


@pytest.mark.parametrize("status", LIMITED_STATES)
def test_a_limited_posters_task_takes_no_new_offers(status: str) -> None:
    task = open_task()
    _limit(task.poster, status)

    # The same code as a closed task: the helper never learns the poster's state.
    assert code_of(services.send_offer, make_helper(), str(task.pk), 3_000) == (
        "TASK_NOT_OPEN_FOR_OFFERS"
    )
    assert not Offer.objects.exists()


@pytest.mark.parametrize("status", LIMITED_STATES)
def test_a_limited_posters_task_takes_no_changed_or_renewed_offers(status: str) -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    _limit(task.poster, status)
    helper = User.objects.get(pk=offer.helper_id)

    assert code_of(services.update_offer, helper, str(offer.pk), price_cents=3_200) == (
        "TASK_NOT_OPEN_FOR_OFFERS"
    )
    assert code_of(services.renew_offer, helper, str(offer.pk)) == "TASK_NOT_OPEN_FOR_OFFERS"
    offer.refresh_from_db()
    assert offer.price_cents == 3_000


def test_a_poster_who_must_accept_new_terms_keeps_receiving_offers() -> None:
    """They accept the terms before they can accept an offer (user decision 2026-11-04)."""
    task = open_task()
    # TERMS take effect that the poster never accepted.
    publish_version(LegalDocument.Kind.TERMS, "2026-11")
    assert account_state(task.poster) is AccountState.TERMS_UPDATE_REQUIRED
    helper = make_helper()

    offer = services.send_offer(helper, str(task.pk), 3_000)
    offer = services.update_offer(helper, str(offer.pk), price_cents=3_200)
    offer = services.renew_offer(helper, str(offer.pk))

    assert (offer.status, offer.price_cents) == (Offer.Status.SENT, 3_200)
