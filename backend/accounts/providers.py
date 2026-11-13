"""Google and Apple ID tokens, verified on the server (plan: "Sign-up and login").

The app signs in with the provider's own SDK and sends us the ID token it got back. We
trust nothing in it until `verify_id_token` has checked:

- the signature, against the provider's published keys (JWKS, cached by PyJWT);
- the issuer and the audience (one of our client IDs in settings);
- the expiry and issue time;
- the nonce: the token must carry a nonce we issued (`issue_nonce`), unused and unexpired.
  It may carry the nonce itself or its SHA-256 hex digest, since Apple's native SDKs and
  Firebase-style clients hash it before handing it to the provider. Using it burns it, so
  a token can't be replayed.

Every failure is INVALID_PROVIDER_TOKEN; what was wrong is not told to the client. When
the provider's key endpoint can't be reached, the client gets PROVIDER_UNAVAILABLE and the
outage is logged as an error (Sentry).
"""

import datetime as dt
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from functools import cache
from typing import Any

import jwt
from django.conf import settings
from django.utils import timezone

from accounts.models import ProviderIdentity, SignInNonce
from core.errors import ApiError, ErrorCode

Provider = ProviderIdentity.Provider
logger = logging.getLogger(__name__)

_JWKS_URLS = {
    Provider.GOOGLE: "https://www.googleapis.com/oauth2/v3/certs",
    Provider.APPLE: "https://appleid.apple.com/auth/keys",
}
_ISSUERS = {
    Provider.GOOGLE: ("https://accounts.google.com", "accounts.google.com"),
    Provider.APPLE: ("https://appleid.apple.com",),
}
# Both providers sign ID tokens with RS256.
_ALGORITHMS = ["RS256"]
# Tolerated clock difference between the provider and us.
_LEEWAY_SECONDS = 30
_JWKS_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class IssuedNonce:
    nonce: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class ProviderAccount:
    """What a verified ID token says about the person."""

    provider: str
    subject: str
    # "" when the token has no email (Apple sends one only if the app asked for it).
    email: str
    email_verified: bool
    is_private_relay_email: bool
    # Google's token carries the name; Apple's never does.
    given_name: str = ""
    family_name: str = ""


def issue_nonce() -> IssuedNonce:
    """A fresh one-time nonce for the client to pass to Google or Apple."""
    nonce = secrets.token_urlsafe(32)
    now = timezone.now()
    row = SignInNonce.objects.create(
        nonce_hash=_sha256(nonce),
        created_at=now,
        expires_at=now + dt.timedelta(seconds=settings.SIGN_IN_NONCE_TTL_SECONDS),
    )
    return IssuedNonce(nonce=nonce, expires_at=row.expires_at)


def verify_id_token(provider: str, id_token: str, nonce: str) -> ProviderAccount:
    """Check `id_token` from `provider` and burn `nonce`. Raises INVALID_PROVIDER_TOKEN."""
    if provider not in _JWKS_URLS:
        raise invalid_token()
    audiences = _audiences(provider)
    if not audiences:
        raise invalid_token()
    try:
        claims = jwt.decode(
            id_token,
            _signing_key(provider, id_token),
            algorithms=_ALGORITHMS,
            audience=audiences,
            issuer=_ISSUERS[Provider(provider)],
            leeway=_LEEWAY_SECONDS,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        )
    except jwt.PyJWKClientConnectionError:
        # The provider's key endpoint is down: our problem, not the token's.
        logger.exception("Sign-in keys of %s unreachable", provider)
        raise ApiError(
            ErrorCode.PROVIDER_UNAVAILABLE, "The sign-in provider can't be reached."
        ) from None
    except jwt.PyJWTError:
        raise invalid_token() from None
    _use_nonce(nonce, str(claims["nonce"]))
    return ProviderAccount(
        provider=provider,
        subject=str(claims["sub"]),
        email=str(claims.get("email") or ""),
        email_verified=_truthy(claims.get("email_verified")),
        is_private_relay_email=_truthy(claims.get("is_private_email")),
        given_name=str(claims.get("given_name") or "").strip(),
        family_name=str(claims.get("family_name") or "").strip(),
    )


def _audiences(provider: str) -> list[str]:
    if provider == Provider.GOOGLE:
        return list(settings.GOOGLE_OAUTH_CLIENT_IDS)
    return list(settings.APPLE_SIGN_IN_CLIENT_IDS)


def _signing_key(provider: str, id_token: str) -> Any:
    """The provider's public key that signed `id_token` (by its `kid`)."""
    return _jwks_client(provider).get_signing_key_from_jwt(id_token).key


@cache
def _jwks_client(provider: str) -> jwt.PyJWKClient:
    # One client per process, so the key set is fetched once and cached (lifespan 5 min,
    # refetched early when a token names an unknown key).
    return jwt.PyJWKClient(_JWKS_URLS[Provider(provider)], timeout=_JWKS_TIMEOUT_SECONDS)


def _use_nonce(nonce: str, token_nonce: str) -> None:
    """Burn our `nonce` if the token carries it (as is or hashed); else INVALID_PROVIDER_TOKEN."""
    nonce_hash = _sha256(nonce)
    # Compared as bytes: compare_digest refuses non-ASCII str, and both come from outside.
    token_bytes = token_nonce.encode()
    if not (
        hmac.compare_digest(token_bytes, nonce.encode())
        or hmac.compare_digest(token_bytes, nonce_hash.encode())
    ):
        raise invalid_token()
    now = timezone.now()
    # One UPDATE, so of two concurrent sign-ins with the same nonce only one wins.
    used = SignInNonce.objects.filter(
        nonce_hash=nonce_hash, used_at__isnull=True, expires_at__gt=now
    ).update(used_at=now)
    if used != 1:
        raise invalid_token()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _truthy(value: object) -> bool:
    # Apple sends booleans as the strings "true" and "false".
    return value is True or value == "true"


def invalid_token() -> ApiError:
    return ApiError(ErrorCode.INVALID_PROVIDER_TOKEN, "The sign-in token isn't valid.")
