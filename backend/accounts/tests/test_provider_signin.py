"""Google and Apple sign-in through the account-details step (accounts/signin.py)."""

import datetime as dt
import re
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone

from accounts import providers, signin
from accounts.models import EmailCode, PendingProviderName, ProviderIdentity, Session, User
from accounts.sessions import NEW_DEVICE_TOPIC, IssuedTokens
from accounts.tasks import purge_expired
from accounts.tests.factories import TERMS_VERSION, make_user
from accounts.tests.id_tokens import id_token
from core.errors import ApiError, ErrorCode
from core.models import Outbox
from legal.models import TermsAcceptance

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("provider_keys", "current_terms")]

RELAY = "x7k2p9@privaterelay.example.com"


def gmail_user(**fields: Any) -> User:
    user = make_user("ana", **fields)
    user.email = "ana.kovac@mail.example.com"
    user.save(update_fields=["email"])
    return user


def sign_in(
    provider: str = "google",
    *,
    first_name: str = "",
    last_name: str = "",
    device_token: str | None = None,
    **claims: Any,
) -> IssuedTokens | signin.ProviderLinkRequired | signin.AccountDetailsRequired:
    nonce = providers.issue_nonce().nonce
    return signin.sign_in_with_provider(
        provider=provider,
        id_token=id_token(provider, nonce=nonce, **claims),
        nonce=nonce,
        platform="ios",
        device_name="iPhone",
        first_name=first_name,
        last_name=last_name,
        device_token=device_token,
    )


def complete(ticket: str, **overrides: Any) -> IssuedTokens:
    details: dict[str, Any] = {
        "ticket": ticket,
        "first_name": "Ana",
        "last_name": "Kovač",
        "is_adult": True,
        "locale": "de",
        "country": "DE",
        "time_zone": "Europe/Berlin",
        "platform": "ios",
        "accepted_terms_version": TERMS_VERSION,
        **overrides,
    }
    return signin.complete_account_details(**details)


def test_new_google_user_goes_through_the_details_step() -> None:
    result = sign_in()

    assert isinstance(result, signin.AccountDetailsRequired)
    assert result.email == "ana.kovac@mail.example.com"
    assert (result.first_name, result.last_name) == ("Ana", "Kovač")
    # The provider sign-in itself never creates the account.
    assert not User.objects.exists()
    assert not ProviderIdentity.objects.exists()

    issued = complete(result.ticket, first_name="Ana Marija")

    user = issued.session.user
    assert user.email == "ana.kovac@mail.example.com"
    assert user.first_name == "Ana Marija"
    identity = ProviderIdentity.objects.get()
    assert (identity.user, identity.provider, identity.subject) == (
        user,
        "google",
        "google-subject-1",
    )
    assert not identity.is_private_relay_email


def test_known_provider_account_signs_straight_in() -> None:
    first = sign_in()
    assert isinstance(first, signin.AccountDetailsRequired)
    user = complete(first.ticket).session.user

    # The email changed at Google since; the subject still finds the account.
    again = sign_in(email="ana.new@mail.example.com")

    assert isinstance(again, IssuedTokens)
    assert again.session.user == user
    assert again.session.platform == "ios"
    assert User.objects.count() == 1


def test_new_apple_user_keeps_relay_email_and_one_time_name() -> None:
    result = sign_in(
        "apple",
        first_name=" Ana ",
        last_name="Kovač",
        email=RELAY,
        is_private_email="true",
    )

    assert isinstance(result, signin.AccountDetailsRequired)
    assert result.email == RELAY
    assert (result.first_name, result.last_name) == ("Ana", "Kovač")

    user = complete(result.ticket).session.user

    assert user.email == RELAY
    assert (user.first_name, user.last_name) == ("Ana", "Kovač")
    identity = ProviderIdentity.objects.get(user=user)
    assert identity.provider == "apple"
    assert identity.is_private_relay_email

    # Apple sends neither the name nor (often) the email again; the subject is enough.
    again = sign_in("apple", email=None, email_verified=None)
    assert isinstance(again, IssuedTokens)
    assert again.session.user == user


def test_under_18_provider_user_gets_no_account() -> None:
    result = sign_in("apple")
    assert isinstance(result, signin.AccountDetailsRequired)

    with pytest.raises(ApiError) as excinfo:
        complete(result.ticket, is_adult=False)

    assert excinfo.value.code is ErrorCode.UNDERAGE
    assert not User.objects.exists()
    assert not ProviderIdentity.objects.exists()


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_provider_sign_up_records_one_terms_acceptance(provider: str) -> None:
    result = sign_in(provider)
    assert isinstance(result, signin.AccountDetailsRequired)

    user = complete(result.ticket, locale="fr").session.user

    acceptance = TermsAcceptance.objects.get()
    assert acceptance.user == user
    document = acceptance.document
    assert (document.kind, document.version, document.language) == ("TERMS", TERMS_VERSION, "fr")
    assert (acceptance.platform, acceptance.sign_in_method) == ("ios", provider)


@pytest.mark.parametrize("provider", ["google", "apple"])
@pytest.mark.parametrize("version", [None, "2025-01"])
def test_provider_sign_up_without_the_current_terms_is_rejected(
    provider: str, version: str | None
) -> None:
    result = sign_in(provider)
    assert isinstance(result, signin.AccountDetailsRequired)

    with pytest.raises(ApiError) as excinfo:
        complete(result.ticket, accepted_terms_version=version)

    assert excinfo.value.code is ErrorCode.TERMS_NOT_ACCEPTED
    assert not User.objects.exists()
    assert not ProviderIdentity.objects.exists()
    assert not TermsAcceptance.objects.exists()


def emailed_code() -> str:
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match is not None
    return match.group(1)


def link_required(**claims: Any) -> signin.ProviderLinkRequired:
    result = sign_in(**claims)
    assert isinstance(result, signin.ProviderLinkRequired)
    return result


def test_matching_email_never_links_without_the_code() -> None:
    user = gmail_user()

    # Even an address on Google's own mail domain (gmail.com in real life), which Google is
    # authoritative for, needs the code.
    result = link_required(email="Ana.Kovac@Mail.Example.com")

    assert result.email == "ana.kovac@mail.example.com"
    assert not ProviderIdentity.objects.exists()
    assert not Session.objects.filter(user=user).exists()
    # The code goes to the account's own address; that email is the owner's notice.
    assert mail.outbox[-1].to == ["ana.kovac@mail.example.com"]


def test_non_gmail_google_address_without_hd_needs_the_code_too() -> None:
    # A consumer Google account on someone else's domain: "verified" may be stale.
    user = make_user("ana")  # ana@example.com

    result = link_required(email="ana@example.com", hd=None)

    assert result.email == "ana@example.com"
    assert not ProviderIdentity.objects.exists()
    assert not Session.objects.filter(user=user).exists()


def test_verified_code_links_the_provider_and_signs_in() -> None:
    user = gmail_user()
    result = link_required()

    issued = signin.link_provider_with_email_code(result.ticket, emailed_code(), "ios", "iPhone")

    assert issued.session.user == user
    identity = ProviderIdentity.objects.get()
    assert (identity.user, identity.provider, identity.subject) == (
        user,
        "google",
        "google-subject-1",
    )
    # From now on the provider account signs straight in.
    assert isinstance(sign_in(), IssuedTokens)
    # Linking adds a sign-in to an account that exists; it accepts nothing new.
    assert not TermsAcceptance.objects.exists()


def test_wrong_code_does_not_link() -> None:
    gmail_user()
    result = link_required()
    code = emailed_code()
    wrong = "000000" if code != "000000" else "111111"

    with pytest.raises(ApiError) as excinfo:
        signin.link_provider_with_email_code(result.ticket, wrong, "ios")

    assert excinfo.value.code is ErrorCode.INVALID_CODE
    assert not ProviderIdentity.objects.exists()
    assert not Session.objects.exists()


def test_expired_code_does_not_link() -> None:
    gmail_user()
    result = link_required()
    EmailCode.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    with pytest.raises(ApiError) as excinfo:
        signin.link_provider_with_email_code(result.ticket, emailed_code(), "ios")

    assert excinfo.value.code is ErrorCode.INVALID_CODE
    assert not ProviderIdentity.objects.exists()
    assert not Session.objects.exists()


def test_link_ticket_is_not_a_sign_up_ticket_and_cannot_be_tampered() -> None:
    gmail_user()
    result = link_required()

    with pytest.raises(ApiError) as tampered:
        signin.link_provider_with_email_code(result.ticket[:-2] + "xx", emailed_code(), "ios")
    with pytest.raises(ApiError) as as_sign_up:
        complete(result.ticket)

    assert tampered.value.code is ErrorCode.SIGNUP_TICKET_EXPIRED
    assert as_sign_up.value.code is ErrorCode.SIGNUP_TICKET_EXPIRED
    assert not ProviderIdentity.objects.exists()


def test_provider_linked_elsewhere_meanwhile_expires_the_link_ticket() -> None:
    user = gmail_user()
    result = link_required()
    other = make_user("bo")
    ProviderIdentity.objects.create(user=other, provider="google", subject="google-subject-1")

    with pytest.raises(ApiError) as excinfo:
        signin.link_provider_with_email_code(result.ticket, emailed_code(), "ios")

    assert excinfo.value.code is ErrorCode.SIGNUP_TICKET_EXPIRED
    assert not ProviderIdentity.objects.filter(user=user).exists()
    assert not Session.objects.filter(user=user).exists()


def test_apple_name_survives_leaving_before_the_details_step() -> None:
    first = sign_in("apple", first_name="Ana", last_name="Kovač")
    assert isinstance(first, signin.AccountDetailsRequired)

    # The app was closed; Apple doesn't send the name on the next sign-in.
    again = sign_in("apple")

    assert isinstance(again, signin.AccountDetailsRequired)
    assert (again.first_name, again.last_name) == ("Ana", "Kovač")
    complete(again.ticket)
    assert not PendingProviderName.objects.exists()


def test_pending_apple_name_expires_and_is_purged() -> None:
    sign_in("apple", first_name="Ana", last_name="Kovač")
    PendingProviderName.objects.update(expires_at=timezone.now() - dt.timedelta(days=2))

    again = sign_in("apple")
    purge_expired()

    assert isinstance(again, signin.AccountDetailsRequired)
    assert (again.first_name, again.last_name) == ("", "")
    assert not PendingProviderName.objects.exists()


@pytest.mark.parametrize(
    "claims",
    [
        {"email": None, "email_verified": None},
        {"email_verified": False},
        {"email_verified": "false"},
    ],
)
def test_new_account_needs_a_verified_email(claims: dict[str, Any]) -> None:
    user = gmail_user()

    with pytest.raises(ApiError) as excinfo:
        sign_in(**claims)

    assert excinfo.value.code is ErrorCode.PROVIDER_EMAIL_MISSING
    # An unverified address never links to the account that owns it.
    assert not ProviderIdentity.objects.filter(user=user).exists()


def test_deactivated_account_cannot_sign_in_with_a_provider() -> None:
    gmail_user(is_active=False)

    with pytest.raises(ApiError) as excinfo:
        sign_in()

    assert excinfo.value.code is ErrorCode.INVALID_PROVIDER_TOKEN


def test_provider_ticket_for_an_address_taken_meanwhile_expires() -> None:
    result = sign_in()
    assert isinstance(result, signin.AccountDetailsRequired)
    gmail_user()

    with pytest.raises(ApiError) as excinfo:
        complete(result.ticket)

    assert excinfo.value.code is ErrorCode.SIGNUP_TICKET_EXPIRED
    assert not ProviderIdentity.objects.exists()


def test_tampered_provider_ticket_is_refused() -> None:
    result = sign_in()
    assert isinstance(result, signin.AccountDetailsRequired)

    with pytest.raises(ApiError) as excinfo:
        complete(result.ticket[:-2] + "xx")

    assert excinfo.value.code is ErrorCode.SIGNUP_TICKET_EXPIRED


def test_a_known_provider_account_still_signs_in_while_its_email_is_locked() -> None:
    """A stranger locking the address with wrong codes doesn't lock out Google or Apple."""
    user = gmail_user()
    ProviderIdentity.objects.create(user=user, provider="google", subject="google-sub-1")
    for _ in range(5):
        with pytest.raises(ApiError):
            signin.verify_email_code(user.email, "123456", "web")

    assert isinstance(sign_in(sub="google-sub-1", email=user.email), IssuedTokens)


def test_linking_with_a_code_is_locked_with_the_address() -> None:
    gmail_user()
    result = link_required()
    codes = []
    for _ in range(5):
        with pytest.raises(ApiError) as excinfo:
            signin.link_provider_with_email_code(result.ticket, "000000", "ios")
        codes.append(excinfo.value.code)

    assert codes[-1] is ErrorCode.LOCKED
    assert not ProviderIdentity.objects.exists()


def new_device_notices() -> int:
    return Outbox.objects.filter(topic=NEW_DEVICE_TOPIC).count()


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_provider_sign_in_sends_the_new_device_notice_unless_the_device_is_known(
    provider: str,
) -> None:
    result = sign_in(provider)
    assert isinstance(result, signin.AccountDetailsRequired)
    first = complete(result.ticket)
    assert new_device_notices() == 0  # the account's first session

    known = sign_in(provider, device_token=first.device_token)
    assert isinstance(known, IssuedTokens)
    assert known.device_token == first.device_token
    assert new_device_notices() == 0

    stranger = sign_in(provider)
    assert isinstance(stranger, IssuedTokens)
    assert stranger.device_token not in (None, first.device_token)
    assert new_device_notices() == 1


def test_linking_a_provider_with_the_code_sends_the_notice_unless_the_device_is_known() -> None:
    gmail_user()
    phone = signin.link_provider_with_email_code(
        link_required().ticket, emailed_code(), "ios", "iPhone"
    )
    ProviderIdentity.objects.all().delete()
    assert new_device_notices() == 0

    signin.link_provider_with_email_code(
        link_required().ticket, emailed_code(), "ios", "iPhone", device_token=phone.device_token
    )
    assert new_device_notices() == 0
    ProviderIdentity.objects.all().delete()

    signin.link_provider_with_email_code(link_required().ticket, emailed_code(), "web", "Firefox")
    assert new_device_notices() == 1
