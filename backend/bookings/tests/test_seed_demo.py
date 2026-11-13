"""manage.py seed_demo (BEAV-198): refused in production and without demo payments,
idempotent, obviously fake data, and every booking moved by the real code."""

import datetime as dt
from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import User
from bookings import cancel
from bookings.demo_seed import (
    DEMO_OIB_PREFIX,
    DOMAIN,
    SCENARIOS,
    SEED_SOURCE,
    USERS,
    DemoSeedRefused,
    Seeder,
    retire_demo_users,
)
from bookings.models import Booking, BookingEvent
from bookings.tests.factories import make_booking
from core.models import ActorKind, Outbox
from helpers.iban import normalize_iban
from helpers.models import HelperProfile
from helpers.oib import is_valid_oib
from offers.models import Offer
from offers.tests.factories import open_task
from payments.models import StripeEvent
from payments.tests.demo import demo_payments  # noqa: F401
from tasks.models import Task

pytestmark = pytest.mark.django_db

S = Booking.Status


def seed(*args: str) -> str:
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def booking(key: str) -> Booking:
    title = next(scenario.title for scenario in SCENARIOS if scenario.key == key)
    return Booking.objects.get(task__title=title, poster__is_active=True)


@override_settings(APP_ENV="production", DEMO_PAYMENTS=True)
def test_refused_in_production() -> None:
    with pytest.raises(CommandError, match="production"):
        seed()
    assert not User.objects.exists()


@pytest.mark.parametrize("key", ["sk_live_abc", "rk_live_abc"])
def test_refused_next_to_a_live_key(key: str) -> None:
    # Settings already refuse DEMO_PAYMENTS with a live key; the command checks again.
    with (
        override_settings(DEMO_PAYMENTS=True, STRIPE_SECRET_KEY=key),
        pytest.raises(CommandError, match="live"),
    ):
        seed("--reset")
    assert not User.objects.exists()


@override_settings(DEMO_PAYMENTS=False)
def test_refused_without_demo_payments() -> None:
    with pytest.raises(CommandError, match="DEMO_PAYMENTS"):
        seed()
    assert not User.objects.exists()


@override_settings(DEMO_PAYMENTS=False)
def test_the_module_refuses_on_its_own_too() -> None:
    # From a shell, past the command: nothing is seeded and nobody is retired.
    with pytest.raises(DemoSeedRefused, match="DEMO_PAYMENTS"):
        Seeder()
    with pytest.raises(DemoSeedRefused, match="DEMO_PAYMENTS"):
        retire_demo_users()
    assert not User.objects.exists()


def test_a_second_run_creates_nothing(demo_payments: None) -> None:  # noqa: F811
    seed()
    counts = [model.objects.count() for model in (User, Task, Offer, Booking, StripeEvent)]

    output = seed()

    assert [model.objects.count() for model in (User, Task, Offer, Booking, StripeEvent)] == counts
    assert "created" not in output
    assert all(f"exists   {scenario.key}" in output for scenario in SCENARIOS)


def test_a_scenario_that_fails_halfway_is_seeded_on_the_next_run(demo_payments: None) -> None:  # noqa: F811
    with (
        mock.patch.object(Seeder, "_pay", side_effect=RuntimeError("checkout failed")),
        pytest.raises(RuntimeError, match="checkout failed"),
    ):
        seed()
    # The open task (no payment step) was seeded; the first paid one left nothing behind.
    assert not Booking.objects.exists()
    paid = next(scenario for scenario in SCENARIOS if scenario.then is not None)
    assert not Task.objects.filter(title=paid.title).exists()

    seed()

    for scenario in SCENARIOS:
        assert Task.objects.filter(title=scenario.title).count() == 1
        if scenario.then is not None:
            assert booking(scenario.key) is not None


def test_every_main_state_is_seeded(demo_payments: None) -> None:  # noqa: F811
    seed()
    statuses = sorted(Booking.objects.values_list("status", flat=True))

    assert statuses == sorted(
        [
            S.FUNDS_HELD,
            S.FUNDS_HELD,
            S.FUNDS_HELD,
            S.PAYMENT_REQUESTED,
            S.RELEASED,
            S.REFUNDED,
            S.CANCEL_REQUESTED,
        ]
    )
    open_task = Task.objects.get(title="Check on my mother's flat and water the plants")
    assert open_task.status == Task.Status.OPEN
    assert open_task.offers.filter(status=Offer.Status.SENT).count() == 2
    assert open_task.poster.locale == "de"
    contested = Booking.objects.get(status=S.CANCEL_REQUESTED)
    assert contested.arrival.no_show_contested_at is not None


def test_money_is_held_only_through_webhook_events(demo_payments: None) -> None:  # noqa: F811
    seed()

    for booking in Booking.objects.all():
        held = BookingEvent.objects.get(booking=booking, type="funds_held")
        assert held.actor_kind == ActorKind.STRIPE_WEBHOOK
        assert StripeEvent.objects.filter(pk=held.data["stripe_event"]).exists()
        assert booking.payment_intent_id.startswith("pi_demo")
    refunded = Booking.objects.get(status=S.REFUNDED)
    assert BookingEvent.objects.get(booking=refunded, type="refunded").actor_kind == (
        ActorKind.STRIPE_WEBHOOK
    )


def test_request_and_release_go_through_the_state_machine_as_each_side(
    demo_payments: None,  # noqa: F811
) -> None:
    seed()
    released = Booking.objects.get(status=S.RELEASED)

    requested = BookingEvent.objects.get(booking=released, type="payment_requested")
    release = BookingEvent.objects.get(booking=released, type="released")
    assert (requested.actor_kind, requested.actor_id) == (ActorKind.HELPER, released.helper_id)
    assert (release.actor_kind, release.actor_id) == (ActorKind.POSTER, released.poster_id)
    assert release.data["source"] == SEED_SOURCE
    assert Outbox.objects.filter(aggregate_id=str(released.pk)).exists()


def test_every_seeded_booking_is_marked_as_demo_data(demo_payments: None) -> None:  # noqa: F811
    seed()

    for booking in Booking.objects.all():
        assert BookingEvent.objects.filter(
            booking=booking, type="demo_seed", data__source=SEED_SOURCE
        ).exists()


def assert_obviously_fake(profile: HelperProfile) -> None:
    assert is_valid_oib(profile.oib)
    assert profile.oib.startswith(DEMO_OIB_PREFIX)
    assert profile.business_oib in ("", profile.oib)
    assert normalize_iban(profile.iban) == profile.iban
    # Bank code 0000000, then an account number of zeros and the counter.
    assert profile.iban.startswith("HR") and set(profile.iban[4:-2]) == {"0"}


def test_the_data_is_obviously_fake(demo_payments: None) -> None:  # noqa: F811
    seed()

    for user in User.objects.all():
        assert user.email.endswith(f"@{DOMAIN}")
        assert user.phone_number == ""
    profiles = HelperProfile.objects.all()
    assert profiles.count() == 3
    assert len({profile.oib for profile in profiles}) == 3
    for profile in profiles:
        assert_obviously_fake(profile)
        assert profile.address_street.startswith("Demo ulica")
    for task in Task.objects.all():
        assert task.address.startswith("Demo ulica")


def test_a_reset_gives_the_new_helpers_the_same_demo_oibs(demo_payments: None) -> None:  # noqa: F811
    seed()
    before = {p.user.email: p.oib for p in HelperProfile.objects.select_related("user")}

    seed("--reset")
    seed("--reset")

    live = HelperProfile.objects.filter(user__is_active=True).select_related("user")
    assert {p.user.email: p.oib for p in live} == before
    for profile in live:
        assert_obviously_fake(profile)
    # Retiring clears the old helpers' OIB, IBAN and VAT ID.
    for profile in HelperProfile.objects.filter(user__is_active=False):
        assert (profile.oib, profile.oib_digest, profile.iban, profile.vat_id) == ("", None, "", "")


def test_reset_retires_the_demo_users_and_seeds_a_fresh_set(demo_payments: None) -> None:  # noqa: F811
    seed()
    first = {key: booking(key).pk for key in ("funds-held", "on-my-way")}

    output = seed("--reset")

    assert all(f"retired  {demo.email}" in output for demo in USERS.values())
    assert User.objects.filter(is_active=True).count() == len(USERS)
    retired = User.objects.filter(is_active=False)
    assert retired.count() == len(USERS)
    assert all(user.email.startswith("retired-") for user in retired)
    assert all(user.email.endswith(f"@{DOMAIN}") for user in retired)
    # Nothing was deleted: the old bookings and their legal evidence are still there.
    assert Booking.objects.filter(pk__in=first.values()).count() == 2
    assert all(booking(key).pk != pk for key, pk in first.items())
    assert not Task.objects.filter(poster__is_active=False, status=Task.Status.OPEN).exists()


def test_reset_is_idempotent(demo_payments: None) -> None:  # noqa: F811
    seed("--reset")
    shape = sorted(Booking.objects.values_list("status", flat=True))

    seed("--reset")

    live = Booking.objects.filter(poster__is_active=True)
    assert sorted(live.values_list("status", flat=True)) == shape
    assert User.objects.filter(is_active=True).count() == len(USERS)


def test_reset_never_touches_anyone_else(demo_payments: None) -> None:  # noqa: F811
    # Same name on the plain example.com domain, and an unknown address on the demo domain.
    now = timezone.now()
    lookalike = User.objects.create_user(
        "klaus.beispiel@example.com",
        first_name="Klaus",
        adult_confirmed_at=now,
        phone_verified_at=now,
    )
    stranger = User.objects.create_user(
        f"someone@{DOMAIN}", first_name="Someone", adult_confirmed_at=now, phone_verified_at=now
    )
    # Their own data: an open task, and a paid booking with a helper of their own.
    task = open_task(lookalike)
    held = make_booking(Booking.Status.FUNDS_HELD, poster=stranger)
    helper = HelperProfile.objects.get(user=held.helper)
    helper_before = (helper.oib, helper.oib_digest, helper.iban, held.helper.is_active)
    held_task_status = held.task.status
    seed()

    seed("--reset")

    for user, email in ((lookalike, "klaus.beispiel@example.com"), (stranger, f"someone@{DOMAIN}")):
        user.refresh_from_db()
        assert (user.email, user.is_active, user.first_name != "Retired") == (email, True, True)
    task.refresh_from_db()
    assert task.status == Task.Status.OPEN
    held.refresh_from_db()
    assert held.status == Booking.Status.FUNDS_HELD
    helper.refresh_from_db()
    held.helper.refresh_from_db()
    assert (helper.oib, helper.oib_digest, helper.iban, held.helper.is_active) == helper_before
    assert Task.objects.get(pk=held.task_id).status == held_task_status


def test_reset_times_move_with_the_clock(demo_payments: None) -> None:  # noqa: F811
    real_now = timezone.now()
    seed("--reset")
    later = real_now + dt.timedelta(days=3)

    with mock.patch("django.utils.timezone.now", return_value=later):
        seed("--reset")

    # Each walkthrough step's window is open right after the reset.
    def hours_ahead(key: str) -> float:
        return (booking(key).slot_start - later).total_seconds() / 3600

    assert 0 < hours_ahead("on-my-way") <= 6  # "On my way" opens 6 h before the start
    assert 0 < hours_ahead("late-cancel") < 24  # a late cancel
    assert hours_ahead("funds-held") > 24  # a free cancel
    assert -3 < hours_ahead("no-show-contested") < 0  # the job started, no-show reported


def test_the_cancel_screens_show_a_free_and_a_late_cancel(demo_payments: None) -> None:  # noqa: F811
    seed("--reset")

    # Three days ahead: a full refund. Under 24 h ahead, a private helper: a late cancel.
    free = cancel.preview(booking("funds-held"))
    late = cancel.preview(booking("late-cancel"))
    assert free.is_full_refund
    assert not late.is_full_refund
