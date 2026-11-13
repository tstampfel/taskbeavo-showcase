"""The legal wording of the withdrawal confirmation email, inside a CANCELLATION document
(BEAV-122; ZZP čl. 81.a(6), CRD Art. 11a(3)).

The confirmation is legal text, so its wording comes from the lawyer in the CANCELLATION
version in force, per language, never from the translation catalogues (hard rule i), the
way the checkout tick boxes do (legal/checkout_consent.py). The withdrawal button exists
only for a trader helper's booking, so there is one marked section. Its first line is the
email's subject:

    <!-- email:withdrawal_confirmation_trader -->
    Subject: Potvrda o raskidu ugovora {booking_ref}
    Poštovani {name}, ...
    <!-- /email:withdrawal_confirmation_trader -->

Line breaks are kept. The placeholders in PLACEHOLDERS are filled from the booking and the
statement (bookings/withdrawal_email.py); anything else in braces is left as written.
The comments don't render on the legal pages (`without_markers`). A version without the
section still publishes: the email then confirms the facts only and support is alerted,
because the confirmation must never be held back.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from legal.checkout_consent import marked_raw

MARKER = "withdrawal_confirmation_trader"
PLACEHOLDERS = (
    "poster_name",
    "helper_business_name",
    "helper_oib",
    "helper_address",
    "booking_ref",
    "task_title",
    "price",
    "fee",
    "date",
    "time",
    "timezone",
    "statement_text",
    "name",
    "email",
    "total_paid",
    "done_amount",
    "description_of_part_done",
    "refund_amount",
)
_SUBJECT = re.compile(r"^\s*subject\s*:\s*", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class WithdrawalConfirmationText:
    """The lawyer's subject ("" if the section has none) and body, placeholders unfilled."""

    subject: str
    body: str

    def filled(self, values: Mapping[str, str]) -> "WithdrawalConfirmationText":
        """Placeholders filled. The subject is a header: every whitespace run in it, CR and
        LF included (a poster-typed task title can hold a line break), becomes one space
        (review R2-1)."""
        return WithdrawalConfirmationText(
            subject=" ".join(fill(self.subject, values).split()), body=fill(self.body, values)
        )


def withdrawal_confirmation_text(document_body: str) -> WithdrawalConfirmationText | None:
    """The marked section of a CANCELLATION text, or None if it is missing or empty."""
    raw = marked_raw(document_body, MARKER, namespace="email")
    if raw is None:
        return None
    lines = [line.strip() for line in raw.strip().splitlines()]
    subject = ""
    if lines and _SUBJECT.match(lines[0]):
        subject = _SUBJECT.sub("", lines.pop(0))
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not body:
        return None
    return WithdrawalConfirmationText(subject=subject, body=body)


def fill(text: str, values: Mapping[str, str]) -> str:
    """`text` with each known `{placeholder}` replaced by its value."""
    return _PLACEHOLDER.sub(
        lambda found: (
            values[found[1]] if found[1] in PLACEHOLDERS and found[1] in values else found[0]
        ),
        text,
    )
