"""The withdrawal confirmation on a durable medium (ZZP čl. 81.a(6); Directive 2023/2673,
CRD Art. 11a(3)), sent without delay once the poster's withdrawal statement is in
(bookings/cancel.py, kind `booking.withdrawal_confirmation`), to the email address the
statement gives.

It always goes out, in the poster's language. Its wording is the lawyer's: the
`email:withdrawal_confirmation_trader` section of the CANCELLATION version in force in the
poster's language (legal/withdrawal_confirmation.py, hard rule i), subject included, with
the placeholders filled from the booking and the statement; production never serves a
draft version. If that version doesn't mark the section, the email confirms the facts only
(labels and values from the translation catalogues: the booking, the task and its start,
when the statement was received, the statement as submitted, what was paid and the
refund) and support is flagged once (`withdrawal_confirmation_wording_missing`). The filled
subject is one header line, whitespace collapsed; if it is still empty, too long or holds
a control character, the facts-only subject is used and support is flagged once
(`withdrawal_confirmation_subject_unusable`), so no filled header can block the send.

Once sent, a `withdrawal_confirmation_sent` BookingEvent records the delivery, the
language, the CANCELLATION version whose wording it carried (empty for facts only) and the
provider's message ID (`provider_id`), as proof. A provider outage never drops it: after
its quick retries support is flagged once (`withdrawal_confirmation_send_failed`) and it
is tried again every 3 hours for 3 days, until it is sent (review R3-1). The address it
went to is the `WithdrawalStatement`'s.

NEEDS REVIEW (lawyer, BEAV-39): the marked wording, and what the placeholders say for a
withdrawal after the start before support has worked out the part already done.
"""

import dataclasses
import datetime as dt
import unicodedata
from typing import Any

from django.conf import settings
from django.utils import formats
from django.utils.translation import gettext as _

from bookings import state_machine
from bookings.cancel import STATEMENT
from bookings.cancellation import charged_cents
from bookings.contract_email import TASK_TIME_ZONE, _money, _when
from bookings.models import Booking, BookingEvent, ConsentRecord, WithdrawalStatement
from core.email import render_email
from core.i18n import in_recipient_locale
from core.models import ActorKind
from legal.models import LegalDocument
from legal.services import current_document
from legal.withdrawal_confirmation import (
    WithdrawalConfirmationText,
    withdrawal_confirmation_text,
)
from notifications.emails import CustomEmail, SlowRetry, register_email
from notifications.models import Delivery

KIND = "booking.withdrawal_confirmation"
SENT = "withdrawal_confirmation_sent"
WORDING_MISSING = "withdrawal_confirmation_wording_missing"
SUBJECT_UNUSABLE = "withdrawal_confirmation_subject_unusable"
SEND_FAILED = "withdrawal_confirmation_send_failed"
# Well inside RFC 5322's 998-character line limit, and Resend's.
SUBJECT_MAX_LENGTH = 250


def _record_sent(delivery: Delivery, email: CustomEmail, message_id: str) -> None:
    document, wording = _wording(email.email.locale)
    BookingEvent.objects.create(
        booking_id=delivery.outbox.aggregate_id,
        type=SENT,
        actor_kind=ActorKind.SYSTEM,
        data={
            "delivery": delivery.pk,
            "language": email.email.locale,
            "cancellation_version": document.version if document and wording else "",
            "provider_id": message_id,
        },
    )


def _flag_failed(delivery: Delivery) -> None:
    """The confirmation used its quick retries (a provider outage): support resends it if
    the slow retries don't get through either. The booking ID only."""
    booking = Booking.objects.get(pk=delivery.outbox.aggregate_id)
    _flag_once(booking, SEND_FAILED, {})


@register_email(
    KIND,
    on_sent=_record_sent,
    on_failed=_flag_failed,
    slow_retry=SlowRetry(every=dt.timedelta(hours=3), for_=dt.timedelta(days=3)),
)
def render(delivery: Delivery) -> CustomEmail:
    booking = Booking.objects.select_related("task", "poster").get(pk=delivery.outbox.aggregate_id)
    poster = delivery.user
    statement = BookingEvent.objects.filter(booking=booking, type=STATEMENT).latest("created_at")
    given = WithdrawalStatement.objects.get(booking=booking)
    with in_recipient_locale(poster) as locale:
        _, wording = _wording(locale)
        context = _context(booking, statement, locale)
        # The section's own subject line, before filling: one that fills to nothing is
        # unusable, not absent.
        has_subject = wording is not None and bool(wording.subject)
        if wording is not None:
            wording = wording.filled(_placeholders(booking, statement, given))
            context["legal_text"] = wording.body
    if wording is None:
        _flag_missing_wording(booking, locale)
    email = render_email("withdrawal_confirmation", poster, context)
    if wording is not None and has_subject:
        if _usable_subject(wording.subject):
            email = dataclasses.replace(email, subject=wording.subject)
        else:
            # Never held back (review R2-1): the facts-only subject, and support is told.
            _flag_once(booking, SUBJECT_UNUSABLE, {"language": locale})
    return CustomEmail(email=email, to=given.email)


def _usable_subject(subject: str) -> bool:
    """A filled subject the mail backends accept as one header line."""
    return (
        0 < len(subject) <= SUBJECT_MAX_LENGTH
        and subject == " ".join(subject.split())
        and not any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in subject)
    )


def _wording(locale: str) -> tuple[LegalDocument | None, WithdrawalConfirmationText | None]:
    document = current_document(LegalDocument.Kind.CANCELLATION, locale)
    if document is None or (document.is_draft and not settings.LEGAL_DRAFTS_ALLOWED):
        return None, None
    return document, withdrawal_confirmation_text(document.body)


def _flag_missing_wording(booking: Booking, locale: str) -> None:
    _flag_once(booking, WORDING_MISSING, {"language": locale})


def _flag_once(booking: Booking, reason: str, data: dict[str, str]) -> None:
    if BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=reason
    ).exists():
        return
    state_machine.flag_for_support(booking, reason, data)


def _context(booking: Booking, statement: BookingEvent, locale: str) -> dict[str, Any]:
    """The facts-only email, in the active language (the poster's)."""
    return {
        "first_name": booking.poster.first_name,
        "booking_id": str(booking.pk),
        "task_title": booking.task.title,
        "start": _when(booking.slot_start),
        "submitted_at": _when(statement.created_at),
        "work_started": bool(statement.data.get("work_started")),
        "total": _money(charged_cents(booking.amount_cents, booking.fee_cents)),
        "legal_text": "",
        "link": f"{settings.WEB_APP_URL}/{locale}/bookings/{booking.pk}",
    }


def _placeholders(
    booking: Booking, statement: BookingEvent, given: WithdrawalStatement
) -> dict[str, str]:
    """The lawyer's placeholders, in the active language. The trader identity is the one
    snapshotted at checkout (ConsentRecord). Right after a withdrawal after the start the
    part already done isn't worked out yet, so those three say so."""
    consent = ConsentRecord.objects.filter(booking=booking).first()
    at = statement.created_at.astimezone(TASK_TIME_ZONE)
    total = charged_cents(booking.amount_cents, booking.fee_cents)
    poster = booking.poster
    values = {
        "poster_name": f"{poster.first_name} {poster.last_name}".strip(),
        "helper_business_name": consent.trader_name if consent else "",
        "helper_oib": consent.trader_oib if consent else "",
        "helper_address": consent.trader_address if consent else "",
        "booking_ref": str(booking.pk),
        "task_title": booking.task.title,
        "price": _money(booking.amount_cents),
        "fee": _money(booking.fee_cents),
        "date": formats.date_format(at, "DATE_FORMAT", use_l10n=True),
        "time": formats.time_format(at, "TIME_FORMAT", use_l10n=True),
        "timezone": at.tzname() or "",
        "statement_text": _("Withdraw from contract"),
        "name": given.name,
        "email": given.email,
        "total_paid": _money(total),
    }
    if statement.data.get("work_started"):
        pending = _("Our team is working this out; we'll let you know.")
        values |= {
            "done_amount": pending,
            "description_of_part_done": pending,
            "refund_amount": pending,
        }
    else:
        values |= {
            "done_amount": _money(0),
            "description_of_part_done": _("None: the work hadn't started."),
            "refund_amount": _money(total),
        }
    return values
