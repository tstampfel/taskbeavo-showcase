"""Push token registration and notification preferences over HTTP at /graphql."""

import pytest
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts.models import RefreshToken
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from notifications.models import NotificationPreference, PushToken

pytestmark = pytest.mark.django_db

TOKEN = "ExponentPushToken[abcdefghijklmnop1234]"
PREFERENCES = "{ notificationPreferences { id category channel enabled locked } }"
SET = """
mutation($category: NotificationCategory!, $channel: NotificationChannel!, $enabled: Boolean!) {
  setNotificationPreference(category: $category, channel: $channel, enabled: $enabled) {
    id category channel enabled locked
  }
}
"""
REGISTER = """
mutation($token: String!) {
  registerPushToken(token: $token, platform: IOS) { id platform }
}
"""
UNREGISTER = "mutation($token: String!) { unregisterPushToken(token: $token) }"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Every test starts with empty mutation rate-limit counters."""
    caches["graphql_rate_limit"].clear()


def test_signed_out_callers_are_refused(client: Client) -> None:
    assert error_codes(gql(client, PREFERENCES)) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, REGISTER, {"token": TOKEN})) == ["UNAUTHENTICATED"]


def test_preferences_default_to_on_with_booking_and_payment_locked(client: Client) -> None:
    token = sign_in(make_user()).access_token

    found = gql(client, PREFERENCES, token=token)["data"]["notificationPreferences"]

    assert len(found) == 8
    assert all(preference["enabled"] for preference in found)
    locked = {p["category"] for p in found if p["locked"]}
    assert locked == {"BOOKING", "PAYMENT"}
    assert {"id": "OFFERS:EMAIL", "category": "OFFERS", "channel": "EMAIL"}.items() <= next(
        p for p in found if p["id"] == "OFFERS:EMAIL"
    ).items()


def test_switching_a_category_off_is_saved_and_returned(client: Client) -> None:
    user = make_user()
    token = sign_in(user).access_token

    body = gql(
        client, SET, {"category": "MESSAGES", "channel": "PUSH", "enabled": False}, token=token
    )

    assert body["data"]["setNotificationPreference"] == {
        "id": "MESSAGES:PUSH",
        "category": "MESSAGES",
        "channel": "PUSH",
        "enabled": False,
        "locked": False,
    }
    assert NotificationPreference.objects.get(user=user).enabled is False
    found = gql(client, PREFERENCES, token=token)["data"]["notificationPreferences"]
    assert next(p for p in found if p["id"] == "MESSAGES:PUSH")["enabled"] is False


@pytest.mark.parametrize("category", ["BOOKING", "PAYMENT"])
def test_booking_and_payment_messages_cant_be_switched_off(client: Client, category: str) -> None:
    token = sign_in(make_user()).access_token

    variables = {"category": category, "channel": "EMAIL", "enabled": False}
    body = gql(client, SET, variables, token=token)

    assert error_codes(body) == ["NOTIFICATION_PREFERENCE_LOCKED"]
    assert not NotificationPreference.objects.exists()


def test_register_push_token_ties_it_to_the_session(client: Client) -> None:
    user = make_user()
    issued = sign_in(user)

    body = gql(client, REGISTER, {"token": TOKEN}, token=issued.access_token)

    device = PushToken.objects.get()
    assert body["data"]["registerPushToken"] == {"id": str(device.pk), "platform": "IOS"}
    assert (device.user, device.session, device.token) == (user, issued.session, TOKEN)


def test_a_token_live_for_another_account_cant_be_taken_over(client: Client) -> None:
    ana = make_user("ana")
    gql(client, REGISTER, {"token": TOKEN}, token=sign_in(ana).access_token)

    body = gql(client, REGISTER, {"token": TOKEN}, token=sign_in(make_user("luc")).access_token)

    assert error_codes(body) == ["INVALID_PUSH_TOKEN"]
    assert PushToken.objects.get().user == ana


def test_a_token_moves_once_the_other_accounts_session_has_ended(client: Client) -> None:
    ana_session = sign_in(make_user("ana"))
    gql(client, REGISTER, {"token": TOKEN}, token=ana_session.access_token)
    # Expired rather than signed out, so the token is still there, but dead.
    RefreshToken.objects.filter(session=ana_session.session).update(expires_at=timezone.now())
    luc = make_user("luc")

    body = gql(client, REGISTER, {"token": TOKEN}, token=sign_in(luc).access_token)

    assert "errors" not in body
    assert PushToken.objects.get().user == luc


def test_the_same_account_can_re_register_from_a_new_session(client: Client) -> None:
    ana = make_user("ana")
    gql(client, REGISTER, {"token": TOKEN}, token=sign_in(ana).access_token)
    second = sign_in(ana)

    assert "errors" not in gql(client, REGISTER, {"token": TOKEN}, token=second.access_token)
    assert PushToken.objects.get().session == second.session


@pytest.mark.parametrize("bad", ["", "abc", "ExponentPushToken[]", "FCM:abcdefghijklmnop"])
def test_invalid_tokens_are_refused(client: Client, bad: str) -> None:
    token = sign_in(make_user()).access_token
    assert error_codes(gql(client, REGISTER, {"token": bad}, token=token)) == ["INVALID_PUSH_TOKEN"]
    assert not PushToken.objects.exists()


def test_unregister_removes_only_the_callers_token(client: Client) -> None:
    ana_token = sign_in(make_user("ana")).access_token
    luc_token = sign_in(make_user("luc")).access_token
    gql(client, REGISTER, {"token": TOKEN}, token=ana_token)

    assert gql(client, UNREGISTER, {"token": TOKEN}, token=luc_token)["data"] == {
        "unregisterPushToken": False
    }
    assert PushToken.objects.exists()
    assert gql(client, UNREGISTER, {"token": TOKEN}, token=ana_token)["data"] == {
        "unregisterPushToken": True
    }
    assert not PushToken.objects.exists()
