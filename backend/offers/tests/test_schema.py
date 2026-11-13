"""Offers over HTTP at /graphql: full objects back (rule d), who sees which offers (rule e),
the service-area re-check (rule j) and the rate limit (rule d)."""

from typing import Any

import pytest
from django.contrib.gis.geos import Point
from django.core.cache import caches
from django.test import Client

from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from helpers.models import HelperProfile
from helpers.tests.business import give_business_address
from offers import services
from offers.tests.factories import make_helper, open_task
from tasks.models import Task
from tasks.tests.factories import VIENNA, make_poster

pytestmark = pytest.mark.django_db

FIELDS = """
id taskId helperUserId helperIsTrader helperBusinessName helperBusinessOib
helperBusinessAddress status priceCents serviceFeeCents serviceFeeScheduleVersion
serviceFeePercent serviceFeeMinimumCents totalCents message
messageMasked expiresAt isMine createdAt updatedAt
"""
SEND = f"mutation($input: SendOfferInput!) {{ sendOffer(input: $input) {{ {FIELDS} }} }}"
UPDATE = f"""
mutation($id: ID!, $input: UpdateOfferInput!) {{
  updateOffer(id: $id, input: $input) {{ {FIELDS} }}
}}
"""
RENEW = f"mutation($id: ID!) {{ renewOffer(id: $id) {{ {FIELDS} }} }}"
WITHDRAW = f"mutation($id: ID!) {{ withdrawOffer(id: $id) {{ {FIELDS} }} }}"
TASK_OFFERS = f"query($taskId: ID!) {{ taskOffers(taskId: $taskId) {{ {FIELDS} }} }}"
MINE = f"{{ myOffers {{ {FIELDS} }} }}"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def send(client: Client, token: str | None, task: Task, **fields: Any) -> dict[str, Any]:
    variables = {"input": {"taskId": str(task.pk), "priceCents": 3_000, **fields}}
    return gql(client, SEND, variables, token=token)


def test_sending_returns_the_full_offer(client: Client) -> None:
    task = open_task()
    marko = make_helper()

    body = send(client, sign_in(marko).access_token, task, message="Call 091 234 5678")

    offer = body["data"]["sendOffer"]
    assert offer["id"]
    assert offer["taskId"] == str(task.pk)
    assert offer["helperUserId"] == str(marko.pk)
    assert (offer["status"], offer["priceCents"], offer["isMine"]) == ("SENT", 3_000, True)
    assert offer["helperIsTrader"] is False
    # The fee checkout will charge on top, from the same rule (12%, half up).
    assert (offer["serviceFeeCents"], offer["totalCents"]) == (360, 3_360)
    # The rule for the checkout's fee notice, from the same price list (BEAV-191).
    assert (offer["serviceFeePercent"], offer["serviceFeeMinimumCents"]) == (12, 250)
    assert offer["serviceFeeScheduleVersion"] == "1"
    assert offer["messageMasked"] is True
    assert "5678" not in offer["message"]


def test_signed_out_and_unverified_users_cannot_send(client: Client) -> None:
    task = open_task()
    assert error_codes(send(client, None, task)) == ["UNAUTHENTICATED"]
    unverified = sign_in(make_user("ivo")).access_token
    assert error_codes(send(client, unverified, task)) == ["PHONE_NOT_VERIFIED"]
    not_a_helper = sign_in(make_poster("luka")).access_token
    assert error_codes(send(client, not_a_helper, task)) == ["HELPER_PROFILE_INCOMPLETE"]


def test_offers_on_a_task_moved_outside_the_area_get_out_of_service_area(
    client: Client,
) -> None:
    task = open_task()
    Task.objects.filter(pk=task.pk).update(location=Point(VIENNA[1], VIENNA[0], srid=4326))

    body = send(client, sign_in(make_helper()).access_token, task)

    assert error_codes(body) == ["OUT_OF_SERVICE_AREA"]


def test_a_price_below_ten_euros_is_refused(client: Client) -> None:
    body = send(client, sign_in(make_helper()).access_token, open_task(), priceCents=900)
    assert error_codes(body) == ["OFFER_PRICE_BELOW_MINIMUM"]


def test_update_renew_and_withdraw_return_the_full_offer(client: Client) -> None:
    marko = make_helper()
    token = sign_in(marko).access_token
    offer_id = send(client, token, open_task())["data"]["sendOffer"]["id"]

    updated = gql(client, UPDATE, {"id": offer_id, "input": {"priceCents": 2_800}}, token=token)
    renewed = gql(client, RENEW, {"id": offer_id}, token=token)
    withdrawn = gql(client, WITHDRAW, {"id": offer_id}, token=token)

    assert updated["data"]["updateOffer"]["priceCents"] == 2_800
    assert renewed["data"]["renewOffer"]["id"] == offer_id
    assert withdrawn["data"]["withdrawOffer"]["status"] == "WITHDRAWN"
    again = gql(client, WITHDRAW, {"id": offer_id}, token=token)
    assert error_codes(again) == ["OFFER_NOT_EDITABLE"]


def test_another_helper_cannot_touch_an_offer(client: Client) -> None:
    offer = services.send_offer(make_helper(), str(open_task().pk), 3_000)
    ivan = sign_in(make_helper("ivan")).access_token

    for query in (RENEW, WITHDRAW):
        assert error_codes(gql(client, query, {"id": str(offer.pk)}, token=ivan)) == [
            "OFFER_NOT_FOUND"
        ]


def test_the_poster_sees_every_offer_on_their_task(client: Client) -> None:
    task = open_task()
    marko = make_helper()
    first = services.send_offer(marko, str(task.pk), 3_000)
    services.withdraw_offer(marko, str(first.pk))
    second = services.send_offer(make_helper("ivan"), str(task.pk), 2_500)

    body = gql(
        client, TASK_OFFERS, {"taskId": str(task.pk)}, token=sign_in(task.poster).access_token
    )

    offers = body["data"]["taskOffers"]
    assert [o["id"] for o in offers] == [str(second.pk), str(first.pk)]
    assert [o["status"] for o in offers] == ["SENT", "WITHDRAWN"]
    # The helper-only field is null for the poster.
    assert all(o["messageMasked"] is None and o["isMine"] is False for o in offers)


def test_a_trader_helpers_offer_names_its_business_and_registered_address(
    client: Client,
) -> None:
    """Hard rule e as updated by BEAV-192 (CRD Art. 6(1)(b)-(c), ZZP čl. 61(1))."""
    task = open_task()
    marko = make_helper()
    profile = HelperProfile.objects.get(user=marko)
    HelperProfile.objects.filter(user=marko).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.OBRT,
        business_name="Vrtlarski obrt Marko",
        business_oib=profile.oib,
    )
    give_business_address(marko)
    services.send_offer(marko, str(task.pk), 3_000)

    body = gql(
        client, TASK_OFFERS, {"taskId": str(task.pk)}, token=sign_in(task.poster).access_token
    )

    [offer] = body["data"]["taskOffers"]
    assert offer["helperIsTrader"] is True
    assert (offer["helperBusinessName"], offer["helperBusinessOib"]) == (
        "Vrtlarski obrt Marko",
        profile.oib,
    )
    assert offer["helperBusinessAddress"] == "Obala Stjepana Radića 12, 20000 Dubrovnik, HR"
    # The registered address only, never the home address, phone or IBAN.
    for private in ("Put Republike", "0000000160", "Horvat"):
        assert private not in str(body)

    # Only the task's poster: the helper's own view of the offer leaves it out too, since it
    # is often the obrt owner's home (user decision 2026-11-07).
    mine = gql(client, MINE, token=sign_in(marko).access_token)["data"]["myOffers"]
    assert mine[0]["helperBusinessName"] == "Vrtlarski obrt Marko"
    assert mine[0]["helperBusinessAddress"] is None
    # Anyone else gets no offer at all, signed in or not.
    other = sign_in(make_helper("ivan")).access_token
    for token in (other, None):
        refused = gql(client, TASK_OFFERS, {"taskId": str(task.pk)}, token=token)
        assert "Obala" not in str(refused)
        assert refused["data"] is None or refused["data"]["taskOffers"] is None


def test_a_private_helpers_offer_has_no_business_identity_or_address(client: Client) -> None:
    task = open_task()
    marko = make_helper()
    profile = HelperProfile.objects.get(user=marko)
    services.send_offer(marko, str(task.pk), 3_000)

    body = gql(
        client, TASK_OFFERS, {"taskId": str(task.pk)}, token=sign_in(task.poster).access_token
    )

    [offer] = body["data"]["taskOffers"]
    assert offer["helperIsTrader"] is False
    assert (
        offer["helperBusinessName"],
        offer["helperBusinessOib"],
        offer["helperBusinessAddress"],
    ) == (None, None, None)
    for private in (profile.oib, "Put Republike", "20000", "Horvat", "0000000160"):
        assert private not in str(body)


def test_a_company_that_lost_its_verification_shows_no_business_identity(
    client: Client,
) -> None:
    task = open_task()
    marko = make_helper()
    services.send_offer(marko, str(task.pk), 3_000)
    # Renamed after sending: unverified again, so nothing unchecked is shown.
    HelperProfile.objects.filter(user=marko).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.COMPANY,
        business_name="Unchecked d.o.o.",
        business_oib="00000000948",
    )
    give_business_address(marko)

    body = gql(
        client, TASK_OFFERS, {"taskId": str(task.pk)}, token=sign_in(task.poster).access_token
    )

    [offer] = body["data"]["taskOffers"]
    assert offer["helperIsTrader"] is True
    assert offer["helperBusinessName"] is None
    assert offer["helperBusinessAddress"] is None
    assert "Unchecked" not in str(body)


def test_nobody_else_sees_the_offers_on_a_task(client: Client) -> None:
    task = open_task()
    marko = make_helper()
    services.send_offer(marko, str(task.pk), 3_000)
    ivan = make_helper("ivan")

    for token in (sign_in(ivan).access_token, sign_in(marko).access_token):
        body = gql(client, TASK_OFFERS, {"taskId": str(task.pk)}, token=token)
        assert error_codes(body) == ["TASK_NOT_FOUND"]
    assert error_codes(gql(client, TASK_OFFERS, {"taskId": str(task.pk)})) == ["UNAUTHENTICATED"]


def test_my_offers_lists_only_the_helpers_own(client: Client) -> None:
    task = open_task()
    marko = make_helper()
    mine = services.send_offer(marko, str(task.pk), 3_000)
    services.send_offer(make_helper("ivan"), str(task.pk), 2_500)

    body = gql(client, MINE, token=sign_in(marko).access_token)

    assert [o["id"] for o in body["data"]["myOffers"]] == [str(mine.pk)]


def test_sending_is_rate_limited(client: Client, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"sendOffer": 2}
    token = sign_in(make_helper()).access_token
    send(client, token, open_task())
    send(client, token, open_task())
    assert error_codes(send(client, token, open_task())) == ["RATE_LIMITED"]
