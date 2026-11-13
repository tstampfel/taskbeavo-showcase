"""Email-code, Google and Apple sign-in and the account-details step (plan: "Sign-up and login").

1. `request_email_code(email, locale, client_ip)` emails a 6-digit code. The answer never says
   whether the address has an account.
2. `verify_email_code(email, code)` checks it: an existing account gets a session; a new
   address gets a sign-up ticket, a signed token proving the address that expires after
   SIGNUP_TICKET_MAX_AGE_SECONDS.
3. `complete_account_details(ticket, ...)` creates the user, and only here: name, 18+
   confirmation, language, country, time zone and the accepted terms version, recorded
   as a TermsAcceptance in the same transaction (legal/models.py). Then it starts a
   session.

Google and Apple join the same flow at step 2: `sign_in_with_provider` verifies the ID
token (accounts/providers.py).
- A known provider account (by the provider's `sub`) gets a session.
- An address that already has an account is never linked on the provider's word alone:
  a provider's "verified" email may be stale (a lapsed domain, an ex-employee's address).
  The account's address gets an email code (step 1) and the result is a link ticket;
  `link_provider_with_email_code(ticket, code)` links the provider account and signs in.
  The code email is the owner's notice that someone asked to link.
- Anyone else gets the same sign-up ticket as an email code, which also names the
  provider account so step 3 links it. The provider callback never creates a user.
Apple sends the person's name only on the first sign-in, and only to the app, which passes
it along. It is kept in a `PendingProviderName` row until the account exists (so it
survives leaving before the details step) and pre-fills the details step, where it is
saved.

Codes are stored as an HMAC keyed with SECRET_KEY, so a database leak doesn't reveal
live codes.

Limits (BEAV-76), the same whether or not the address has an account, so they can't be
used to find out which addresses do:
- Code requests: EMAIL_CODE_REQUESTS_PER_HOUR_PER_EMAIL per address and
  EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP per client address, over a sliding hour
  (accounts/limits.py). Over either: RATE_LIMITED with `retryAfterSeconds`.
- Wrong codes lock the attacker, not the owner (decision 2026-11-04, after NIST SP
  800-63B's preference for throttling over locks attackers can abuse; it replaces the
  plan's "30-minute lockout after 5 wrong codes"):
  - EMAIL_CODE_LOCKOUT_AFTER_FAILURES wrong guesses for an address from one source
    within EMAIL_CODE_FAILURE_WINDOW_SECONDS block that source for that address for
    EMAIL_CODE_LOCKOUT_SECONDS (LOCKED with `retryAfterSeconds`). The source is the
    device token when it is one of the account's known devices, else the client
    address (IPv6 by /64). A token the attacker makes up is never a source, or a fresh
    one per guess would dodge the block. The owner on another device or network signs
    in as usual.
  - EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR wrong guesses for an address from all sources
    within the window lock email sign-in for that address for EMAIL_CODE_LOCKOUT_SECONDS,
    for checks and new code requests alike, and burn its live code. Google and Apple
    stay open; support lifts it with `manage.py unlock_email_sign_in`.
  Both counts are per address (and source), not per code, so asking for a new code
  doesn't give back the guesses. A correct code clears the address's count and the
  source's.
- Each code also burns after EMAIL_CODE_MAX_ATTEMPTS wrong guesses.
The counters live in Redis, keyed by the address's keyed digest, never the address.
"""

import datetime as dt
import hmac
import re
import secrets
import time
import zoneinfo
from dataclasses import dataclass
from typing import Any, cast

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts import providers
from accounts.limits import address_bucket, check_hourly, store
from accounts.models import (
    EmailCode,
    PendingProviderName,
    ProviderIdentity,
    User,
    normalize_email,
)
from accounts.sessions import IssuedTokens, device_digest, is_known_device, start_session
from config.graphql_limits import incr_in_window
from core.crypto import pii_digest
from core.email import render_email, send_email
from core.errors import ApiError, ErrorCode
from core.i18n import SUPPORTED_LOCALES, LocaleRecipient
from legal.models import LegalDocument, TermsAcceptance
from legal.services import current_document

_CODE_SALT = "taskbeavo.accounts.email-code.v1"
_TICKET_SALT = "taskbeavo.accounts.signup-ticket.v1"
_LINK_TICKET_SALT = "taskbeavo.accounts.provider-link-ticket.v1"
_CODE_PATTERN = re.compile(r"^\d{6}$")
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
_NAME_MAX_LENGTH = 100
# How long Apple's one-time name waits for its account to be created.
PENDING_NAME_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class _CodeRecipient:
    """A recipient with no account yet: the code goes out in the client's language."""

    locale: str | None


@dataclass(frozen=True)
class AccountDetailsRequired:
    """The address is verified but has no account: finish with `complete_account_details`."""

    email: str
    ticket: str
    # A pre-fill for the details step: the name Google or Apple shared, else "".
    first_name: str = ""
    last_name: str = ""


@dataclass(frozen=True)
class ProviderLinkRequired:
    """The provider's email has an account: a code went to it; finish with
    `link_provider_with_email_code`."""

    email: str
    ticket: str


def request_email_code(email: str, locale: str, client_ip: str = "") -> None:
    """Email a fresh sign-in code to `email`, replacing any unused earlier one.

    `locale` is the client's language, used only when the address has no account; an
    account's saved language wins. Raises LOCKED while the address is locked and
    RATE_LIMITED over the hourly caps.
    """
    address = _valid_email(email)
    digest = pii_digest("email", address)
    _check_not_locked(digest)
    # The client's cap first: a request it refuses uses none of the address's quota.
    check_hourly(
        f"email-code-send:ip:{address_bucket(client_ip)}",
        settings.EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP,
    )
    check_hourly(f"email-code-send:email:{digest}", settings.EMAIL_CODE_REQUESTS_PER_HOUR_PER_EMAIL)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    with transaction.atomic():
        EmailCode.objects.filter(email=address, used_at__isnull=True).delete()
        EmailCode.objects.create(
            email=address,
            code_hash=_code_hash(address, code),
            created_at=now,
            expires_at=now + dt.timedelta(seconds=settings.EMAIL_CODE_TTL_SECONDS),
        )
        user = User.objects.filter(email=address).first()
        recipient = cast(LocaleRecipient, user) if user else _CodeRecipient(locale)
        rendered = render_email(
            "sign_in_code",
            recipient,
            {"code": code, "minutes": settings.EMAIL_CODE_TTL_SECONDS // 60},
        )
    # Sent once the code is saved, in this request: the person is waiting for it.
    send_email(address, rendered)


def verify_email_code(
    email: str,
    code: str,
    platform: str,
    device_name: str = "",
    client_ip: str = "",
    device_token: str | None = None,
) -> IssuedTokens | AccountDetailsRequired:
    """Use the address's live code: a session for an account, else a sign-up ticket.

    Raises INVALID_CODE for a wrong, expired, used or burnt code. A wrong guess counts
    against the code even though the error is raised.
    """
    address = _valid_email(email)
    _use_email_code(address, code, client_ip, device_token)
    user = User.objects.filter(email=address, is_active=True).first()
    if user is not None:
        return start_session(user, platform, device_name, client_ip, device_token)
    if User.objects.filter(email=address).exists():
        # A deactivated account can't sign in, and can't be re-created either.
        raise ApiError(ErrorCode.INVALID_CODE, "The code is wrong or expired.")
    return AccountDetailsRequired(email=address, ticket=_new_ticket({"email": address}))


def _use_email_code(
    address: str, code: str, client_ip: str = "", device_token: str | None = None
) -> None:
    """Use the address's live code. Raises INVALID_CODE for a wrong, expired, used or burnt
    code, and LOCKED while the address or this source is locked, or on the guess that
    locks it; a wrong guess counts even though the error is raised."""
    digest = pii_digest("email", address)
    _check_not_locked(digest)
    source = _source(digest, address, client_ip, device_token)
    _check_not_locked(digest, source)
    now = timezone.now()
    with transaction.atomic():
        row = (
            EmailCode.objects.select_for_update()
            .filter(email=address, used_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        matched = (
            row is not None
            and _CODE_PATTERN.match(code.strip()) is not None
            and hmac.compare_digest(row.code_hash, _code_hash(address, code.strip()))
        )
        if row is not None:
            if matched:
                row.used_at = now
            else:
                row.failed_attempts += 1
                if row.failed_attempts >= settings.EMAIL_CODE_MAX_ATTEMPTS:
                    row.used_at = now
            row.save(update_fields=["used_at", "failed_attempts"])
    # Raised after the block, so the failed attempt is counted.
    if not matched:
        _count_failure(address, digest, source)
        raise ApiError(ErrorCode.INVALID_CODE, "The code is wrong or expired.")
    store(lambda cache: cache.delete_many([_failures_key(digest), _failures_key(digest, source)]))


def _source(digest: str, address: str, client_ip: str, device_token: str | None) -> str:
    """Where a guess comes from: a known device of the address's account, else the client
    address. Keyed under the address's epoch, so support's unlock clears every source."""
    epoch = store(lambda cache: cache.get(_epoch_key(digest))) or 0
    if device_token is not None and is_known_device(address, device_token):
        origin = f"device:{device_digest(device_token)}"
    else:
        origin = f"ip:{pii_digest('ip', address_bucket(client_ip))}"
    return f"{epoch}:{origin}"


def _failures_key(digest: str, source: str | None = None) -> str:
    key = f"email-code-fail:email:{digest}"
    return key if source is None else f"{key}:src:{source}"


def _lock_key(digest: str, source: str | None = None) -> str:
    key = f"email-code-lock:email:{digest}"
    return key if source is None else f"{key}:src:{source}"


def _epoch_key(digest: str) -> str:
    return f"email-code-epoch:email:{digest}"


def _check_not_locked(digest: str, source: str | None = None) -> None:
    """LOCKED while the address (or, given a source, that source for it) is locked, with
    the seconds left."""
    locked_until = store(lambda cache: cache.get(_lock_key(digest, source)))
    if locked_until is None:
        return
    remaining = int(locked_until) - int(time.time())
    raise _locked(max(remaining, 1))


def _count_failure(address: str, digest: str, source: str) -> None:
    """Count a wrong guess for the address and its source; at either limit, lock and raise
    LOCKED."""
    window = settings.EMAIL_CODE_FAILURE_WINDOW_SECONDS
    total = store(lambda cache: incr_in_window(cache, _failures_key(digest), window))
    from_source = store(lambda cache: incr_in_window(cache, _failures_key(digest, source), window))
    if total >= settings.EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR:
        _lock(digest, None)
        # The code on its way can't be tried after the lock either.
        EmailCode.objects.filter(email=address, used_at__isnull=True).update(used_at=timezone.now())
        raise _locked(settings.EMAIL_CODE_LOCKOUT_SECONDS)
    if from_source >= settings.EMAIL_CODE_LOCKOUT_AFTER_FAILURES:
        # Only this source: the owner's code stays usable from their own device.
        _lock(digest, source)
        raise _locked(settings.EMAIL_CODE_LOCKOUT_SECONDS)


def _lock(digest: str, source: str | None) -> None:
    lockout = settings.EMAIL_CODE_LOCKOUT_SECONDS
    until = int(time.time()) + lockout

    # The count is left to run out with its window: clearing it here would hand out a
    # fresh budget each time a lock ends, more than the cap per window.
    store(lambda cache: cache.set(_lock_key(digest, source), until, timeout=lockout))


def unlock_address(email: str) -> bool:
    """Support lifts an address's lockout and clears its wrong-code counts, those of every
    source too. True if the address itself was locked. Google and Apple sign-in stay open
    during a lockout anyway."""
    digest = pii_digest("email", _valid_email(email))
    was_locked = store(lambda cache: cache.get(_lock_key(digest))) is not None

    def unlock(cache: Any) -> None:
        cache.delete_many([_lock_key(digest), _failures_key(digest)])
        # Every source key carries the epoch: a new one orphans them (they expire).
        cache.set(_epoch_key(digest), int(time.time() * 1000), timeout=None)

    store(unlock)
    return was_locked


def _locked(retry_after_seconds: int) -> ApiError:
    return ApiError(
        ErrorCode.LOCKED,
        "Too many wrong codes; try again later.",
        retry_after_seconds=retry_after_seconds,
    )


def sign_in_with_provider(
    *,
    provider: str,
    id_token: str,
    nonce: str,
    platform: str,
    device_name: str = "",
    first_name: str = "",
    last_name: str = "",
    client_ip: str = "",
    device_token: str | None = None,
) -> IssuedTokens | ProviderLinkRequired | AccountDetailsRequired:
    """Verify a Google or Apple ID token: a session for a known provider account, a link
    ticket (and an email code) for an address that has an account, else a sign-up ticket.

    `first_name` and `last_name` are what Apple handed the app on the first sign-in (Google
    puts the name in the token); they only pre-fill the details step. Raises
    INVALID_PROVIDER_TOKEN, PROVIDER_UNAVAILABLE, or PROVIDER_EMAIL_MISSING when the
    provider shared no verified email.
    """
    account = providers.verify_id_token(provider, id_token, nonce)
    now = timezone.now()
    identity = (
        ProviderIdentity.objects.select_related("user")
        .filter(provider=account.provider, subject=account.subject)
        .first()
    )
    if identity is not None:
        if not identity.user.is_active:
            raise providers.invalid_token()
        identity.last_used_at = now
        identity.save(update_fields=["last_used_at"])
        return start_session(identity.user, platform, device_name, client_ip, device_token)
    if not account.email or not account.email_verified:
        raise ApiError(ErrorCode.PROVIDER_EMAIL_MISSING, "The provider shared no verified email.")
    address = normalize_email(account.email)
    user = User.objects.filter(email=address).first()
    if user is not None:
        if not user.is_active:
            raise providers.invalid_token()
        # Never link on the provider's word: the owner proves the address with a code.
        request_email_code(address, user.locale, client_ip)
        link = {
            "email": address,
            "provider": account.provider,
            "subject": account.subject,
            "private_relay": account.is_private_relay_email,
        }
        return ProviderLinkRequired(
            email=address, ticket=signing.dumps(link, salt=_LINK_TICKET_SALT)
        )
    given, family = _provider_names(account, first_name, last_name)
    ticket = _new_ticket(
        {
            "email": address,
            "provider": account.provider,
            "subject": account.subject,
            "private_relay": account.is_private_relay_email,
        }
    )
    return AccountDetailsRequired(email=address, ticket=ticket, first_name=given, last_name=family)


def link_provider_with_email_code(
    ticket: str,
    code: str,
    platform: str,
    device_name: str = "",
    client_ip: str = "",
    device_token: str | None = None,
) -> IssuedTokens:
    """Link the ticket's Google or Apple account to the account the code proves, and sign in.

    Raises INVALID_CODE for a wrong code, SIGNUP_TICKET_EXPIRED for a bad or old ticket or
    when the account is gone or the provider account was linked meanwhile.
    """
    try:
        data = signing.loads(
            ticket, salt=_LINK_TICKET_SALT, max_age=settings.SIGNUP_TICKET_MAX_AGE_SECONDS
        )
    except signing.BadSignature:
        raise _ticket_expired() from None
    if not _valid_provider_ticket(data):
        raise _ticket_expired()
    _use_email_code(data["email"], code, client_ip, device_token)
    try:
        with transaction.atomic():
            user = User.objects.filter(email=data["email"], is_active=True).first()
            if user is None:
                raise _ticket_expired()
            ProviderIdentity.objects.create(
                user=user,
                provider=data["provider"],
                subject=data["subject"],
                is_private_relay_email=bool(data.get("private_relay")),
            )
            return start_session(user, platform, device_name, client_ip, device_token)
    except IntegrityError:
        # The provider account was linked meanwhile (a second tab).
        raise _ticket_expired() from None


def complete_account_details(
    *,
    ticket: str,
    first_name: str,
    last_name: str,
    is_adult: bool,
    locale: str,
    country: str,
    time_zone: str,
    platform: str,
    accepted_terms_version: str | None,
    device_name: str = "",
    client_ip: str = "",
    device_token: str | None = None,
) -> IssuedTokens:
    """Create the account the ticket's address verified, then start its first session.

    A Google or Apple ticket also links that provider account to the new user.
    `accepted_terms_version` is the TERMS version the person ticked the box for: it must
    be the one in force (TERMS_NOT_ACCEPTED otherwise, also while none is), and its
    TermsAcceptance, in the chosen language, is written with the user, so no account
    exists without one whatever the sign-in method.
    """
    data = _ticket_data(ticket)
    email = data["email"]
    if not is_adult:
        raise ApiError(ErrorCode.UNDERAGE, "TaskBeavo is for people aged 18 or older.")
    details = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "locale": locale,
        "country": country.strip().upper(),
        "time_zone": time_zone.strip(),
    }
    if not _valid_details(details):
        raise ApiError(ErrorCode.INVALID_ACCOUNT_DETAILS, "Check the name, country and time zone.")
    try:
        with transaction.atomic():
            terms = current_document(LegalDocument.Kind.TERMS, details["locale"])
            if terms is None or accepted_terms_version != terms.version:
                raise ApiError(ErrorCode.TERMS_NOT_ACCEPTED, "Accept the terms version in force.")
            user = User.objects.create_user(email, adult_confirmed_at=timezone.now(), **details)
            TermsAcceptance.objects.create(
                user=user,
                document=terms,
                platform=platform,
                sign_in_method=data.get("provider", TermsAcceptance.SignInMethod.EMAIL),
            )
            if "provider" in data:
                ProviderIdentity.objects.create(
                    user=user,
                    provider=data["provider"],
                    subject=data["subject"],
                    is_private_relay_email=bool(data.get("private_relay")),
                )
                PendingProviderName.objects.filter(
                    provider=data["provider"], subject=data["subject"]
                ).delete()
    except IntegrityError:
        # The account was created meanwhile (a second tab): sign in instead.
        raise _ticket_expired() from None
    return start_session(user, platform, device_name, client_ip, device_token)


def _valid_email(email: str) -> str:
    address = normalize_email(email)
    try:
        validate_email(address)
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_EMAIL, "Not a valid email address.") from None
    return address


def _code_hash(address: str, code: str) -> str:
    # django-types lacks salted_hmac's `algorithm` parameter.
    digest = salted_hmac(_CODE_SALT, f"{address}:{code}", algorithm="sha256")  # pyright: ignore[reportCallIssue]
    return digest.hexdigest()


def _new_ticket(data: dict[str, object]) -> str:
    return signing.dumps(data, salt=_TICKET_SALT)


def _ticket_data(ticket: str) -> dict[str, Any]:
    """The ticket's verified email and, for Google and Apple, the provider account."""
    try:
        data = signing.loads(
            ticket, salt=_TICKET_SALT, max_age=settings.SIGNUP_TICKET_MAX_AGE_SECONDS
        )
    except signing.BadSignature:
        raise _ticket_expired() from None
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        raise _ticket_expired()
    if "provider" in data and not _valid_provider_ticket(data):
        raise _ticket_expired()
    return data


def _valid_provider_ticket(data: object) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("email"), str)
        and data.get("provider") in ProviderIdentity.Provider.values
        and isinstance(data.get("subject"), str)
    )


def _provider_names(
    account: providers.ProviderAccount, first_name: str, last_name: str
) -> tuple[str, str]:
    """The name to pre-fill: the token's (Google), else what the app passed (Apple), else
    what Apple passed on an earlier, unfinished sign-up.

    A name the app passed is kept in a PendingProviderName row, since Apple won't send it
    again; the row goes when the account is created or a day after it expires.
    """
    given = account.given_name or first_name.strip()[:_NAME_MAX_LENGTH]
    family = account.family_name or last_name.strip()[:_NAME_MAX_LENGTH]
    key = {"provider": account.provider, "subject": account.subject}
    if first_name.strip() or last_name.strip():
        now = timezone.now()
        PendingProviderName.objects.update_or_create(
            **key,
            defaults={
                "first_name": first_name.strip()[:_NAME_MAX_LENGTH],
                "last_name": last_name.strip()[:_NAME_MAX_LENGTH],
                "created_at": now,
                "expires_at": now + dt.timedelta(seconds=PENDING_NAME_TTL_SECONDS),
            },
        )
    elif not (given and family):
        pending = PendingProviderName.objects.filter(**key, expires_at__gt=timezone.now()).first()
        if pending is not None:
            given = given or pending.first_name
            family = family or pending.last_name
    return given[:_NAME_MAX_LENGTH], family[:_NAME_MAX_LENGTH]


def _ticket_expired() -> ApiError:
    return ApiError(ErrorCode.SIGNUP_TICKET_EXPIRED, "Sign in again with a new code.")


def _valid_details(details: dict[str, str]) -> bool:
    names_ok = all(
        0 < len(details[field]) <= _NAME_MAX_LENGTH for field in ("first_name", "last_name")
    )
    return (
        names_ok
        and details["locale"] in SUPPORTED_LOCALES
        and _COUNTRY_PATTERN.match(details["country"]) is not None
        and _known_time_zone(details["time_zone"])
    )


def _known_time_zone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True
