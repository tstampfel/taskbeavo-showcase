"""The contract confirmation email on a durable medium (Consumer Rights Directive
Art. 8(7); Zakon o zaštiti potrošača, NN 19/22), sent to the poster when the booking
reaches FUNDS_HELD (bookings/outbox_handlers.py, kind `booking.contract_confirmation`).

In the poster's language, it confirms:

- the contract: the task, its start (Croatian time), the helper and whether they are a
  private individual or a registered business (with the full business name, OIB and
  registered address a trader must show, as snapshotted at checkout), the task price,
  TaskBeavo's fee and the total paid, and TaskBeavo's own identity as the trader for the
  fee (settings `TASKBEAVO_*`);
- what the withdrawal right covers (user decision 2026-11-04, PLAN.md "Consumer withdrawal
  law"): with a private helper, only the fee contract with TaskBeavo, since the task is
  consumer-to-consumer; with a trader helper, the fee and the task;
- the CANCELLATION document version the booking is governed by, which holds the
  withdrawal information and the model withdrawal form (decided 2026-11-04;
  `ConsentRecord.withdrawal_notice_version`). Its text is attached as published in the
  LEGAL_DOCUMENT, in the language the checkout showed it and, if different and published,
  in the email's language. Legal text never comes from the translation catalogues;
- the poster's express request to start inside the withdrawal period and their
  acknowledgement, with when they gave it.

A booking whose CANCELLATION version can't be found raises, so the delivery is retried
and then fails loudly (notifications/delivery.py) instead of sending a confirmation
without its legal text. Once sent, a `contract_confirmation_sent` BookingEvent records
the versions, languages and text fingerprints that went out, as proof.
"""

import datetime as dt
import logging
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import formats
from django.utils.translation import gettext as _

from bookings import state_machine
from bookings.models import Booking, BookingEvent
from core.email import Attachment, render_email
from core.i18n import in_recipient_locale
from core.models import ActorKind
from legal.checkout_consent import without_markers
from legal.models import LegalDocument
from notifications.emails import CustomEmail, register_email
from notifications.models import Delivery

logger = logging.getLogger(__name__)

KIND = "booking.contract_confirmation"
SENT = "contract_confirmation_sent"
SEND_FAILED = "contract_confirmation_send_failed"
# Task times are Croatian time, wherever the poster lives.
TASK_TIME_ZONE = ZoneInfo("Europe/Zagreb")


def _record_sent(delivery: Delivery, email: CustomEmail, message_id: str) -> None:
    documents = _documents(_booking(delivery), email.email.locale)
    BookingEvent.objects.create(
        booking_id=delivery.outbox.aggregate_id,
        type=SENT,
        actor_kind=ActorKind.SYSTEM,
        data={
            "delivery": delivery.pk,
            "language": email.email.locale,
            "cancellation_version": documents[0].version,
            # LEGAL_DOCUMENT rows are immutable, so their IDs pin the exact text sent.
            "legal_documents": [str(doc.pk) for doc in documents],
            "provider_id": message_id,
        },
    )


def _flag_failed(delivery: Delivery) -> None:
    """The contract confirmation failed for good (a provider outage): support resends it.
    The booking ID only."""
    booking = Booking.objects.get(pk=delivery.outbox.aggregate_id)
    state_machine.flag_for_support(booking, SEND_FAILED, {})


@register_email(KIND, on_sent=_record_sent, on_failed=_flag_failed)
def render(delivery: Delivery) -> CustomEmail:
    booking = _booking(delivery)
    poster = delivery.user
    with in_recipient_locale(poster) as locale:
        documents = _documents(booking, locale)
        context = _context(booking, documents[0], locale)
    email = render_email("contract_confirmation", poster, context)
    attachments = [
        # As readers see it: without the checkout tick-box markers (legal.checkout_consent).
        Attachment(
            f"taskbeavo-cancellation-policy-{doc.version}-{doc.language}.txt",
            without_markers(doc.body),
        )
        for doc in documents
    ]
    return CustomEmail(email=email, attachments=attachments)


def _booking(delivery: Delivery) -> Booking:
    return Booking.objects.select_related("task", "helper", "consent").get(
        pk=delivery.outbox.aggregate_id
    )


def _documents(booking: Booking, locale: str) -> list[LegalDocument]:
    """The booking's CANCELLATION version: in the language the poster accepted it in, and
    in `locale` too when that differs and is published. The accepted one comes first."""
    consent = booking.consent
    version = consent.withdrawal_notice_version
    found = {
        doc.language: doc
        for doc in LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CANCELLATION,
            version=version,
            language__in={consent.language, locale},
        )
    }
    if consent.language not in found:
        raise LookupError(f"CANCELLATION {version} ({consent.language}) is not published.")
    return [found[consent.language]] + [
        doc for language, doc in found.items() if language != consent.language
    ]


def _context(booking: Booking, policy: LegalDocument, locale: str) -> dict[str, Any]:
    """Formatted in the active language (the poster's). The trader identity is the one
    snapshotted at checkout (ConsentRecord), never the live profile (review R1-4)."""
    consent = booking.consent
    if consent.helper_is_trader and not consent.trader_address:
        # The offer gate makes this impossible; if it happens, the email goes out without
        # an address (never the home address) and support should add it.
        logger.error(
            "bookings: a trader's contract confirmation has no registered business address",
            extra={"booking.id": str(booking.pk)},
        )
    return {
        "first_name": booking.poster.first_name,
        "task_title": booking.task.title,
        "start": _when(booking.slot_start),
        "helper_name": booking.helper.first_name,
        "helper_is_trader": consent.helper_is_trader,
        "trader_name": consent.trader_name,
        "trader_oib": consent.trader_oib,
        "trader_address": consent.trader_address,
        "price": _money(booking.amount_cents),
        "fee": _money(booking.fee_cents),
        "total": _money(booking.amount_cents + booking.fee_cents),
        "cancellation_version": policy.version,
        "early_start_at": _when(consent.express_request_at),
        # TaskBeavo is the trader for the fee (CRD Art. 6(1)(b)-(c)).
        "taskbeavo_name": settings.TASKBEAVO_LEGAL_NAME,
        "taskbeavo_oib": settings.TASKBEAVO_OIB,
        "taskbeavo_address": settings.TASKBEAVO_ADDRESS,
        "taskbeavo_email": settings.TASKBEAVO_CONTACT_EMAIL,
        "link": f"{settings.WEB_APP_URL}/{locale}/bookings/{booking.pk}",
    }


def _money(cents: int) -> str:
    amount = formats.number_format(Decimal(cents) / 100, 2, use_l10n=True)
    # Translators: an amount in euros; "€%(amount)s" in English, "%(amount)s €" in hr/de/fr.
    return _("€%(amount)s") % {"amount": amount}


def _when(moment: dt.datetime) -> str:
    return formats.date_format(moment.astimezone(TASK_TIME_ZONE), "DATETIME_FORMAT", use_l10n=True)
