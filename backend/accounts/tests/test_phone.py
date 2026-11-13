"""Phone verification by SMS code, end to end over HTTP at /graphql."""

import datetime as dt
import io
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone
from redis.exceptions import RedisError

from accounts import limits, phone
from accounts.models import PhoneCode, User
from accounts.tasks import purge_expired
from accounts.tests.factories import access_token, make_user
from accounts.tests.test_schema import error_codes
from core import sms
from core.crypto import pii_digest
from core.errors import ApiError
from core.sms import SmsSendError

pytestmark = pytest.mark.django_db

DE_MOBILE = "+4915123456789"
HR_MOBILE = "+385921234567"
REQUEST = """
mutation($phone: String!) {
  requestPhoneCode(phone: $phone) { id phoneNumberMasked expiresAt resendAvailableAt }
}
"""
VERIFY = """
mutation($code: String!) {
  verifyPhoneCode(code: $code) { id phoneVerified phoneNumberMasked }
}
"""


def gql(
    client: Client,
    query: str,
    variables: dict[str, Any] | None = None,
    token: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(
        "/graphql",
        {"query": query, "variables": variables or {}},
        content_type="application/json",
        headers=headers,
        **extra,
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture(autouse=True)
def _empty_sms_outbox() -> Iterator[None]:
    sms.outbox.clear()
    yield
    sms.outbox.clear()


@pytest.fixture
def no_resend_timer() -> Iterator[None]:
    with override_settings(PHONE_CODE_RESEND_SECONDS=0):
        yield


def request(client: Client, token: str, phone: str, **extra: Any) -> dict[str, Any]:
    return gql(client, REQUEST, {"phone": phone}, token=token, **extra)


def last_code() -> str:
    match = re.search(r"\b(\d{6})\b", sms.outbox[-1].text)
    assert match is not None
    return match.group(1)


def verify(client: Client, token: str, code: str) -> dict[str, Any]:
    return gql(client, VERIFY, {"code": code}, token=token)


def test_request_and_verify_saves_the_number_encrypted(client: Client) -> None:
    user = make_user("ana", locale="de", country="DE")
    token = access_token(user)

    sent = request(client, token, "+49 151 2345 6789")["data"]["requestPhoneCode"]

    assert sent["id"]
    assert sent["phoneNumberMasked"] == "+49 ••• 789"
    assert len(sms.outbox) == 1
    assert sms.outbox[0].to == DE_MOBILE
    # In the account's language.
    assert "ist Ihr TaskBeavo-Code" in sms.outbox[0].text

    body = verify(client, token, last_code())

    assert body["data"]["verifyPhoneCode"] == {
        "id": str(user.pk),
        "phoneVerified": True,
        "phoneNumberMasked": "+49 ••• 789",
    }
    user.refresh_from_db()
    assert user.phone_number == DE_MOBILE
    assert user.phone_verified_at is not None
    with connection.cursor() as cursor:
        cursor.execute("SELECT phone_number FROM accounts_user WHERE id = %s", [user.pk])
        row = cursor.fetchone()
    assert row is not None
    stored = row[0]
    assert DE_MOBILE not in stored
    assert DE_MOBILE.removeprefix("+49") not in stored  # the national number


def test_national_numbers_are_read_in_the_account_country(client: Client) -> None:
    token = access_token(make_user("marko", country="HR"))

    request(client, token, "092 123 4567")

    assert sms.outbox[-1].to == HR_MOBILE
    assert "je vaš TaskBeavo kôd" in sms.outbox[-1].text


@pytest.mark.parametrize(
    ("phone", "code"),
    [
        ("not a number", "INVALID_PHONE_NUMBER"),
        ("+49 151", "INVALID_PHONE_NUMBER"),
        # A Zagreb landline can't receive SMS.
        ("+385 1 2345 678", "INVALID_PHONE_NUMBER"),
        # US and Canadian carriers drop the alphanumeric sender: refused, not sent in vain.
        ("+1 201 555 0123", "PHONE_COUNTRY_NOT_SUPPORTED"),
    ],
)
def test_bad_numbers_are_refused_before_any_sms(client: Client, phone: str, code: str) -> None:
    body = request(client, access_token(make_user("ana")), phone)

    assert error_codes(body) == [code]
    assert sms.outbox == []
    assert not PhoneCode.objects.exists()


def test_signed_out_gets_unauthenticated(client: Client) -> None:
    assert error_codes(gql(client, REQUEST, {"phone": HR_MOBILE})) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, VERIFY, {"code": "123456"})) == ["UNAUTHENTICATED"]


def test_wrong_code_gets_invalid_code_and_burns_after_five(client: Client) -> None:
    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(5):
        assert error_codes(verify(client, token, wrong)) == ["INVALID_CODE"]

    # The right code no longer works: it burnt.
    assert error_codes(verify(client, token, code)) == ["INVALID_CODE"]
    user.refresh_from_db()
    assert user.phone_verified_at is None


def test_expired_and_used_codes_get_invalid_code(client: Client, no_resend_timer: None) -> None:
    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    PhoneCode.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    assert error_codes(verify(client, token, last_code())) == ["INVALID_CODE"]

    request(client, token, HR_MOBILE)
    code = last_code()
    assert "errors" not in verify(client, token, code)
    assert error_codes(verify(client, token, code)) == ["INVALID_CODE"]


def test_a_new_request_replaces_the_earlier_code(client: Client, no_resend_timer: None) -> None:
    token = access_token(make_user("ana"))
    request(client, token, HR_MOBILE)
    first = last_code()
    request(client, token, DE_MOBILE)
    second = last_code()

    if first != second:
        assert error_codes(verify(client, token, first)) == ["INVALID_CODE"]
    body = verify(client, token, second)

    assert body["data"]["verifyPhoneCode"]["phoneNumberMasked"] == "+49 ••• 789"


def test_a_code_only_verifies_its_own_account(client: Client) -> None:
    ana, marko = make_user("ana"), make_user("marko")
    request(client, access_token(ana), HR_MOBILE)

    body = verify(client, access_token(marko), last_code())

    assert error_codes(body) == ["INVALID_CODE"]


def test_a_number_verified_on_another_account_is_refused(
    client: Client, no_resend_timer: None
) -> None:
    ana, marko = make_user("ana"), make_user("marko")
    ana_token, marko_token = access_token(ana), access_token(marko)
    request(client, ana_token, HR_MOBILE)
    verify(client, ana_token, last_code())

    # Sending the code reveals nothing: only the person holding the phone gets further.
    assert "errors" not in request(client, marko_token, HR_MOBILE)
    body = verify(client, marko_token, last_code())

    assert error_codes(body) == ["PHONE_NUMBER_IN_USE"]
    marko.refresh_from_db()
    assert marko.phone_verified_at is None
    assert marko.phone_number == ""


def test_resend_timer_answers_rate_limited_with_retry_after(client: Client) -> None:
    token = access_token(make_user("ana"))
    request(client, token, HR_MOBILE)

    body = request(client, token, HR_MOBILE)

    assert error_codes(body) == ["RATE_LIMITED"]
    assert 0 < body["errors"][0]["extensions"]["retryAfterSeconds"] <= 60
    assert len(sms.outbox) == 1


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_USER=2)
def test_hourly_limit_per_account(client: Client, no_resend_timer: None) -> None:
    token = access_token(make_user("ana"))
    request(client, token, HR_MOBILE)
    request(client, token, DE_MOBILE)

    body = request(client, token, "+43664123456")

    assert error_codes(body) == ["RATE_LIMITED"]
    assert body["errors"][0]["extensions"]["retryAfterSeconds"] <= 3600
    assert len(sms.outbox) == 2


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_PHONE=2)
def test_hourly_limit_per_number_across_accounts(client: Client) -> None:
    for name in ("ana", "marko"):
        request(client, access_token(make_user(name)), HR_MOBILE)

    body = request(client, access_token(make_user("iva")), "092 123 4567")

    assert error_codes(body) == ["RATE_LIMITED"]
    assert len(sms.outbox) == 2


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_IP=2)
def test_hourly_limit_per_client_address(client: Client) -> None:
    request(client, access_token(make_user("ana")), HR_MOBILE)
    request(client, access_token(make_user("marko")), DE_MOBILE)

    body = request(client, access_token(make_user("iva")), "+43664123456")
    elsewhere = request(
        client, access_token(make_user("luka")), "+43664123456", REMOTE_ADDR="10.9.9.9"
    )

    assert error_codes(body) == ["RATE_LIMITED"]
    assert "errors" not in elsewhere


@override_settings(PHONE_CODE_VERIFY_ATTEMPTS_PER_HOUR=3)
def test_hourly_limit_on_checks(client: Client) -> None:
    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(2):
        verify(client, token, wrong)

    assert "errors" not in verify(client, token, code)
    assert error_codes(verify(client, token, code)) == ["RATE_LIMITED"]


def test_provider_failure_gets_sms_not_sent_and_can_retry(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(message: sms.SmsMessage) -> None:
        raise SmsSendError("Infobip answered HTTP 500")

    token = access_token(make_user("ana"))
    monkeypatch.setattr(sms.LocmemSmsBackend, "send", lambda self, message: refuse(message))

    assert error_codes(request(client, token, HR_MOBILE)) == ["SMS_NOT_SENT"]
    assert not PhoneCode.objects.exists()

    monkeypatch.undo()
    # The resend timer was given back, so the person can try again at once.
    assert "errors" not in request(client, token, HR_MOBILE)


def test_the_number_never_reaches_logs_or_errors(
    client: Client, caplog: pytest.LogCaptureFixture, no_resend_timer: None
) -> None:
    token = access_token(make_user("ana"))
    with caplog.at_level(logging.DEBUG):
        bodies = [
            request(client, token, HR_MOBILE),
            request(client, token, "+385 1 2345 678"),
            verify(client, token, "000000"),
        ]

    for text in [caplog.text, *map(str, bodies)]:
        assert "921234567" not in text
        assert "12345678" not in text


def test_viewer_shows_only_a_masked_number(client: Client) -> None:
    user = make_user("ana")
    token = access_token(user)
    query = "{ viewer { phoneVerified phoneNumberMasked } }"
    assert gql(client, query, token=token)["data"]["viewer"] == {
        "phoneVerified": False,
        "phoneNumberMasked": None,
    }
    request(client, token, HR_MOBILE)
    verify(client, token, last_code())

    assert gql(client, query, token=token)["data"]["viewer"] == {
        "phoneVerified": True,
        "phoneNumberMasked": "+385 ••• 567",
    }


def test_the_schema_has_no_field_for_the_full_number() -> None:
    from config.schema import schema

    sdl = schema.as_str()
    assert "phoneNumber:" not in sdl
    assert "phoneNumber " not in sdl


def test_purge_deletes_codes_a_day_past_expiry(client: Client) -> None:
    user = make_user("ana")
    request(client, access_token(user), HR_MOBILE)
    PhoneCode.objects.update(expires_at=timezone.now() - dt.timedelta(days=2))

    purge_expired()

    assert not PhoneCode.objects.exists()
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.parametrize("number", ["+54 9 11 2345 6789", "+27 71 123 4567"])
def test_diaspora_numbers_anywhere_verify(client: Client, number: str) -> None:
    """Posters live anywhere (hard rule j): Argentina and South Africa get SMS too."""
    token = access_token(make_user("ana", country="AR"))

    assert "errors" not in request(client, token, number)
    body = verify(client, token, last_code())

    assert body["data"]["verifyPhoneCode"]["phoneVerified"] is True
    assert sms.outbox[-1].sender == "TaskBeavo"


@override_settings(INFOBIP_SENDER_BY_REGION={"US": "+15550100"})
def test_north_american_numbers_get_sms_once_a_numeric_sender_is_set(client: Client) -> None:
    token = access_token(make_user("ana", country="US"))

    assert "errors" not in request(client, token, "+1 201 555 0123")
    assert sms.outbox[-1].sender == "+15550100"
    assert error_codes(request(client, access_token(make_user("lea")), "+1 204 555 0123")) == [
        "PHONE_COUNTRY_NOT_SUPPORTED"
    ]


@override_settings(PHONE_DENIED_REGIONS=frozenset({"ZA"}))
def test_a_denied_country_is_refused(client: Client) -> None:
    body = request(client, access_token(make_user("ana")), "+27 71 123 4567")

    assert error_codes(body) == ["PHONE_COUNTRY_NOT_SUPPORTED"]
    assert sms.outbox == []


@override_settings(
    PHONE_CODE_REQUESTS_PER_HOUR_PER_COUNTRY=2, PHONE_CODE_COUNTRY_HOURLY_CAPS={"HR": 50}
)
def test_hourly_cap_per_country(client: Client) -> None:
    za = ["+27711234567", "+27711234568", "+27711234569"]
    bodies = [
        request(client, access_token(make_user(f"user{i}")), number, REMOTE_ADDR=f"10.0.0.{i}")
        for i, number in enumerate(za)
    ]
    croatian = request(client, access_token(make_user("marko")), HR_MOBILE)

    assert ["errors" in body for body in bodies] == [False, False, True]
    assert error_codes(bodies[2]) == ["RATE_LIMITED"]
    # A busy country has its own, wider cap.
    assert "errors" not in croatian


@override_settings(PHONE_CODE_SMS_PER_DAY=5)
def test_daily_cap_on_all_sms_warns_sentry_at_80_percent(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    numbers = [f"+38592123456{i}" for i in range(6)]
    with caplog.at_level(logging.ERROR, logger="accounts.phone"):
        bodies = [
            request(client, access_token(make_user(f"user{i}")), number, REMOTE_ADDR=f"10.0.1.{i}")
            for i, number in enumerate(numbers)
        ]

    assert [error_codes(body) for body in bodies] == [[], [], [], [], [], ["RATE_LIMITED"]]
    assert len(sms.outbox) == 5
    [alert] = [r for r in caplog.records if "80%" in r.getMessage()]
    assert alert.sms_sent_today == 4  # pyright: ignore[reportAttributeAccessIssue]
    assert "92123456" not in caplog.text


def test_a_failed_resend_keeps_the_earlier_code(
    client: Client, monkeypatch: pytest.MonkeyPatch, no_resend_timer: None
) -> None:
    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    first = last_code()

    def refuse(self: object, message: sms.SmsMessage) -> None:
        raise SmsSendError("Infobip answered HTTP 503")

    monkeypatch.setattr(sms.LocmemSmsBackend, "send", refuse)
    assert error_codes(request(client, token, HR_MOBILE)) == ["SMS_NOT_SENT"]

    assert "errors" not in verify(client, token, first)


def test_a_successful_resend_retires_the_earlier_code(
    client: Client, no_resend_timer: None
) -> None:
    token = access_token(make_user("ana"))
    request(client, token, HR_MOBILE)
    request(client, token, HR_MOBILE)

    assert PhoneCode.objects.filter(used_at__isnull=True).count() == 1


def test_codes_fail_closed_while_redis_is_down(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        def __getattr__(self, name: str) -> object:
            def fail(*args: object, **kwargs: object) -> None:
                raise RedisError("down")

            return fail

    token = access_token(make_user("ana"))
    monkeypatch.setattr(phone, "caches", {"graphql_rate_limit": Down()})
    monkeypatch.setattr(limits, "caches", {"graphql_rate_limit": Down()})

    sent = request(client, token, HR_MOBILE)
    checked = verify(client, token, "123456")

    assert error_codes(sent) == error_codes(checked) == ["RATE_LIMITED"]
    assert sent["errors"][0]["extensions"]["retryAfterSeconds"] == 60
    assert sms.outbox == []


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_IP=2)
def test_ipv6_addresses_count_per_64(client: Client) -> None:
    numbers = [HR_MOBILE, DE_MOBILE, "+43664123456"]
    addresses = ["2001:db8:1:2::1", "2001:db8:1:2::ffff", "2001:db8:1:2:abcd::9"]
    bodies = [
        request(client, access_token(make_user(f"user{i}")), number, REMOTE_ADDR=address)
        for i, (number, address) in enumerate(zip(numbers, addresses, strict=True))
    ]

    assert error_codes(bodies[2]) == ["RATE_LIMITED"]


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_USER=5)
def test_hourly_caps_slide_across_the_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    hour = (int(time.time()) // 3600 + 1) * 3600
    cache = caches["graphql_rate_limit"]
    # Five calls late in the previous clock hour ...
    cache.set(f"phone-code-send:user:1:{hour // 3600 - 1}", 5, timeout=7200)
    # ... still count a minute into the next one.
    monkeypatch.setattr(limits.time, "time", lambda: hour + 60)

    with pytest.raises(ApiError) as raised:
        limits.check_hourly("phone-code-send:user:1", 5)

    # Once a fifth of that hour has slid out of the window, at 12 minutes past.
    assert raised.value.extensions == {"code": "RATE_LIMITED", "retryAfterSeconds": 660}


def test_recompute_digests_after_a_key_change(client: Client, settings: Any) -> None:
    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    verify(client, token, last_code())

    settings.PII_DIGEST_KEY = "a-new-digest-key-not-secret"
    out = io.StringIO()
    call_command("recompute_pii_digests", stdout=out)

    user.refresh_from_db()
    assert user.phone_digest == pii_digest("phone", HR_MOBILE)
    assert out.getvalue().startswith("1 phone digests recomputed")
    assert "921234567" not in out.getvalue()


def test_reencrypt_fields_moves_values_to_the_first_key(client: Client, settings: Any) -> None:
    from cryptography.fernet import Fernet

    from core import crypto

    user = make_user("ana")
    token = access_token(user)
    request(client, token, HR_MOBILE)
    verify(client, token, last_code())
    old = settings.FIELD_ENCRYPTION_KEY
    new = Fernet.generate_key().decode()

    settings.FIELD_ENCRYPTION_KEY = f"{new},{old}"
    crypto._fernet.cache_clear()
    call_command("reencrypt_fields", stdout=io.StringIO())
    settings.FIELD_ENCRYPTION_KEY = new
    crypto._fernet.cache_clear()

    user.refresh_from_db()
    assert user.phone_number == HR_MOBILE
    crypto._fernet.cache_clear()


def test_the_resend_timer_starts_atomically() -> None:
    """Two requests at once: only the first passes the timer."""
    user = make_user("ana")
    phone._start_resend_timer(user)

    with pytest.raises(ApiError) as raised:
        phone._start_resend_timer(user)

    assert raised.value.extensions is not None
    assert raised.value.extensions["code"] == "RATE_LIMITED"
    assert 0 < raised.value.extensions["retryAfterSeconds"] <= 60


@override_settings(PHONE_CODE_REQUESTS_PER_HOUR_PER_PHONE=1)
def test_a_request_refused_by_a_cap_gives_the_timer_back(client: Client) -> None:
    other = access_token(make_user("marko"))
    request(client, other, HR_MOBILE)
    token = access_token(make_user("ana"))

    assert error_codes(request(client, token, HR_MOBILE)) == ["RATE_LIMITED"]
    # Not held by the resend timer: another number goes through at once.
    assert "errors" not in request(client, token, DE_MOBILE)
