"""The VAT note every surface that states the service fee carries (BEAV-194): /fees, the
fee receipt (PDF and email, BEAV-130) and the fiscal receipt (BEAV-128).

TaskBeavo launches outside the VAT system (small-business exemption, ZPDV čl. 90 st. 1;
user decision 2026-11-07, docs/vat-position.md), so with FEE_VAT=NOT_CHARGED each of
them must state the exemption with its statutory citation. The fiscal receipt is always
Croatian; the fee receipt email is in the poster's language (hard rule i).

The Croatian sentence is the statutory wording the user gave. en, de and fr are DRAFTS,
never machine-translated, for the accountant or lawyer to confirm, paragraph included
(packages/i18n/REVIEW.md, "VAT exemption note"). The web catalogue's `fees.vatNotCharged`
ends with the same sentence in each locale; a test keeps them in step.
"""

from django.conf import settings

from bookings.money import FeeVat
from core.i18n import DEFAULT_LOCALE

FEE_VAT_EXEMPTION_NOTE: dict[str, str] = {
    "hr": "PDV nije obračunat temeljem čl. 90. st. 1. Zakona o PDV-u.",
    # DRAFT: accountant or lawyer to confirm.
    "en": "VAT not charged under Article 90(1) of the Croatian VAT Act (Zakon o PDV-u).",
    # DRAFT: professional translator and accountant or lawyer to confirm.
    "de": (
        "Keine Mehrwertsteuer berechnet gemäß Art. 90 Abs. 1 des kroatischen "
        "Mehrwertsteuergesetzes (Zakon o PDV-u)."
    ),
    # DRAFT: professional translator and accountant or lawyer to confirm.
    "fr": (
        "TVA non appliquée en vertu de l\u2019article 90, paragraphe 1, de la loi croate "
        "relative à la TVA (Zakon o PDV-u)."
    ),
}


def fee_vat_note(locale: str = DEFAULT_LOCALE, fee_vat: str | None = None) -> str | None:
    """The VAT note a receipt for a service fee must print, in `locale` (the fiscal
    receipt passes "hr"), or None when there is none to print: with FEE_VAT=INCLUDED the
    receipt shows the VAT amount instead, and UNDECIDED only runs locally and in tests.
    `fee_vat` defaults to the FEE_VAT setting."""
    if FeeVat(fee_vat or settings.FEE_VAT) != FeeVat.NOT_CHARGED:
        return None
    return FEE_VAT_EXEMPTION_NOTE.get(locale, FEE_VAT_EXEMPTION_NOTE[DEFAULT_LOCALE])
