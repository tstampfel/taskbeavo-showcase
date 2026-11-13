import logging
from dataclasses import dataclass

import pytest
from django.utils import translation

from core.email import render_email
from core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, in_recipient_locale, recipient_locale


@dataclass
class Recipient:
    """Satisfies the `LocaleRecipient` contract; the personal fields must never be logged."""

    locale: str | None
    pk: int = 42
    first_name: str = "Ana"
    email: str = "ana@example.com"
    phone: str = "+385911234567"
    address: str = "Ulica od Puča 1, Dubrovnik"


PII = ("Ana", "ana@example.com", "+385911234567", "Ulica od Puča")


def warnings_from(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "core.i18n" and r.levelno == logging.WARNING]


def assert_no_pii(record: logging.LogRecord) -> None:
    logged = record.getMessage() + repr(record.__dict__)
    for value in PII:
        assert value not in logged


def test_supported_locales_are_hr_en_de_fr_with_hr_default() -> None:
    assert SUPPORTED_LOCALES == ("hr", "en", "de", "fr")
    assert DEFAULT_LOCALE == "hr"


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_supported_locale_is_used_without_a_warning(
    locale: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert recipient_locale(Recipient(locale=locale)) == locale
    assert warnings_from(caplog) == []


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("DE", "de"), ("de-AT", "de"), ("fr_CH", "fr"), (" en ", "en")],
)
def test_regional_and_mixed_case_values_map_to_the_language(stored: str, expected: str) -> None:
    assert recipient_locale(Recipient(locale=stored)) == expected


def test_german_locale_renders_german() -> None:
    email = render_email("welcome", Recipient(locale="de"), {"first_name": "Ana"})

    assert email.locale == "de"
    assert email.subject == "Willkommen bei TaskBeavo"
    assert "Hallo Ana!" in email.text


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_missing_locale_falls_back_to_croatian_and_warns(
    stored: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="core.i18n")

    email = render_email("welcome", Recipient(locale=stored), {"first_name": "Ana"})

    assert email.locale == "hr"
    assert email.subject == "Dobro došli u TaskBeavo"
    [record] = warnings_from(caplog)
    assert record.reason == "missing"  # type: ignore[attr-defined]
    assert record.recipient_type == "Recipient"  # type: ignore[attr-defined]
    assert record.recipient_id == 42  # type: ignore[attr-defined]
    assert_no_pii(record)


@pytest.mark.parametrize("stored", ["it", "xx-YY", "Ana"])
def test_unsupported_locale_falls_back_to_croatian_and_warns(
    stored: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="core.i18n")

    email = render_email("welcome", Recipient(locale=stored), {"first_name": "Ana"})

    assert email.locale == "hr"
    [record] = warnings_from(caplog)
    assert record.reason == "unsupported"  # type: ignore[attr-defined]
    assert record.recipient_type == "Recipient"  # type: ignore[attr-defined]
    assert record.recipient_id == 42  # type: ignore[attr-defined]
    # The stored value itself isn't logged either: it is free text on someone's record.
    assert_no_pii(record)


def test_never_uses_the_active_request_language() -> None:
    # The sender's language (active for the request) must not leak to the recipient.
    with translation.override("de"):
        assert recipient_locale(Recipient(locale="fr")) == "fr"
        assert recipient_locale(Recipient(locale=None)) == "hr"


def test_in_recipient_locale_activates_and_restores_the_language() -> None:
    with translation.override("en"):
        with in_recipient_locale(Recipient(locale="de")) as locale:
            assert locale == "de"
            assert translation.get_language() == "de"
        assert translation.get_language() == "en"
