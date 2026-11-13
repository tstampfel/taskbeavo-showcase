"""Google and Apple ID-token verification (accounts/providers.py)."""

import datetime as dt
import hashlib
import time
from typing import Any

import jwt
import pytest
from django.utils import timezone

from accounts import providers
from accounts.models import SignInNonce
from accounts.tasks import purge_expired
from accounts.tests.id_tokens import (
    APPLE_SERVICES_ID,
    GOOGLE_IOS_CLIENT_ID,
    OTHER_KEY,
    id_token,
)
from core.errors import ApiError, ErrorCode

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("provider_keys")]


def rejected(provider: str, token: str, nonce: str) -> bool:
    with pytest.raises(ApiError) as excinfo:
        providers.verify_id_token(provider, token, nonce)
    return excinfo.value.code is ErrorCode.INVALID_PROVIDER_TOKEN


def test_nonce_is_stored_only_as_a_hash() -> None:
    issued = providers.issue_nonce()

    row = SignInNonce.objects.get()
    assert row.nonce_hash == hashlib.sha256(issued.nonce.encode()).hexdigest()
    assert issued.nonce not in row.nonce_hash
    assert row.expires_at - row.created_at == dt.timedelta(minutes=10)


def test_valid_google_token_is_accepted() -> None:
    nonce = providers.issue_nonce().nonce

    account = providers.verify_id_token("google", id_token(nonce=nonce), nonce)

    assert account == providers.ProviderAccount(
        provider="google",
        subject="google-subject-1",
        email="ana.kovac@mail.example.com",
        email_verified=True,
        is_private_relay_email=False,
        given_name="Ana",
        family_name="Kovač",
    )


@pytest.mark.parametrize(
    ("provider", "audience"), [("google", GOOGLE_IOS_CLIENT_ID), ("apple", APPLE_SERVICES_ID)]
)
def test_every_configured_client_id_is_an_accepted_audience(provider: str, audience: str) -> None:
    nonce = providers.issue_nonce().nonce

    account = providers.verify_id_token(
        provider, id_token(provider, nonce=nonce, aud=audience), nonce
    )

    assert account.subject == f"{provider}-subject-1"


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_token_for_another_app_is_rejected(provider: str) -> None:
    nonce = providers.issue_nonce().nonce
    token = id_token(provider, nonce=nonce, aud="someone-elses-app")

    assert rejected(provider, token, nonce)


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_expired_token_is_rejected(provider: str) -> None:
    nonce = providers.issue_nonce().nonce
    an_hour_ago = int(time.time()) - 3600
    token = id_token(provider, nonce=nonce, iat=an_hour_ago - 600, exp=an_hour_ago)

    assert rejected(provider, token, nonce)


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_token_with_wrong_nonce_is_rejected(provider: str) -> None:
    nonce = providers.issue_nonce().nonce
    token = id_token(provider, nonce=providers.issue_nonce().nonce)

    assert rejected(provider, token, nonce)
    # The issued nonce wasn't burnt by the failed attempt.
    assert not SignInNonce.objects.filter(used_at__isnull=False).exists()


def test_token_without_nonce_is_rejected() -> None:
    nonce = providers.issue_nonce().nonce

    assert rejected("google", id_token(nonce=None), nonce)


def test_nonce_we_never_issued_is_rejected() -> None:
    assert rejected("google", id_token(nonce="made-up"), "made-up")


def test_nonce_works_only_once() -> None:
    nonce = providers.issue_nonce().nonce
    token = id_token(nonce=nonce)
    providers.verify_id_token("google", token, nonce)

    assert rejected("google", token, nonce)


def test_expired_nonce_is_rejected() -> None:
    nonce = providers.issue_nonce().nonce
    SignInNonce.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    assert rejected("google", id_token(nonce=nonce), nonce)


def test_token_may_carry_the_hashed_nonce() -> None:
    nonce = providers.issue_nonce().nonce
    hashed = hashlib.sha256(nonce.encode()).hexdigest()

    account = providers.verify_id_token("apple", id_token("apple", nonce=hashed), nonce)

    assert account.provider == "apple"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example.com"},
        # A Google token presented as Apple's.
        {"iss": "https://accounts.google.com"},
        {"sub": None},
    ],
)
def test_wrong_issuer_or_missing_subject_is_rejected(claims: dict[str, Any]) -> None:
    nonce = providers.issue_nonce().nonce

    assert rejected("apple", id_token("apple", nonce=nonce, **claims), nonce)


def test_token_signed_with_another_key_is_rejected() -> None:
    nonce = providers.issue_nonce().nonce

    assert rejected("google", id_token(nonce=nonce, key=OTHER_KEY), nonce)


def test_garbage_and_unknown_provider_are_rejected() -> None:
    nonce = providers.issue_nonce().nonce

    assert rejected("google", "not-a-jwt", nonce)
    assert rejected("facebook", id_token(nonce=nonce), nonce)


def test_provider_without_client_ids_refuses_every_token(settings: Any) -> None:
    settings.APPLE_SIGN_IN_CLIENT_IDS = []
    nonce = providers.issue_nonce().nonce

    assert rejected("apple", id_token("apple", nonce=nonce), nonce)


def test_apple_private_relay_flag_is_read() -> None:
    nonce = providers.issue_nonce().nonce
    token = id_token(
        "apple",
        nonce=nonce,
        email="x7k2p9@privaterelay.example.com",
        is_private_email="true",
    )

    account = providers.verify_id_token("apple", token, nonce)

    assert account.is_private_relay_email
    assert account.email == "x7k2p9@privaterelay.example.com"
    assert account.given_name == ""


def test_expired_nonces_are_purged() -> None:
    providers.issue_nonce()
    SignInNonce.objects.update(expires_at=timezone.now() - dt.timedelta(days=2))
    providers.issue_nonce()

    purge_expired()

    assert SignInNonce.objects.count() == 1


def test_non_ascii_nonce_is_rejected_not_a_crash() -> None:
    nonce = providers.issue_nonce().nonce

    assert rejected("google", id_token(nonce=nonce), "ü-not-ours")


def test_hash_from_the_token_is_not_accepted_as_the_nonce() -> None:
    nonce = providers.issue_nonce().nonce
    hashed = hashlib.sha256(nonce.encode()).hexdigest()

    # The hash matches the token's nonce, but no nonce was issued whose hash is sha256(hash).
    assert rejected("apple", id_token("apple", nonce=hashed), hashed)


def test_unreachable_provider_keys_give_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(provider: str, token: str) -> None:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(providers, "_signing_key", unreachable)
    nonce = providers.issue_nonce().nonce

    with pytest.raises(ApiError) as excinfo:
        providers.verify_id_token("google", id_token(nonce=nonce), nonce)

    assert excinfo.value.code is ErrorCode.PROVIDER_UNAVAILABLE
