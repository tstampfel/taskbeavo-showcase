"""Mobile number verification by SMS code (plan: "Sign-up and login").

Everyone verifies a mobile number before posting, offering or chatting
(`accounts.permissions`), which cuts fake accounts and lets on-site reminders work.

1. `request_phone_code(user, phone, client_ip)` normalises the number to E.164 and texts
   it a 6-digit code in the account's language, valid PHONE_CODE_TTL_SECONDS.
2. `verify_phone_code(user, code)` checks the account's latest code and, on a match,
   saves the number as verified. A number already verified on another account is
   refused with PHONE_NUMBER_IN_USE, after the code proved the person holds it, so the
   check can't be used to find out whose a number is.

Posters live anywhere (hard rule j), so any country's mobile numbers qualify, except
those in PHONE_DENIED_REGIONS and +1 countries with no numeric sender configured
(`core.sms.sender_for`): both get PHONE_COUNTRY_NOT_SUPPORTED.

Limits (SMS costs money and is the classic SMS-pumping target): a resend timer per
account; sliding one-hour caps per account, per client address (IPv6 per /64), per
number and per country; a daily cap on all code SMS, which reports to Sentry at 80%;
an hourly cap per account on checks; and a code burns after PHONE_CODE_MAX_ATTEMPTS
wrong guesses. Over a limit the API answers RATE_LIMITED with `retryAfterSeconds`. The
counters live in Redis; while it is down, sending and checking codes fail closed with
RATE_LIMITED rather than count per process. The per-number cap can be filled by anyone
who knows a number, which keeps its owner waiting up to an hour: the price of capping
SMS to one number.

The number is personal data (hard rule e): encrypted at rest, shown only to its owner
masked, and never put in a log, an error message, a span or a rate-limit key (those use
its keyed digest).
"""

import datetime as dt
import hmac
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass
from typing import cast

import phonenumbers
from django.conf import settings
from django.core.cache import caches
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext
from phonenumbers import PhoneNumberType
from redis.exceptions import RedisError

from accounts.limits import address_bucket, check_hourly, rate_limited, store
from accounts.models import PhoneCode, User
from config.graphql_limits import RATE_LIMIT_CACHE, incr_in_window
from core.crypto import pii_digest
from core.errors import ApiError, ErrorCode
from core.i18n import LocaleRecipient, in_recipient_locale
from core.sms import SmsSendError, mask_phone, send_sms, sender_for

logger = logging.getLogger(__name__)

_CODE_SALT = "taskbeavo.accounts.phone-code.v1"
_CODE_PATTERN = re.compile(r"^\d{6}$")
_DAY = 24 * 60 * 60
_MOBILE_TYPES = frozenset({PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE})
# Share of the daily SMS cap at which Sentry hears about it.
_DAILY_CAP_ALERT = 0.8


@dataclass(frozen=True)
class PhoneCodeSent:
    id: int  # the PhoneCode row's
    phone_masked: str
    expires_at: dt.datetime
    resend_available_at: dt.datetime


@dataclass(frozen=True)
class PhoneNumber:
    e164: str
    region: str  # ISO 3166-1 alpha-2
    sender: str  # who the SMS comes from (`core.sms.sender_for`)


def normalize_phone(raw: str, default_region: str | None = None) -> PhoneNumber:
    """The number in E.164 with its country and SMS sender, or INVALID_PHONE_NUMBER /
    PHONE_COUNTRY_NOT_SUPPORTED.

    International input ("+49 151 23456789", "0049 ...") is read as written; a national
    one ("091 234 5678") in `default_region`, normally the account's country. Only mobile
    numbers qualify.
    """
    text = raw.strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    try:
        number = phonenumbers.parse(text, default_region)
    except phonenumbers.NumberParseException:
        raise _invalid_number() from None
    if not phonenumbers.is_valid_number(number):
        raise _invalid_number()
    if phonenumbers.number_type(number) not in _MOBILE_TYPES:
        raise _invalid_number()
    region = phonenumbers.region_code_for_number(number) or ""
    sender = sender_for(region) if region else None
    if region in settings.PHONE_DENIED_REGIONS or sender is None:
        raise ApiError(
            ErrorCode.PHONE_COUNTRY_NOT_SUPPORTED, "SMS to this country isn't supported yet."
        )
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return PhoneNumber(e164=e164, region=region, sender=sender)


def masked_phone(user: User) -> str | None:
    """The account's verified number for its owner, masked: "+385 ••• 678"."""
    return mask_phone(user.phone_number) if user.phone_verified_at else None


def request_phone_code(user: User, phone: str, client_ip: str) -> PhoneCodeSent:
    """Text a fresh code to `phone` for `user`; once it is sent, the account's earlier
    unused codes stop working."""
    number = normalize_phone(phone, user.country or None)
    digest = pii_digest("phone", number.e164)
    # Started atomically before the caps, so two requests at once can't both pass it;
    # given back when a cap refuses, so a refused request doesn't hold the timer.
    _start_resend_timer(user)
    try:
        _check_send_caps(user, number.region, digest, client_ip)
    except ApiError:
        _clear_resend_timer(user)
        raise
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    expires_at = now + dt.timedelta(seconds=settings.PHONE_CODE_TTL_SECONDS)
    row = PhoneCode.objects.create(
        user=user,
        phone_number=number.e164,
        phone_digest=digest,
        code_hash=_code_hash(user, digest, code),
        created_at=now,
        expires_at=expires_at,
    )
    with in_recipient_locale(cast(LocaleRecipient, user)):
        text = gettext(
            "%(code)s is your TaskBeavo code. It expires in %(minutes)s minutes. "
            "Don't share it with anyone."
        ) % {"code": code, "minutes": settings.PHONE_CODE_TTL_SECONDS // 60}
    # Sent once the code is saved, in this request: the person is waiting for it.
    try:
        send_sms(number.e164, text, number.sender)
    except SmsSendError:
        # Reaches Sentry with SmsSendError's message, which holds no number by contract.
        logger.exception("Phone code SMS not sent")
        # The earlier code, possibly already on the phone, keeps working.
        row.delete()
        _clear_resend_timer(user)
        raise ApiError(ErrorCode.SMS_NOT_SENT, "The SMS couldn't be sent.") from None
    # Only older codes: a concurrent request's newer code stays valid.
    PhoneCode.objects.filter(user=user, used_at__isnull=True, pk__lt=row.pk).delete()
    return PhoneCodeSent(
        id=row.pk,
        phone_masked=mask_phone(number.e164),
        expires_at=expires_at,
        resend_available_at=now + dt.timedelta(seconds=settings.PHONE_CODE_RESEND_SECONDS),
    )


def verify_phone_code(user: User, code: str) -> User:
    """Check the account's latest code; on a match, save its number as verified.

    Raises INVALID_CODE for a wrong, expired, used or burnt code (a wrong guess still
    counts against the code), PHONE_NUMBER_IN_USE if another account verified the number.
    """
    check_hourly(f"phone-code-verify:user:{user.pk}", settings.PHONE_CODE_VERIFY_ATTEMPTS_PER_HOUR)
    now = timezone.now()
    in_use = False
    with transaction.atomic():
        row = (
            PhoneCode.objects.select_for_update()
            .filter(user=user, used_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        guess = code.strip()
        matched = (
            row is not None
            and _CODE_PATTERN.match(guess) is not None
            and hmac.compare_digest(row.code_hash, _code_hash(user, row.phone_digest, guess))
        )
        if row is not None:
            if matched:
                row.used_at = now
            else:
                row.failed_attempts += 1
                if row.failed_attempts >= settings.PHONE_CODE_MAX_ATTEMPTS:
                    row.used_at = now
            row.save(update_fields=["used_at", "failed_attempts"])
        if matched and row is not None:
            in_use = not _save_verified(user, row, now)
    # Raised after the block, so the failed attempt is counted.
    if not matched:
        raise ApiError(ErrorCode.INVALID_CODE, "The code is wrong or expired.")
    if in_use:
        raise ApiError(ErrorCode.PHONE_NUMBER_IN_USE, "The number is verified on another account.")
    return user


def _save_verified(user: User, row: PhoneCode, now: dt.datetime) -> bool:
    """Save the code's number on the account; False if another account has it."""
    if User.objects.filter(phone_digest=row.phone_digest).exclude(pk=user.pk).exists():
        return False
    user.phone_number = row.phone_number
    user.phone_digest = row.phone_digest
    user.phone_verified_at = now
    try:
        with transaction.atomic():
            user.save(update_fields=["phone_number", "phone_digest", "phone_verified_at"])
    except IntegrityError:
        # Another account verified the same number a moment ago.
        user.refresh_from_db(fields=["phone_number", "phone_digest", "phone_verified_at"])
        return False
    return True


def _code_hash(user: User, digest: str, code: str) -> str:
    # Bound to the account and the number, so a code only verifies what it was sent for.
    # django-types lacks salted_hmac's `algorithm` parameter.
    value = salted_hmac(_CODE_SALT, f"{user.pk}:{digest}:{code}", algorithm="sha256")  # pyright: ignore[reportCallIssue]
    return value.hexdigest()


def _resend_key(user: User) -> str:
    return f"phone-code-resend:user:{user.pk}"


def _start_resend_timer(user: User) -> None:
    """RATE_LIMITED if the account asked for a code less than PHONE_CODE_RESEND_SECONDS ago;
    otherwise starts that timer. One atomic `add`, so concurrent requests can't both pass."""
    wait = settings.PHONE_CODE_RESEND_SECONDS
    if wait <= 0:
        return
    now = int(time.time())
    # The value is when the timer ends, for the countdown of a refused request.
    if store(lambda cache: cache.add(_resend_key(user), now + wait, timeout=wait)):
        return
    ends_at = store(lambda cache: cache.get(_resend_key(user)))
    remaining = ends_at - now if isinstance(ends_at, int) else wait
    raise rate_limited(max(remaining, 1))


def _clear_resend_timer(user: User) -> None:
    try:
        caches[RATE_LIMIT_CACHE].delete(_resend_key(user))
    except RedisError as exc:
        logger.warning("Phone code limit store unavailable: %s", type(exc).__name__)


def _check_send_caps(user: User, region: str, digest: str, client_ip: str) -> None:
    """Count the SMS against each cap in turn; RATE_LIMITED at the first one it exceeds.

    Stopping there keeps a refused request off the later, wider counters.
    """
    country_cap = settings.PHONE_CODE_COUNTRY_HOURLY_CAPS.get(
        region, settings.PHONE_CODE_REQUESTS_PER_HOUR_PER_COUNTRY
    )
    check_hourly(f"phone-code-send:user:{user.pk}", settings.PHONE_CODE_REQUESTS_PER_HOUR_PER_USER)
    check_hourly(
        f"phone-code-send:ip:{address_bucket(client_ip)}",
        settings.PHONE_CODE_REQUESTS_PER_HOUR_PER_IP,
    )
    check_hourly(f"phone-code-send:phone:{digest}", settings.PHONE_CODE_REQUESTS_PER_HOUR_PER_PHONE)
    check_hourly(f"phone-code-send:country:{region}", country_cap)
    _check_daily_total()


def _check_daily_total() -> None:
    """The daily cap on all code SMS (UTC days), with an error to Sentry at 80%."""
    now = int(time.time())
    day = now // _DAY
    cap = settings.PHONE_CODE_SMS_PER_DAY
    sent = store(lambda cache: incr_in_window(cache, f"phone-code-send:day:{day}", 2 * _DAY))
    if sent == math.ceil(cap * _DAILY_CAP_ALERT):
        logger.error(
            "Phone code SMS reached 80%% of the daily cap",
            extra={"sms_sent_today": sent, "sms_daily_cap": cap},
        )
    if sent > cap:
        raise rate_limited((day + 1) * _DAY - now)


def _invalid_number() -> ApiError:
    return ApiError(ErrorCode.INVALID_PHONE_NUMBER, "Not a valid mobile number.")
