from dataclasses import dataclass

import pytest
from django.utils import translation

from core.email import RenderedEmail, render_email


@dataclass
class Recipient:
    locale: str | None


# Text the sample (welcome) email and the base layout must show in each language.
EXPECTED = {
    "hr": {
        "subject": "Dobro došli u TaskBeavo",
        "greeting": "Pozdrav, Ana!",
        "footer": "Ovu e-poruku primate jer imate TaskBeavo račun.",
    },
    "en": {
        "subject": "Welcome to TaskBeavo",
        "greeting": "Hello, Ana!",
        "footer": "You're receiving this email because you have a TaskBeavo account.",
    },
    "de": {
        "subject": "Willkommen bei TaskBeavo",
        "greeting": "Hallo Ana!",
        "footer": "Sie erhalten diese E-Mail, weil Sie ein TaskBeavo-Konto haben.",
    },
    "fr": {
        "subject": "Bienvenue sur TaskBeavo",
        "greeting": "Bonjour Ana,",
        "footer": "Vous recevez cet e-mail car vous avez un compte TaskBeavo.",
    },
}


def render_welcome(locale: str | None) -> RenderedEmail:
    return render_email("welcome", Recipient(locale=locale), {"first_name": "Ana"})


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_sample_email_renders_in_the_recipients_language(locale: str) -> None:
    email = render_welcome(locale)
    expected = EXPECTED[locale]

    assert email.locale == locale
    assert email.subject == expected["subject"]
    for part in (email.html, email.text):
        assert expected["greeting"] in part
        assert expected["footer"] in part
    assert f'<html lang="{locale}">' in email.html


def test_sender_language_does_not_leak_into_the_email() -> None:
    with translation.override("en"):
        email = render_welcome("de")
        assert translation.get_language() == "en"

    assert email.subject == EXPECTED["de"]["subject"]


def test_unknown_locale_renders_in_croatian() -> None:
    assert render_welcome("it").subject == EXPECTED["hr"]["subject"]


def test_subject_is_a_single_line() -> None:
    for locale in EXPECTED:
        assert "\n" not in render_welcome(locale).subject


def test_html_escapes_context_but_text_part_does_not() -> None:
    email = render_email("welcome", Recipient(locale="en"), {"first_name": "<b>Ana</b>"})

    assert "&lt;b&gt;Ana&lt;/b&gt;" in email.html
    assert "<b>Ana</b>" not in email.html
    assert "Hello, <b>Ana</b>!" in email.text


def test_base_layout_uses_brand_colours() -> None:
    html = render_welcome("en").html

    assert "#1F5C46" in html  # green header
    assert "#F5F4EF" in html  # off-white background
    assert "#17211C" in html  # ink text
