"""Test users and sessions, shared by every app's tests."""

from typing import Any, cast

from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone

from accounts.models import Session, User
from accounts.sessions import IssuedTokens, start_session
from legal.models import LegalDocument, TermsAcceptance
from legal.services import current_document

# The TERMS version the `current_terms` fixture puts in force for sign-up tests.
TERMS_VERSION = "2026-11"


def make_user(name: str = "ana", **fields: Any) -> User:
    details: dict[str, Any] = {
        "first_name": name.capitalize(),
        "last_name": "Test",
        "locale": "hr",
        "country": "HR",
        "time_zone": "Europe/Zagreb",
        "adult_confirmed_at": timezone.now(),
        **fields,
    }
    return User.objects.create_user(f"{name}@example.com", **details)


def sign_in(user: AbstractBaseUser, platform: str = Session.Platform.IOS) -> IssuedTokens:
    return start_session(cast(User, user), platform, "Test device")


def access_token(user: AbstractBaseUser) -> str:
    return sign_in(user).access_token


def accept_current_terms(user: User, language: str = "en") -> None:
    """Records `user`'s acceptance of the TERMS version in force, as sign-up does, so a
    test that publishes TERMS keeps the account out of limited mode (accounts.states)."""
    document = current_document(LegalDocument.Kind.TERMS, language)
    assert document is not None, "Publish a TERMS version first."
    TermsAcceptance.objects.create(user=user, document=document, platform=Session.Platform.WEB)
