"""Email-code sign-in and the account-details step (accounts/signin.py)."""

import datetime as dt
import io
import re
from typing import Any

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.utils import timezone

from accounts import signin
from accounts.models import EmailCode, RefreshToken, User
from accounts.sessions import IssuedTokens
from accounts.tasks import purge_expired
from accounts.tests.factories import TERMS_VERSION, make_user, sign_in
from core.errors import ApiError, ErrorCode
from legal.models import LegalDocumentWithdrawal, TermsAcceptance
from legal.tests.factories import publish_version

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("current_terms")]

EMAIL = "Ana.Kovac@Example.org"
ADDRESS = "ana.kovac@example.org"


def sent_code(index: int = -1) -> str:
    match = re.search(r"\b(\d{6})\b", mail.outbox[index].body)
    assert match is not None
    return match.group(1)


def wrong_for(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def raised(call: Any, *args: Any) -> ApiError:
    with pytest.raises(ApiError) as excinfo:
        call(*args)
    return excinfo.value


def html_of(message: mail.EmailMessage) -> str:
    assert isinstance(message, EmailMultiAlternatives)
    return str(message.alternatives[0][0])


def request_code(email: str = EMAIL, locale: str = "en") -> str:
    signin.request_email_code(email, locale)
    return sent_code()


def verify(code: str, email: str = EMAIL) -> IssuedTokens | signin.AccountDetailsRequired:
    return signin.verify_email_code(email, code, "web", "Firefox on Mac")


def error_code(excinfo: pytest.ExceptionInfo[ApiError]) -> ErrorCode:
    return excinfo.value.code


def details(ticket: str, **overrides: Any) -> dict[str, Any]:
    return {
        "ticket": ticket,
        "first_name": "Ana",
        "last_name": "Kovač",
        "is_adult": True,
        "locale": "de",
        "country": "de",
        "time_zone": "Europe/Berlin",
        "platform": "web",
        "accepted_terms_version": TERMS_VERSION,
        **overrides,
    }


def new_ticket() -> str:
    result = verify(request_code())
    assert isinstance(result, signin.AccountDetailsRequired)
    return result.ticket


def test_code_is_emailed_and_stored_only_as_a_hash() -> None:
    signin.request_email_code(EMAIL, "en")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    code = sent_code()
    assert message.to == [ADDRESS]
    assert message.subject == f"{code} is your TaskBeavo code"
    row = EmailCode.objects.get(email=ADDRESS)
    assert code not in row.code_hash
    assert row.expires_at - row.created_at == dt.timedelta(minutes=10)


def test_new_address_gets_the_code_in_the_clients_language() -> None:
    signin.request_email_code(EMAIL, "fr")

    assert mail.outbox[0].extra_headers["Content-Language"] == "fr"
    assert "Votre code de connexion" in html_of(mail.outbox[0])


def test_german_user_gets_the_code_email_in_german() -> None:
    """The account's saved language wins over the language of the requesting client."""
    make_user("hans", locale="de")

    signin.request_email_code("hans@example.com", "en")

    message = mail.outbox[0]
    assert message.extra_headers["Content-Language"] == "de"
    assert message.subject == f"{sent_code()} ist Ihr TaskBeavo-Code"
    assert '<html lang="de">' in html_of(message)


def test_invalid_email_is_rejected() -> None:
    with pytest.raises(ApiError) as excinfo:
        signin.request_email_code("not-an-email", "en")

    assert error_code(excinfo) is ErrorCode.INVALID_EMAIL
    assert not mail.outbox


def test_new_address_gets_a_ticket_and_no_user_row() -> None:
    result = verify(request_code())

    assert isinstance(result, signin.AccountDetailsRequired)
    assert result.email == ADDRESS
    assert not User.objects.exists()


def test_code_works_once() -> None:
    code = request_code()
    verify(code)

    with pytest.raises(ApiError) as excinfo:
        verify(code)

    assert error_code(excinfo) is ErrorCode.INVALID_CODE


def test_expired_code_is_refused() -> None:
    code = request_code()
    EmailCode.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

    with pytest.raises(ApiError) as excinfo:
        verify(code)

    assert error_code(excinfo) is ErrorCode.INVALID_CODE


def test_a_new_code_replaces_the_earlier_one() -> None:
    first = request_code()
    second = request_code()
    if first == second:
        pytest.skip("both random codes happened to match")

    with pytest.raises(ApiError):
        verify(first)
    assert isinstance(verify(second), signin.AccountDetailsRequired)


def test_code_is_burnt_after_too_many_wrong_tries(settings) -> None:
    settings.EMAIL_CODE_MAX_ATTEMPTS = 3
    settings.EMAIL_CODE_LOCKOUT_AFTER_FAILURES = 10
    code = request_code()
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(3):
        with pytest.raises(ApiError):
            verify(wrong)

    with pytest.raises(ApiError) as excinfo:
        verify(code)

    assert error_code(excinfo) is ErrorCode.INVALID_CODE
    assert EmailCode.objects.get().failed_attempts == 3


def test_code_for_another_address_is_refused() -> None:
    code = request_code()

    with pytest.raises(ApiError) as excinfo:
        verify(code, email="someone.else@example.com")

    assert error_code(excinfo) is ErrorCode.INVALID_CODE


def test_existing_account_signs_in_with_the_code() -> None:
    user = make_user("ana")
    result = verify(request_code("ana@example.com"), email="ana@example.com")

    assert isinstance(result, IssuedTokens)
    assert result.session.user == user
    assert result.session.platform == "web"
    assert result.session.device_name == "Firefox on Mac"


def test_deactivated_account_cannot_sign_in() -> None:
    make_user("ana", is_active=False)

    with pytest.raises(ApiError) as excinfo:
        verify(request_code("ana@example.com"), email="ana@example.com")

    assert error_code(excinfo) is ErrorCode.INVALID_CODE


def test_account_details_create_the_user_and_a_session() -> None:
    issued = signin.complete_account_details(**details(new_ticket()))

    user = User.objects.get()
    assert issued.session.user == user
    assert user.email == ADDRESS
    assert (user.first_name, user.last_name) == ("Ana", "Kovač")
    assert (user.locale, user.country, user.time_zone) == ("de", "DE", "Europe/Berlin")
    assert user.adult_confirmed_at is not None
    assert not user.has_usable_password()


def test_under_18_is_rejected_and_no_user_is_created() -> None:
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(new_ticket(), is_adult=False))

    assert error_code(excinfo) is ErrorCode.UNDERAGE
    assert not User.objects.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": "  "},
        {"last_name": "x" * 101},
        {"country": "Germany"},
        {"time_zone": "Mars/Olympus"},
        {"time_zone": ""},
        {"locale": "es"},
    ],
)
def test_invalid_details_are_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(new_ticket(), **overrides))

    assert error_code(excinfo) is ErrorCode.INVALID_ACCOUNT_DETAILS
    assert not User.objects.exists()


@pytest.mark.parametrize("ticket", ["", "forged", "eyJlbWFpbCI6ImFAYi5jIn0:forged:sig"])
def test_forged_ticket_is_rejected(ticket: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(ticket))

    assert error_code(excinfo) is ErrorCode.SIGNUP_TICKET_EXPIRED


def test_expired_ticket_is_rejected(settings) -> None:
    ticket = new_ticket()
    settings.SIGNUP_TICKET_MAX_AGE_SECONDS = -1

    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(ticket))

    assert error_code(excinfo) is ErrorCode.SIGNUP_TICKET_EXPIRED


def test_ticket_cannot_create_a_second_account() -> None:
    ticket = new_ticket()
    signin.complete_account_details(**details(ticket))

    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(ticket, first_name="Eve"))

    assert error_code(excinfo) is ErrorCode.SIGNUP_TICKET_EXPIRED
    assert User.objects.get().first_name == "Ana"


def test_sign_up_records_one_terms_acceptance_in_the_users_language() -> None:
    issued = signin.complete_account_details(**details(new_ticket(), platform="android"))

    acceptance = TermsAcceptance.objects.get()
    assert acceptance.user == issued.session.user
    document = acceptance.document
    assert (document.kind, document.version, document.language) == ("TERMS", TERMS_VERSION, "de")
    assert (acceptance.platform, acceptance.sign_in_method) == ("android", "email")
    assert acceptance.accepted_at <= timezone.now()


@pytest.mark.parametrize("version", [None, "", "2026-10", " 2026-11"])
def test_sign_up_without_the_current_terms_is_rejected(version: str | None) -> None:
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(new_ticket(), accepted_terms_version=version))

    assert error_code(excinfo) is ErrorCode.TERMS_NOT_ACCEPTED
    assert not User.objects.exists()
    assert not TermsAcceptance.objects.exists()


def test_a_version_that_has_been_replaced_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    later = timezone.now() + dt.timedelta(minutes=1)
    publish_version(version="2026-12", effective_at=later)
    ticket = new_ticket()

    # Scheduled, not yet in force: accepting it early doesn't count.
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(ticket, accepted_terms_version="2026-12"))
    assert error_code(excinfo) is ErrorCode.TERMS_NOT_ACCEPTED

    # Once it takes effect, the version before it is stale.
    monkeypatch.setattr(timezone, "now", lambda: later + dt.timedelta(seconds=1))
    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(ticket))
    assert error_code(excinfo) is ErrorCode.TERMS_NOT_ACCEPTED

    signin.complete_account_details(**details(ticket, accepted_terms_version="2026-12"))

    assert TermsAcceptance.objects.get().document.version == "2026-12"


def test_no_account_is_created_while_no_terms_are_in_force() -> None:
    LegalDocumentWithdrawal.objects.create(kind="TERMS", version=TERMS_VERSION, reason="test")

    with pytest.raises(ApiError) as excinfo:
        signin.complete_account_details(**details(new_ticket()))

    assert error_code(excinfo) is ErrorCode.TERMS_NOT_ACCEPTED
    assert not User.objects.exists()


def test_signing_in_to_an_existing_account_records_no_acceptance() -> None:
    signin.complete_account_details(**details(new_ticket()))

    assert isinstance(verify(request_code()), IssuedTokens)

    assert TermsAcceptance.objects.count() == 1


def test_purge_deletes_codes_and_refresh_tokens_a_day_past_expiry() -> None:
    request_code()
    sign_in(make_user("ana"))
    long_ago = timezone.now() - dt.timedelta(days=2)
    EmailCode.objects.update(expires_at=long_ago)
    RefreshToken.objects.update(expires_at=long_ago)
    request_code("fresh@example.com")
    sign_in(make_user("marko"))

    assert purge_expired() == 2
    assert list(EmailCode.objects.values_list("email", flat=True)) == ["fresh@example.com"]
    assert RefreshToken.objects.count() == 1


# --- Limits and lockout (BEAV-76) ----------------------------------------------------


def test_five_code_requests_per_address_per_hour() -> None:
    for _ in range(5):
        signin.request_email_code(EMAIL, "en", "203.0.113.1")

    error = raised(signin.request_email_code, EMAIL, "en", "203.0.113.2")

    assert error.code is ErrorCode.RATE_LIMITED
    assert error.extensions is not None and 0 < error.extensions["retryAfterSeconds"] <= 3600
    assert len(mail.outbox) == 5


def test_the_address_limit_ignores_spelling() -> None:
    for spelling in [
        "Ana.Kovac@Example.org",
        "ana.kovac@example.org ",
        " ANA.KOVAC@EXAMPLE.ORG",
    ] * 2:
        try:
            signin.request_email_code(spelling, "en", "203.0.113.1")
        except ApiError as error:
            assert error.code is ErrorCode.RATE_LIMITED
    assert len(mail.outbox) == 5


def test_code_requests_are_limited_per_client_address(settings) -> None:
    settings.EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP = 3
    for i in range(3):
        signin.request_email_code(f"user{i}@example.com", "en", "2001:db8:1:2::1")

    # Another address in the same IPv6 /64 counts as the same client.
    error = raised(signin.request_email_code, "user9@example.com", "en", "2001:db8:1:2::ffff")

    assert error.code is ErrorCode.RATE_LIMITED
    signin.request_email_code("user9@example.com", "en", "203.0.113.7")


def test_a_request_refused_for_its_client_uses_none_of_the_addresses_quota(settings) -> None:
    settings.EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP = 1
    signin.request_email_code("someone@example.com", "en", "198.51.100.9")
    for _ in range(10):
        error = raised(signin.request_email_code, "ana@example.com", "en", "198.51.100.9")
        assert error.code is ErrorCode.RATE_LIMITED

    # The owner, elsewhere, still has all five requests of the hour.
    for i in range(5):
        signin.request_email_code("ana@example.com", "en", f"203.0.113.{i}")


def test_limits_answer_the_same_for_known_and_unknown_addresses() -> None:
    make_user("ana")
    answers = []
    for email in ["ana@example.com", "nobody@example.com"]:
        for _ in range(5):
            signin.request_email_code(email, "en", "203.0.113.1")
        answers.append(raised(signin.request_email_code, email, "en", "203.0.113.1").extensions)

    assert answers[0] is not None and answers[1] is not None
    assert answers[0]["code"] == answers[1]["code"] == "RATE_LIMITED"


def verify_from(
    code: str, client_ip: str, device_token: str | None = None, email: str = EMAIL
) -> IssuedTokens | signin.AccountDetailsRequired:
    return signin.verify_email_code(email, code, "web", "Firefox on Mac", client_ip, device_token)


def test_five_wrong_codes_block_their_source_for_30_minutes() -> None:
    code = request_code()
    for _ in range(4):
        assert raised(verify, wrong_for(code)).code is ErrorCode.INVALID_CODE

    fifth = raised(verify, wrong_for(code))
    # Blocked from here: even the right code is refused.
    right = raised(verify, code)

    assert fifth.extensions == {"code": "LOCKED", "retryAfterSeconds": 1800}
    assert right.code is ErrorCode.LOCKED
    assert right.extensions is not None and 1790 <= right.extensions["retryAfterSeconds"] <= 1800


def test_an_attacker_is_blocked_while_the_owner_signs_in_from_their_device() -> None:
    """Decision 2026-11-04: lock the attacker, not the user."""
    from accounts import sessions

    user = make_user("ana")
    phone = sessions.start_session(user, "ios", "iPhone")
    code = request_code("ana@example.com")
    for _ in range(5):
        raised(verify_from, wrong_for(code), "198.51.100.66", None, "ana@example.com")
    # Made-up device tokens don't make the attacker a new source.
    blocked = raised(verify_from, code, "198.51.100.66", "x" * 43, "ana@example.com")
    assert blocked.code is ErrorCode.LOCKED

    # The owner, on the same network even, with a fresh code and their phone's token.
    fresh = request_code("ana@example.com")
    issued = verify_from(fresh, "198.51.100.66", phone.device_token, "ana@example.com")
    assert isinstance(issued, IssuedTokens) and issued.session.user == user
    # And from another network without a token.
    assert isinstance(
        verify_from(request_code("ana@example.com"), "203.0.113.5", None, "ana@example.com"),
        IssuedTokens,
    )


def test_twenty_wrong_codes_an_hour_from_all_sources_lock_the_address(settings) -> None:
    settings.EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR = 20
    code = request_code()
    for i in range(19):
        assert raised(verify_from, wrong_for(code), f"198.51.100.{i}").code in (
            ErrorCode.INVALID_CODE,
        )

    twentieth = raised(verify_from, wrong_for(code), "198.51.100.99")
    new_code = raised(signin.request_email_code, EMAIL, "en", "203.0.113.5")
    elsewhere = raised(verify_from, code, "203.0.113.5")

    assert twentieth.extensions == {"code": "LOCKED", "retryAfterSeconds": 1800}
    assert new_code.code is elsewhere.code is ErrorCode.LOCKED
    assert not EmailCode.objects.filter(used_at__isnull=True).exists()


def test_a_new_code_does_not_reset_the_guess_budget() -> None:
    for _ in range(4):
        code = request_code()
        assert raised(verify, wrong_for(code)).code is ErrorCode.INVALID_CODE

    code = request_code()

    assert raised(verify, wrong_for(code)).code is ErrorCode.LOCKED


def test_the_lockout_ends_after_30_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    code = request_code()
    for _ in range(5):
        raised(verify, wrong_for(code))
    now = signin.time.time()
    # time.time is also the test cache's clock, so the lock key expires as in Redis.
    monkeypatch.setattr(signin.time, "time", lambda: now + 1801)

    assert isinstance(verify(request_code()), signin.AccountDetailsRequired)


def test_a_lock_that_ends_inside_the_hour_gives_no_fresh_guesses(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR = 20
    code = request_code()
    for i in range(20):
        raised(verify_from, wrong_for(code), f"198.51.100.{i}")
    now = signin.time.time()
    monkeypatch.setattr(signin.time, "time", lambda: now + 1801)

    # 30 minutes on, the address opens again, but its hour's 20 are spent.
    code = request_code()
    again = raised(verify_from, wrong_for(code), "203.0.113.50")

    assert again.code is ErrorCode.LOCKED


def test_a_right_code_clears_the_wrong_guesses() -> None:
    code = request_code()
    for _ in range(4):
        raised(verify, wrong_for(code))
    verify(code)

    code = request_code()
    for _ in range(4):
        assert raised(verify, wrong_for(code)).code is ErrorCode.INVALID_CODE


def test_lockout_answers_the_same_for_known_and_unknown_addresses() -> None:
    make_user("ana")
    answers = []
    for email in ["ana@example.com", "nobody@example.com"]:
        code = request_code(email)
        errors = [raised(verify, wrong_for(code), email) for _ in range(5)]
        answers.append(errors[-1].extensions)

    assert answers[0] == answers[1] == {"code": "LOCKED", "retryAfterSeconds": 1800}


def test_wrong_guesses_without_a_code_count_too() -> None:
    errors = [raised(verify, "123456") for _ in range(5)]

    assert errors[-1].code is ErrorCode.LOCKED


def test_support_can_lift_a_lockout(settings) -> None:
    settings.EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR = 6
    code = request_code()
    for _ in range(5):
        raised(verify, wrong_for(code))  # blocks this source
    raised(verify_from, wrong_for(code), "198.51.100.1")  # locks the address
    out = io.StringIO()

    call_command("unlock_email_sign_in", EMAIL, stdout=out)

    assert out.getvalue().strip() == "Unlocked."
    assert ADDRESS not in out.getvalue()
    assert isinstance(verify(request_code()), signin.AccountDetailsRequired)
