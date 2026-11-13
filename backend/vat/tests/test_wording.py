"""The VAT exemption note for /fees and the receipts (BEAV-194)."""

import json
from pathlib import Path
from typing import Any

import pytest

from core.i18n import SUPPORTED_LOCALES
from vat.wording import FEE_VAT_EXEMPTION_NOTE, fee_vat_note

CATALOGUES = Path(__file__).resolve().parents[3] / "packages" / "i18n" / "src" / "locales"


def test_every_supported_locale_has_a_note() -> None:
    assert set(FEE_VAT_EXEMPTION_NOTE) == set(SUPPORTED_LOCALES)


def test_the_croatian_note_is_the_statutory_sentence() -> None:
    assert fee_vat_note("hr", "NOT_CHARGED") == (
        "PDV nije obračunat temeljem čl. 90. st. 1. Zakona o PDV-u."
    )


@pytest.mark.parametrize(
    ("locale", "citation"),
    [
        ("en", "Article 90(1) of the Croatian VAT Act"),
        ("de", "Art. 90 Abs. 1 des kroatischen Mehrwertsteuergesetzes"),
        ("fr", "l\u2019article 90, paragraphe 1, de la loi croate relative à la TVA"),
    ],
)
def test_every_locale_cites_article_90_paragraph_1(locale: str, citation: str) -> None:
    note = fee_vat_note(locale, "NOT_CHARGED")
    assert note is not None
    assert citation in note
    assert "Zakon o PDV-u" in note


@pytest.mark.parametrize("fee_vat", ["INCLUDED", "UNDECIDED"])
def test_no_note_unless_the_fee_is_outside_vat(fee_vat: str) -> None:
    assert fee_vat_note("hr", fee_vat) is None


def test_the_note_follows_the_fee_vat_setting(settings: Any) -> None:
    settings.FEE_VAT = "NOT_CHARGED"
    assert fee_vat_note("de") == FEE_VAT_EXEMPTION_NOTE["de"]
    settings.FEE_VAT = "INCLUDED"
    assert fee_vat_note("de") is None


def test_an_unsupported_locale_gets_croatian() -> None:
    assert fee_vat_note("it", "NOT_CHARGED") == FEE_VAT_EXEMPTION_NOTE["hr"]


@pytest.mark.parametrize("locale", sorted(FEE_VAT_EXEMPTION_NOTE))
def test_fees_page_states_the_same_note(locale: str) -> None:
    if not CATALOGUES.exists():
        # The compose service mounts only backend/, so packages/ is not there.
        pytest.skip("packages/i18n is outside the mounted backend/ directory")
    catalogue = json.loads((CATALOGUES / f"{locale}.json").read_text())
    assert catalogue["fees"]["vatNotCharged"].endswith(FEE_VAT_EXEMPTION_NOTE[locale])
