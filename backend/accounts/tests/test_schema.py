"""The sign-in API end to end over HTTP at /graphql."""

import re
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core import mail
from django.test import Client

from accounts.models import ProviderIdentity, User
from accounts.tests.factories import TERMS_VERSION, access_token, make_user, sign_in
from accounts.tests.id_tokens import id_token
from config.ws_auth import resolve_connection_session
from legal.models import TermsAcceptance

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("current_terms")]

DEVICE = {"platform": "WEB", "name": "Firefox on Mac"}
SESSION_FIELDS = "id accessToken accessTokenExpiresAt refreshToken refreshTokenExpiresAt user { id email firstName lastName locale country timeZone }"  # noqa: E501
VERIFY = f"""
mutation Verify($email: String!, $code: String!, $device: DeviceInput!) {{
  verifyEmailCode(email: $email, code: $code, device: $device) {{
    __typename
    ... on AuthSession {{ {SESSION_FIELDS} }}
    ... on AccountDetailsRequired {{ id email ticket }}
  }}
}}
"""
COMPLETE = f"""
mutation Complete($details: AccountDetailsInput!, $device: DeviceInput!) {{
  completeAccountDetails(details: $details, device: $device) {{ {SESSION_FIELDS} }}
}}
"""


def gql(
    client: Client, query: str, variables: dict[str, Any] | None = None, token: str | None = None
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(
        "/graphql",
        {"query": query, "variables": variables or {}},
        content_type="application/json",
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def error_codes(body: dict[str, Any]) -> list[str]:
    return [error["extensions"]["code"] for error in body.get("errors", [])]


def request_code(client: Client, email: str) -> str:
    body = gql(
        client,
        "mutation($email: String!) { requestEmailCode(email: $email, locale: DE) }",
        {"email": email},
    )
    assert body == {"data": {"requestEmailCode": True}}
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match is not None
    return match.group(1)


def details(ticket: str, **overrides: Any) -> dict[str, Any]:
    return {
        "ticket": ticket,
        "firstName": "Ana",
        "lastName": "Kovač",
        "isAdult": True,
        "locale": "DE",
        "country": "DE",
        "timeZone": "Europe/Berlin",
        "acceptedTermsVersion": TERMS_VERSION,
        **overrides,
    }


def verify(client: Client, email: str, code: str) -> dict[str, Any]:
    return gql(client, VERIFY, {"email": email, "code": code, "device": DEVICE})


def test_sign_up_flow_creates_the_user_only_at_the_details_step(client: Client) -> None:
    email = "ana.kovac@example.org"
    code = request_code(client, email)
    assert mail.outbox[-1].extra_headers["Content-Language"] == "de"

    verified = verify(client, email, code)["data"]["verifyEmailCode"]
    assert verified["__typename"] == "AccountDetailsRequired"
    assert verified["email"] == email
    assert verified["id"]
    assert not User.objects.exists()

    body = gql(client, COMPLETE, {"details": details(verified["ticket"]), "device": DEVICE})
    session = body["data"]["completeAccountDetails"]
    assert session["id"]
    assert session["user"] == {
        "id": str(User.objects.get().pk),
        "email": email,
        "firstName": "Ana",
        "lastName": "Kovač",
        "locale": "DE",
        "country": "DE",
        "timeZone": "Europe/Berlin",
    }

    viewer = gql(client, "{ viewer { id email } }", token=session["accessToken"])
    assert viewer == {"data": {"viewer": {"id": session["user"]["id"], "email": email}}}


def test_existing_account_gets_a_session(client: Client) -> None:
    user = make_user("ana")

    result = verify(client, "ana@example.com", request_code(client, "ana@example.com"))

    session = result["data"]["verifyEmailCode"]
    assert session["__typename"] == "AuthSession"
    assert session["user"]["id"] == str(user.pk)


def test_request_code_answers_the_same_for_known_and_unknown_addresses(client: Client) -> None:
    make_user("ana")
    query = "mutation($email: String!) { requestEmailCode(email: $email, locale: EN) }"

    known = gql(client, query, {"email": "ana@example.com"})
    unknown = gql(client, query, {"email": "nobody@example.com"})

    assert known == unknown == {"data": {"requestEmailCode": True}}


def test_under_18_gets_underage(client: Client) -> None:
    email = "teen@example.com"
    ticket = verify(client, email, request_code(client, email))["data"]["verifyEmailCode"]["ticket"]

    body = gql(client, COMPLETE, {"details": details(ticket, isAdult=False), "device": DEVICE})

    assert error_codes(body) == ["UNDERAGE"]
    assert not User.objects.exists()


def test_wrong_code_gets_invalid_code(client: Client) -> None:
    code = request_code(client, "ana@example.com")
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    assert error_codes(verify(client, "ana@example.com", wrong)) == ["INVALID_CODE"]


def test_refresh_session_rotates_and_detects_reuse(client: Client, settings) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = -1
    issued = sign_in(make_user())
    query = (
        "mutation($t: String!) { refreshSession(refreshToken: $t) "
        "{ id accessToken refreshToken user { id } } }"
    )

    rotated = gql(client, query, {"t": issued.refresh_token})["data"]["refreshSession"]
    assert rotated["id"] == str(issued.session.pk)
    assert rotated["refreshToken"] != issued.refresh_token

    assert error_codes(gql(client, query, {"t": issued.refresh_token})) == ["SESSION_EXPIRED"]
    assert error_codes(gql(client, query, {"t": rotated["refreshToken"]})) == ["SESSION_EXPIRED"]


def test_sign_out_ends_the_session(client: Client) -> None:
    issued = sign_in(make_user())

    body = gql(
        client,
        "mutation($t: String!) { signOut(refreshToken: $t) }",
        {"t": issued.refresh_token},
    )

    assert body == {"data": {"signOut": True}}
    assert gql(client, "{ viewer { id } }", token=issued.access_token) == {"data": {"viewer": None}}


def test_viewer_is_null_when_signed_out(client: Client) -> None:
    assert gql(client, "{ viewer { id } }") == {"data": {"viewer": None}}
    assert gql(client, "{ viewer { id } }", token="garbage") == {"data": {"viewer": None}}


def test_web_socket_token_needs_a_signed_in_user(client: Client) -> None:
    body = gql(client, "mutation { webSocketToken }")

    assert error_codes(body) == ["UNAUTHENTICATED"]


def test_web_socket_token_opens_a_subscription_connection(client: Client) -> None:
    user = make_user()
    issued = sign_in(user)

    token = gql(client, "mutation { webSocketToken }", token=issued.access_token)["data"][
        "webSocketToken"
    ]

    signed_in = async_to_sync(resolve_connection_session)({"authToken": token})
    assert signed_in is not None
    assert signed_in == (user, issued.session)


def test_sign_in_mutations_are_rate_limited(client: Client, settings) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"requestEmailCode": 1}
    query = 'mutation { requestEmailCode(email: "ana@example.com", locale: EN) }'

    assert error_codes(gql(client, query)) == []
    assert error_codes(gql(client, query)) == ["RATE_LIMITED"]


SET_LOCALE = "mutation($locale: Locale!) { setLocale(locale: $locale) { id locale } }"


def test_set_locale_saves_the_signed_in_users_language(client: Client) -> None:
    user = make_user(locale="hr")
    token = access_token(user)

    body = gql(client, SET_LOCALE, {"locale": "FR"}, token=token)

    assert body == {"data": {"setLocale": {"id": str(user.pk), "locale": "FR"}}}
    user.refresh_from_db()
    assert user.locale == "fr"


def test_set_locale_needs_a_signed_in_user(client: Client) -> None:
    assert error_codes(gql(client, SET_LOCALE, {"locale": "DE"})) == ["UNAUTHENTICATED"]


PROVIDER_SIGN_IN = f"""
mutation Provider($input: ProviderSignInInput!, $device: DeviceInput!) {{
  signInWithProvider(input: $input, device: $device) {{
    __typename
    ... on AuthSession {{ {SESSION_FIELDS} }}
    ... on AccountDetailsRequired {{ id email ticket firstName lastName }}
    ... on ProviderLinkRequired {{ id email ticket }}
  }}
}}
"""
LINK = f"""
mutation Link($ticket: String!, $code: String!, $device: DeviceInput!) {{
  linkProviderWithEmailCode(ticket: $ticket, code: $code, device: $device) {{ {SESSION_FIELDS} }}
}}
"""


def sign_in_nonce(client: Client) -> str:
    body = gql(client, "mutation { requestSignInNonce { id nonce expiresAt } }")
    issued = body["data"]["requestSignInNonce"]
    assert issued["id"] and issued["expiresAt"]
    return issued["nonce"]


def test_sign_up_without_accepted_terms_gets_terms_not_accepted(client: Client) -> None:
    email = "ana.kovac@example.org"
    ticket = verify(client, email, request_code(client, email))["data"]["verifyEmailCode"]["ticket"]
    without = details(ticket)
    del without["acceptedTermsVersion"]

    for sent in (without, details(ticket, acceptedTermsVersion="2020-01")):
        body = gql(client, COMPLETE, {"details": sent, "device": DEVICE})
        assert error_codes(body) == ["TERMS_NOT_ACCEPTED"]
    assert not User.objects.exists()

    body = gql(client, COMPLETE, {"details": details(ticket), "device": DEVICE})
    assert body["data"]["completeAccountDetails"]["user"]["email"] == email
    acceptance = TermsAcceptance.objects.get()
    assert (acceptance.platform, acceptance.sign_in_method) == ("web", "email")


@pytest.mark.usefixtures("provider_keys")
def test_apple_sign_up_goes_through_the_details_step(client: Client) -> None:
    nonce = sign_in_nonce(client)
    provider_input = {
        "provider": "APPLE",
        "idToken": id_token("apple", nonce=nonce, email="x7k2p9@privaterelay.example.com"),
        "nonce": nonce,
        "firstName": "Ana",
        "lastName": "Kovač",
    }

    body = gql(client, PROVIDER_SIGN_IN, {"input": provider_input, "device": DEVICE})

    required = body["data"]["signInWithProvider"]
    assert required["__typename"] == "AccountDetailsRequired"
    assert required["email"] == "x7k2p9@privaterelay.example.com"
    assert (required["firstName"], required["lastName"]) == ("Ana", "Kovač")
    assert not User.objects.exists()

    body = gql(client, COMPLETE, {"details": details(required["ticket"]), "device": DEVICE})

    session = body["data"]["completeAccountDetails"]
    assert session["user"]["email"] == "x7k2p9@privaterelay.example.com"
    assert ProviderIdentity.objects.get().user.pk == int(session["user"]["id"])


@pytest.mark.usefixtures("provider_keys")
def test_google_token_for_another_app_gets_invalid_provider_token(client: Client) -> None:
    nonce = sign_in_nonce(client)
    provider_input = {
        "provider": "GOOGLE",
        "idToken": id_token(nonce=nonce, aud="someone-elses-app"),
        "nonce": nonce,
    }

    body = gql(client, PROVIDER_SIGN_IN, {"input": provider_input, "device": DEVICE})

    assert error_codes(body) == ["INVALID_PROVIDER_TOKEN"]
    assert not User.objects.exists()


@pytest.mark.usefixtures("provider_keys")
def test_google_email_of_existing_account_links_only_with_the_emailed_code(
    client: Client,
) -> None:
    user = make_user("ana")
    nonce = sign_in_nonce(client)
    provider_input = {
        "provider": "GOOGLE",
        "idToken": id_token(nonce=nonce, email="ana@example.com"),
        "nonce": nonce,
    }

    body = gql(client, PROVIDER_SIGN_IN, {"input": provider_input, "device": DEVICE})

    link = body["data"]["signInWithProvider"]
    assert link["__typename"] == "ProviderLinkRequired"
    assert link["email"] == "ana@example.com"
    assert "accessToken" not in link
    assert not ProviderIdentity.objects.exists()
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match is not None

    body = gql(client, LINK, {"ticket": link["ticket"], "code": match.group(1), "device": DEVICE})

    assert body["data"]["linkProviderWithEmailCode"]["user"]["id"] == str(user.pk)
    assert ProviderIdentity.objects.get().user == user


# --- BEAV-76 ----------------------------------------------------------------------------

SIGN_OUT_EVERYWHERE = "mutation { signOutEverywhere }"
FROM_EMAIL = "mutation($token: String!) { signOutEverywhereFromEmail(token: $token) }"


def test_sign_out_everywhere_needs_a_signed_in_user(client: Client) -> None:
    assert error_codes(gql(client, SIGN_OUT_EVERYWHERE)) == ["UNAUTHENTICATED"]


def test_sign_out_everywhere_ends_every_session(client: Client) -> None:
    from accounts.models import Session

    user = make_user("ana")
    phone, laptop = sign_in(user), sign_in(user, Session.Platform.WEB)

    body = gql(client, SIGN_OUT_EVERYWHERE, token=laptop.access_token)

    assert body == {"data": {"signOutEverywhere": True}}
    for issued in (phone, laptop):
        viewer = gql(client, "query { viewer { id } }", token=issued.access_token)
        assert viewer == {"data": {"viewer": None}}
    assert not Session.objects.filter(revoked_at__isnull=True).exists()


def test_sign_out_everywhere_from_email(client: Client) -> None:
    from accounts.models import Session
    from accounts.sessions import sign_out_link_token

    issued = sign_in(make_user("ana"))

    good = gql(client, FROM_EMAIL, {"token": sign_out_link_token(issued.session)})
    bad = gql(client, FROM_EMAIL, {"token": "forged"})

    assert good == {"data": {"signOutEverywhereFromEmail": True}}
    assert error_codes(bad) == ["SIGN_OUT_LINK_EXPIRED"]
    assert Session.objects.get().revoked_at is not None


def test_wrong_codes_lock_the_address_over_the_api(client: Client) -> None:
    code = request_code(client, "ana@example.com")
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    bodies = [verify(client, "ana@example.com", wrong) for _ in range(5)]

    assert [error_codes(b) for b in bodies] == [["INVALID_CODE"]] * 4 + [["LOCKED"]]
    assert bodies[-1]["errors"][0]["extensions"]["retryAfterSeconds"] == 1800


def test_code_requests_are_counted_per_client_address(client: Client, settings) -> None:
    settings.EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP = 2
    query = "mutation($email: String!) { requestEmailCode(email: $email, locale: EN) }"

    answers = [error_codes(gql(client, query, {"email": f"user{i}@example.com"})) for i in range(3)]

    assert answers == [[], [], ["RATE_LIMITED"]]


def test_sign_in_returns_a_device_token_that_makes_the_device_known(client: Client) -> None:
    from core.models import Outbox

    make_user("ana")
    query = VERIFY.replace("refreshTokenExpiresAt", "refreshTokenExpiresAt deviceToken")

    def sign_in_with(device: dict[str, Any]) -> dict[str, Any]:
        code = request_code(client, "ana@example.com")
        variables = {"email": "ana@example.com", "code": code, "device": device}
        return gql(client, query, variables)["data"]["verifyEmailCode"]

    first = sign_in_with(DEVICE)
    sign_in_with({**DEVICE, "deviceToken": first["deviceToken"]})

    assert len(first["deviceToken"]) == 43
    # The first session of the account sends nothing; the known device neither.
    assert not Outbox.objects.filter(topic="session.new_device").exists()
