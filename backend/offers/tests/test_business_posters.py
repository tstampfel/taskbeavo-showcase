"""Business posters book only registered helpers (BEAV-195; decision A4, guardrail 3):
the poster's business OIB, sendOffer and acceptOffer refusing private helpers on the
server, hidden offers, and the withdrawal of open ones with a neutral reason (rule k)."""

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from django.core import mail
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts import business
from accounts import gdpr as accounts_gdpr
from accounts.models import PosterBusiness, PosterBusinessEvent, User
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from bookings import state_machine
from bookings.models import Booking
from bookings.tests.factories import book
from core import outbox
from core.errors import ApiError
from core.models import ActorKind, Outbox
from helpers.models import HelperProfile
from helpers.tests.business import give_business_address
from notifications.delivery import DeliveryResult, deliver
from notifications.models import Delivery
from offers import gdpr as offers_gdpr
from offers import services
from offers.models import Offer, OfferEvent
from offers.tests.factories import COMPANY_OIB, make_helper, open_task
from tasks.models import Task

pytestmark = pytest.mark.django_db

REASON = Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def code_of(call: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ApiError) as raised:
        call(*args, **kwargs)
    return raised.value.code.name


def make_trader(name: str = "tomo") -> User:
    """A helper registered as an obrt, with its business address: can send offers."""
    user = make_helper(name)
    profile = HelperProfile.objects.get(user=user)
    HelperProfile.objects.filter(user=user).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.OBRT,
        business_name=f"Obrt {name}",
        business_oib=profile.oib,
    )
    give_business_address(user)
    return user


def become_business(poster: User) -> PosterBusiness:
    return business.set_poster_business(
        poster, kind=PosterBusiness.Kind.COMPANY, name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )


# --- the poster's business ------------------------------------------------------------


def test_a_poster_becomes_a_business_poster_with_a_valid_oib() -> None:
    task = open_task()
    assert not business.is_business_poster(task.poster)

    become_business(task.poster)

    assert business.is_business_poster(task.poster)
    assert business.is_business_poster(task.poster_id)


def test_the_oib_check_digit_is_validated() -> None:
    poster = open_task().poster
    bad = COMPANY_OIB[:-1] + str((int(COMPANY_OIB[-1]) + 1) % 10)

    assert (
        code_of(business.set_poster_business, poster, kind="COMPANY", name="X d.o.o.", oib=bad)
        == "INVALID_OIB"
    )
    assert not business.is_business_poster(poster)


def test_a_company_oib_cannot_be_a_persons_oib_and_the_code_does_not_say_why() -> None:
    """The same code as a bad check digit (review R1-6): the answer never tells a poster
    whether an OIB belongs to a TaskBeavo helper."""
    poster = open_task().poster
    helper_oib = HelperProfile.objects.get(user=make_helper()).oib

    assert (
        code_of(
            business.set_poster_business, poster, kind="COMPANY", name="X d.o.o.", oib=helper_oib
        )
        == "INVALID_OIB"
    )


def test_an_obrt_oib_is_the_owners_own_when_known() -> None:
    owner = make_helper()
    own = HelperProfile.objects.get(user=owner).oib

    assert (
        code_of(business.set_poster_business, owner, kind="OBRT", name="Obrt", oib=COMPANY_OIB)
        == "INVALID_BUSINESS_OIB"
    )
    assert business.set_poster_business(owner, kind="OBRT", name="Obrt", oib=own).kind == "OBRT"


def test_kind_and_name_are_checked() -> None:
    poster = open_task().poster

    for kind, name in (("PERSON", "X"), ("COMPANY", " "), ("COMPANY", "x" * 201)):
        assert (
            code_of(business.set_poster_business, poster, kind=kind, name=name, oib=COMPANY_OIB)
            == "INVALID_POSTER_BUSINESS"
        )


def test_clearing_the_business_lets_private_helpers_offer_again() -> None:
    task = open_task()
    become_business(task.poster)

    business.clear_poster_business(task.poster)

    assert not business.is_business_poster(task.poster)
    assert services.send_offer(make_helper(), str(task.pk), 3_000).status == Offer.Status.SENT


# --- sendOffer --------------------------------------------------------------------------


def test_a_private_helper_cannot_offer_on_a_business_posters_task() -> None:
    task = open_task()
    become_business(task.poster)

    assert (
        code_of(services.send_offer, make_helper(), str(task.pk), 3_000)
        == "BUSINESS_POSTER_REGISTERED_ONLY"
    )
    assert not Offer.objects.filter(task=task).exists()


@pytest.mark.parametrize("status", [HelperProfile.Status.PENSIONER, HelperProfile.Status.STUDENT])
def test_pensioners_and_students_are_private_helpers(status: HelperProfile.Status) -> None:
    task = open_task()
    become_business(task.poster)
    helper = make_helper()
    HelperProfile.objects.filter(user=helper).update(declared_status=status)

    assert (
        code_of(services.send_offer, helper, str(task.pk), 3_000)
        == "BUSINESS_POSTER_REGISTERED_ONLY"
    )


def test_a_registered_helper_can_offer_on_a_business_posters_task() -> None:
    task = open_task()
    become_business(task.poster)

    offer = services.send_offer(make_trader(), str(task.pk), 3_000)

    assert offer.status == Offer.Status.SENT
    assert [o.pk for o in services.offers_on_task(task.poster, str(task.pk))] == [offer.pk]


def test_a_private_helper_cannot_renew_or_change_an_offer_once_the_poster_is_a_business() -> None:
    task = open_task()
    helper = make_helper()
    offer = services.send_offer(helper, str(task.pk), 3_000)
    Offer.objects.filter(pk=offer.pk).update(status=Offer.Status.EXPIRED)
    PosterBusiness.objects.create(
        user=task.poster, kind="COMPANY", name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )

    assert code_of(services.renew_offer, helper, str(offer.pk)) == "BUSINESS_POSTER_REGISTERED_ONLY"
    Offer.objects.filter(pk=offer.pk).update(status=Offer.Status.SENT)
    assert (
        code_of(services.update_offer, helper, str(offer.pk), price_cents=2_000)
        == "BUSINESS_POSTER_REGISTERED_ONLY"
    )


# --- becoming a business withdraws private offers ---------------------------------------


def test_becoming_a_business_withdraws_private_helpers_open_offers_with_a_neutral_reason() -> None:
    task = open_task()
    private = services.send_offer(make_helper("marko"), str(task.pk), 3_000)
    trader = services.send_offer(make_trader(), str(task.pk), 3_200)
    already = services.send_offer(make_helper("ivan"), str(task.pk), 2_800)
    services.withdraw_offer(already.helper, str(already.pk))

    become_business(task.poster)

    for offer in (private, trader, already):
        offer.refresh_from_db()
    assert (private.status, private.withdrawn_reason) == (Offer.Status.WITHDRAWN, REASON)
    assert private.closed_at is not None
    assert trader.status == Offer.Status.SENT
    # The helper's own withdrawal keeps its own (blank) reason.
    assert (already.status, already.withdrawn_reason) == (Offer.Status.WITHDRAWN, "")
    event = OfferEvent.objects.filter(offer=private).latest("id")
    assert (event.type, event.actor_kind, event.actor_id) == ("withdrawn", ActorKind.SYSTEM, None)
    assert event.data["reason"] == REASON
    [row] = Outbox.objects.filter(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC)
    assert row.aggregate_id == str(private.pk)
    # The poster's "a helper withdrew" notice isn't written: the poster caused this.
    assert not Outbox.objects.filter(
        topic=services.WITHDRAWN_TOPIC, aggregate_id=str(private.pk)
    ).exists()


def test_declined_and_expired_offers_stay_as_they_are_and_nobody_is_told() -> None:
    """Only live offers on open tasks are withdrawn (review R1-2): a helper whose offer was
    declined or expired long ago hears nothing new about it."""
    booked = open_task()
    chosen = services.send_offer(make_trader(), str(booked.pk), 3_000)
    declined = services.send_offer(make_helper("marko"), str(booked.pk), 2_800)
    book(booked.poster, chosen)
    done = open_task(booked.poster)
    expired = services.send_offer(make_helper("ivan"), str(done.pk), 2_500)
    Offer.objects.filter(pk=expired.pk).update(status=Offer.Status.EXPIRED)
    Task.objects.filter(pk=done.pk).update(status=Task.Status.CLOSED)

    become_business(booked.poster)

    declined.refresh_from_db()
    expired.refresh_from_db()
    assert (declined.status, declined.withdrawn_reason) == (Offer.Status.DECLINED, "")
    assert (expired.status, expired.withdrawn_reason) == (Offer.Status.EXPIRED, "")
    assert not Outbox.objects.filter(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC).exists()


def test_a_live_offer_on_a_closed_task_is_left_alone() -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    Task.objects.filter(pk=task.pk).update(status=Task.Status.CLOSED)

    become_business(task.poster)

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT
    assert not Outbox.objects.filter(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC).exists()


def test_private_offers_are_hidden_from_a_business_poster() -> None:
    task = open_task()
    private = services.send_offer(make_helper(), str(task.pk), 3_000)
    trader = services.send_offer(make_trader(), str(task.pk), 3_200)
    # A private helper's offer sent before; withdrawing would normally hide nothing.
    PosterBusiness.objects.create(
        user=task.poster, kind="COMPANY", name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )

    shown = services.offers_on_task(task.poster, str(task.pk))

    assert [o.pk for o in shown] == [trader.pk]
    assert private.pk in [o.pk for o in services.my_offers(private.helper)]


def test_a_private_helpers_open_checkout_blocks_becoming_a_business() -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    booking = book(task.poster, offer)
    assert booking.status == Booking.Status.AWAITING_PAYMENT

    assert code_of(become_business, task.poster) == "POSTER_BUSINESS_CHECKOUT_OPEN"
    assert not business.is_business_poster(task.poster)


def test_a_registered_helpers_open_checkout_does_not_block_it() -> None:
    task = open_task()
    book(task.poster, services.send_offer(make_trader(), str(task.pk), 3_000))

    become_business(task.poster)

    assert business.is_business_poster(task.poster)


# --- acceptOffer ------------------------------------------------------------------------


def test_accepting_a_private_helpers_offer_is_refused_on_the_server() -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    # The offer is still live: say it slipped past the withdrawal.
    PosterBusiness.objects.create(
        user=task.poster, kind="COMPANY", name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )

    assert code_of(book, task.poster, offer) == "BUSINESS_POSTER_REGISTERED_ONLY"
    assert not Booking.objects.filter(task=task).exists()
    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT


def test_a_helper_who_stopped_being_registered_cannot_be_booked() -> None:
    task = open_task()
    become_business(task.poster)
    trader = make_trader()
    offer = services.send_offer(trader, str(task.pk), 3_000)
    HelperProfile.objects.filter(user=trader).update(
        declared_status=HelperProfile.Status.PRIVATE_INDIVIDUAL
    )

    assert code_of(book, task.poster, offer) == "BUSINESS_POSTER_REGISTERED_ONLY"


def test_a_business_poster_books_a_registered_helper() -> None:
    task = open_task()
    become_business(task.poster)
    offer = services.send_offer(make_trader(), str(task.pk), 3_000)

    assert book(task.poster, offer).status == Booking.Status.AWAITING_PAYMENT


# --- telling the helper -----------------------------------------------------------------


def test_the_helper_hears_why_by_push_and_the_poster_hears_nothing() -> None:
    task = open_task()
    offer = services.send_offer(make_helper(), str(task.pk), 3_000)
    become_business(task.poster)
    row = Outbox.objects.get(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC)

    outbox.publish_row(row.pk)

    deliveries = Delivery.objects.filter(outbox=row)
    assert {(d.user_id, d.kind) for d in deliveries} == {
        (offer.helper_id, "offer.withdrawn_registered_only")
    }


SUBJECTS = {
    "hr": "Vaša ponuda je povučena",
    "en": "Your offer was withdrawn",
    "de": "Ihr Angebot wurde zurückgezogen",
    "fr": "Votre offre a été retirée",
}


@pytest.mark.parametrize("locale", sorted(SUBJECTS))
def test_the_helpers_notice_is_in_their_language_and_names_nobody(locale: str) -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    helper = make_helper()
    User.objects.filter(pk=helper.pk).update(locale=locale)
    services.send_offer(helper, str(task.pk), 4_321, "Gladly, tomorrow at nine")
    become_business(task.poster)
    row = Outbox.objects.get(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC)
    outbox.publish_row(row.pk)
    # No push device, so it goes by email.
    email = Delivery.objects.get(outbox=row, channel="email")

    assert deliver(email.pk)[0] == DeliveryResult.SENT

    (message,) = mail.outbox
    assert SUBJECTS[locale] in message.subject
    text = message.subject + message.body
    for detail in ("Gladly", "43,21", "Villa", COMPANY_OIB, task.poster.first_name):
        assert detail not in text


def test_the_notice_is_still_sent_after_the_task_closed() -> None:
    task = open_task()
    services.send_offer(make_helper(), str(task.pk), 3_000)
    become_business(task.poster)
    Task.objects.filter(pk=task.pk).update(status=Task.Status.CLOSED)

    outbox.publish_row(Outbox.objects.get(topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC).pk)

    assert Delivery.objects.filter(kind="offer.withdrawn_registered_only").exists()


# --- GraphQL ----------------------------------------------------------------------------

SET = """
mutation($input: PosterBusinessInput!) {
  setPosterBusiness(input: $input) { id posterBusiness { id kind name oibMasked } }
}
"""
CLEAR = "mutation { clearPosterBusiness { id posterBusiness { id } } }"
SEND = """
mutation($input: SendOfferInput!) { sendOffer(input: $input) { id status } }
"""
MINE = "{ myOffers { id status withdrawnReason } }"
TASK_OFFERS = "query($taskId: ID!) { taskOffers(taskId: $taskId) { id withdrawnReason } }"


def test_set_poster_business_over_graphql(client: Client) -> None:
    task = open_task()
    token = sign_in(task.poster).access_token
    variables = {"input": {"kind": "COMPANY", "name": "Villa Beavo d.o.o.", "oib": COMPANY_OIB}}

    body = gql(client, SET, variables, token=token)

    shown = body["data"]["setPosterBusiness"]["posterBusiness"]
    assert (shown["kind"], shown["name"]) == ("COMPANY", "Villa Beavo d.o.o.")
    assert shown["oibMasked"].endswith(COMPANY_OIB[-4:])
    assert COMPANY_OIB not in str(body)
    cleared = gql(client, CLEAR, token=token)
    assert cleared["data"]["clearPosterBusiness"]["posterBusiness"] is None


def test_set_poster_business_needs_a_signed_in_user(client: Client) -> None:
    variables = {"input": {"kind": "COMPANY", "name": "X d.o.o.", "oib": COMPANY_OIB}}

    assert error_codes(gql(client, SET, variables)) == ["UNAUTHENTICATED"]


def test_send_offer_over_graphql_returns_the_code(client: Client) -> None:
    task = open_task()
    become_business(task.poster)
    variables = {"input": {"taskId": str(task.pk), "priceCents": 3_000}}

    body = gql(client, SEND, variables, token=sign_in(make_helper()).access_token)

    assert error_codes(body) == ["BUSINESS_POSTER_REGISTERED_ONLY"]


def test_only_the_helper_sees_the_withdrawn_reason(client: Client) -> None:
    task = open_task()
    helper = make_helper()
    offer = services.send_offer(helper, str(task.pk), 3_000)
    become_business(task.poster)

    mine = gql(client, MINE, token=sign_in(helper).access_token)["data"]["myOffers"]
    assert mine == [{"id": str(offer.pk), "status": "WITHDRAWN", "withdrawnReason": REASON.value}]
    # Hidden from the business poster altogether.
    variables = {"taskId": str(task.pk)}
    shown = gql(client, TASK_OFFERS, variables, token=sign_in(task.poster).access_token)
    assert shown["data"]["taskOffers"] == []


# --- audit trail and status at acceptance (manager decision) ---------------------------


def test_add_remove_add_leaves_three_audit_events_without_the_oib() -> None:
    poster = open_task().poster

    become_business(poster)
    business.clear_poster_business(poster)
    become_business(poster)

    events = list(PosterBusinessEvent.objects.filter(poster=poster).order_by("id"))
    assert [(e.type, e.data["status_before"], e.data["status_after"]) for e in events] == [
        ("set", "PRIVATE", "BUSINESS"),
        ("cleared", "BUSINESS", "PRIVATE"),
        ("set", "PRIVATE", "BUSINESS"),
    ]
    assert {(e.actor_kind, e.actor_id) for e in events} == {(ActorKind.POSTER, poster.pk)}
    assert all(COMPANY_OIB not in str(e.data) and "Villa" not in str(e.data) for e in events)


def test_changing_the_business_is_audited_and_clearing_nothing_is_not() -> None:
    poster = open_task().poster
    business.clear_poster_business(poster)
    become_business(poster)

    business.set_poster_business(poster, kind="COMPANY", name="Beavo j.d.o.o.", oib=COMPANY_OIB)

    assert list(
        PosterBusinessEvent.objects.filter(poster=poster).order_by("id").values_list("type")
    ) == [("set",), ("changed",)]


def test_acceptance_uses_and_records_the_status_at_that_moment() -> None:
    task = open_task()
    private = services.send_offer(make_helper(), str(task.pk), 3_000)
    # Accepted while private: allowed, and the booking says so for good.
    early = book(task.poster, private)
    assert early.poster_was_business is False

    second = open_task(task.poster)
    trader_offer = services.send_offer(make_trader(), str(second.pk), 3_000)
    late_private = services.send_offer(make_helper("ivan"), str(second.pk), 2_000)
    Booking.objects.filter(pk=early.pk).update(status=Booking.Status.FUNDS_HELD)
    become_business(task.poster)

    # The earlier booking keeps the status it was accepted under.
    early.refresh_from_db()
    assert early.poster_was_business is False
    late_private.refresh_from_db()
    assert late_private.status == Offer.Status.WITHDRAWN
    later = book(task.poster, trader_offer)
    assert later.poster_was_business is True

    # Removed again: a private helper can be booked, and the booking records PRIVATE.
    business.clear_poster_business(task.poster)
    third = open_task(task.poster)
    offer = services.send_offer(make_helper("luka"), str(third.pk), 3_000)
    assert book(task.poster, offer).poster_was_business is False


def test_staff_see_the_business_masked_and_its_audit_trail(client: Client) -> None:
    client.force_login(make_user("support", is_staff=True, is_superuser=True))
    poster = open_task().poster
    become_business(poster)
    business.clear_poster_business(poster)
    become_business(poster)

    page = client.get(f"/admin/accounts/user/{poster.pk}/change/").content.decode()

    assert "Villa Beavo d.o.o." in page
    assert COMPANY_OIB[-4:] in page
    assert COMPANY_OIB not in page
    assert page.count("status_before") == 3


def test_staff_see_on_the_booking_list_whether_the_poster_was_a_business(client: Client) -> None:
    client.force_login(make_user("support", is_staff=True, is_superuser=True))
    task = open_task()
    become_business(task.poster)
    book(task.poster, services.send_offer(make_trader(), str(task.pk), 3_000))

    page = client.get("/admin/bookings/booking/?poster_was_business__exact=1")

    assert page.status_code == 200
    assert page.context["cl"].result_count == 1


# --- review round 1 ---------------------------------------------------------------------


def test_the_poster_hears_nothing_about_a_helper_who_left_the_business_status() -> None:
    """Review R1-4: their offer is hidden from the business poster, so no push, email or
    live event about it reaches them (it stays unbookable: create_booking refuses it)."""
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    become_business(task.poster)
    trader = make_trader()
    offer = services.send_offer(trader, str(task.pk), 3_000)
    HelperProfile.objects.filter(user=trader).update(
        declared_status=HelperProfile.Status.PRIVATE_INDIVIDUAL
    )
    received = Outbox.objects.get(topic=services.RECEIVED_TOPIC, aggregate_id=str(offer.pk))
    now = timezone.now()
    Offer.objects.filter(pk=offer.pk).update(
        expires_at=now + dt.timedelta(hours=20), lifetime_started_at=now - dt.timedelta(days=6)
    )
    assert services.queue_expiry_reminders() == 1
    expiring = Outbox.objects.get(topic=services.EXPIRING_TOPIC, aggregate_id=str(offer.pk))
    services.withdraw_offer(trader, str(offer.pk))
    withdrawn = Outbox.objects.get(topic=services.WITHDRAWN_TOPIC, aggregate_id=str(offer.pk))

    for row in (received, expiring, withdrawn):
        outbox.publish_row(row.pk)

    assert not Delivery.objects.filter(user_id=task.poster_id).exists()


def test_the_poster_still_hears_about_a_registered_helpers_offer() -> None:
    task = open_task()
    become_business(task.poster)
    offer = services.send_offer(make_trader(), str(task.pk), 3_000)

    outbox.publish_row(
        Outbox.objects.get(topic=services.RECEIVED_TOPIC, aggregate_id=str(offer.pk)).pk
    )

    assert Delivery.objects.filter(user_id=task.poster_id, kind="offer.received").exists()


def test_the_business_and_its_audit_trail_are_in_the_owners_data_export() -> None:
    """Review R1-5: the owner gets their own OIB in full; withdrawn offers keep their reason."""
    task = open_task()
    helper = make_helper()
    services.send_offer(helper, str(task.pk), 3_000)
    assert accounts_gdpr.export_poster_business(task.poster) == {"business": None, "events": []}

    become_business(task.poster)

    exported = accounts_gdpr.export_poster_business(task.poster)
    assert exported["business"] is not None
    assert (exported["business"]["kind"], exported["business"]["oib"]) == ("COMPANY", COMPANY_OIB)
    assert [event["type"] for event in exported["events"]] == ["set"]
    [offer] = offers_gdpr.export_offers(helper)
    assert offer["withdrawn_reason"] == REASON


def test_set_poster_business_has_a_tight_rate_limit(client: Client) -> None:
    """Review R1-6: three tries a minute, so the OIB check can't be used to probe OIBs."""
    token = sign_in(open_task().poster).access_token
    variables = {"input": {"kind": "COMPANY", "name": "X d.o.o.", "oib": "00000000000"}}

    codes = [error_codes(gql(client, SET, variables, token=token)) for _ in range(4)]

    assert codes[:3] == [["INVALID_OIB"]] * 3
    assert codes[3] == ["RATE_LIMITED"]


def test_a_failed_registered_checkout_after_the_switch_withdraws_private_declined_offers() -> None:
    """Review R2-1: the poster accepted a registered helper (which declined a private one),
    became a business, then the checkout failed. The registered helper's offer reopens; the
    private helper's is withdrawn with the neutral reason, not reopened."""
    task = open_task()
    chosen = services.send_offer(make_trader(), str(task.pk), 3_000)
    declined = services.send_offer(make_helper(), str(task.pk), 2_800)
    booking = book(task.poster, chosen)
    become_business(task.poster)
    declined.refresh_from_db()
    assert declined.status == Offer.Status.DECLINED

    state_machine.transition(booking.pk, Booking.Status.PAYMENT_FAILED, actor_kind=ActorKind.SYSTEM)

    chosen.refresh_from_db()
    declined.refresh_from_db()
    assert chosen.status == Offer.Status.SENT
    assert (declined.status, declined.withdrawn_reason) == (Offer.Status.WITHDRAWN, REASON)
    assert not Outbox.objects.filter(
        topic=state_machine.OFFER_REOPENED_TOPIC, aggregate_id=str(declined.pk)
    ).exists()
    assert Outbox.objects.filter(
        topic=services.WITHDRAWN_REGISTERED_ONLY_TOPIC, aggregate_id=str(declined.pk)
    ).exists()


def test_a_failed_checkout_of_a_private_poster_still_reopens_every_offer() -> None:
    task = open_task()
    chosen = services.send_offer(make_trader(), str(task.pk), 3_000)
    declined = services.send_offer(make_helper(), str(task.pk), 2_800)
    booking = book(task.poster, chosen)

    state_machine.transition(booking.pk, Booking.Status.PAYMENT_FAILED, actor_kind=ActorKind.SYSTEM)

    declined.refresh_from_db()
    assert (declined.status, declined.withdrawn_reason) == (Offer.Status.SENT, "")
