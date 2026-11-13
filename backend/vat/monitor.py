"""The VAT threshold monitor (BEAV-194, docs/vat-position.md).

TaskBeavo launches outside the VAT system (ZPDV čl. 90 st. 1). Two limits could end that,
and this module watches both, per calendar year in Europe/Zagreb:

- **Cross-border EU B2C fees.** If the fee were classed as an electronically supplied
  service, its place of supply moves to the poster's Member State once fees to private
  posters living in other EU Member States pass EUR 10,000 in a year (ZPDV čl. 24.b st. 2,
  VAT Directive Art. 59c), bringing VAT there (OSS) from that point. Alerts at EUR 7,000
  and EUR 9,000 leave time to act; the EUR 10,000 limit itself is documented, not alerted.
- **Total fee turnover.** The small-business exemption ends above EUR 60,000 a year
  (ZPDV čl. 90); the alert comes at EUR 50,000.

**Which fees count.** A booking's fee counts in the year its payment was confirmed (its
first `funds_held` event, Stripe's webhook). What TaskBeavo keeps is counted: the kept fee
of a cancellation settlement, nothing for a booking refunded in full, otherwise the whole
fee charged (`Booking.fee_cents`, after any credit). A full refund is read from the
booking's history (a `refunded` event), not its current status, which moves on to CLOSED.
A refund still on its way counts as kept until it lands, which errs towards alerting
early. The counting date is payment
confirmation, not the receipt or supply date: a fee paid in late December and released
in January counts in December's year here, while its receipt (issued at release under
BEAV-128's working assumption) falls in January. Open with the accountant (BEAV-30 (e)).

**Where the poster lives.** `User.country`, the ISO country of residence the poster gives
in the account details at sign-up (PLAN.md, "One account, two roles"). Never the IP, the
phone number or the card. A missing or malformed value counts as unknown: it adds to the
total turnover but not to the cross-border total, and every alert reports how many fees
and how much that is, so support can check them. Every poster is treated as a private
person (consumer): there are no business poster accounts.

**Alerts.** Each threshold alerts once per year (a `VatThresholdAlert` row): an email to
VAT_ALERT_EMAILS (support and the founder) and a Sentry warning tagged
`support_flag=vat`. They carry amounts and counts only, never names, emails or user IDs.
"""

import datetime as dt
import logging
from dataclasses import asdict, dataclass
from enum import StrEnum

import sentry_sdk
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Exists, Min, OuterRef, Q
from django.utils import timezone

from bookings.models import Booking, BookingEvent
from bookings.money import FEE_TIME_ZONE
from helpers.countries import EU_MEMBER_STATES
from vat.models import VatThresholdAlert

logger = logging.getLogger("vat.monitor")

HOME_COUNTRY = "HR"
# ZPDV čl. 24.b st. 2 / VAT Directive Art. 59c: the yearly limit on cross-border EU B2C
# electronically supplied services before the place of supply moves to the customer's
# Member State. Documented here; the alerts below come before it.
CROSS_BORDER_LIMIT_CENTS = 1_000_000
# ZPDV čl. 90: the small-business exemption's yearly turnover limit.
SMALL_BUSINESS_LIMIT_CENTS = 6_000_000


class Threshold(StrEnum):
    CROSS_BORDER_7000 = "CROSS_BORDER_EU_7000"
    CROSS_BORDER_9000 = "CROSS_BORDER_EU_9000"
    TOTAL_50000 = "TOTAL_TURNOVER_50000"


# Each threshold: the amount in cents and whether it measures the cross-border total.
THRESHOLDS: dict[Threshold, tuple[int, bool]] = {
    Threshold.CROSS_BORDER_7000: (700_000, True),
    Threshold.CROSS_BORDER_9000: (900_000, True),
    Threshold.TOTAL_50000: (5_000_000, False),
}


@dataclass(frozen=True)
class YearTotals:
    """A calendar year's service fees TaskBeavo kept, in cents, split by where the poster
    lives, with the number of bookings in each part."""

    year: int
    total_cents: int
    bookings: int
    cross_border_eu_cents: int
    cross_border_eu_bookings: int
    domestic_cents: int
    non_eu_cents: int
    unknown_country_cents: int
    unknown_country_bookings: int


def year_bounds(year: int) -> tuple[dt.datetime, dt.datetime]:
    """1 January 00:00 of `year` and of the next year, in Europe/Zagreb."""
    start = dt.datetime(year, 1, 1, tzinfo=FEE_TIME_ZONE)
    return start, dt.datetime(year + 1, 1, 1, tzinfo=FEE_TIME_ZONE)


def kept_fee_cents(fee_cents: int, refunded: bool, settlement_kept_cents: int | None) -> int:
    """The part of a booking's fee TaskBeavo keeps (see the module docstring). `refunded`:
    the booking was ever refunded in full (a `refunded` event), whatever its status now, so
    a later REFUNDED → CLOSED never makes the fee count again."""
    if settlement_kept_cents is not None:
        return settlement_kept_cents
    if refunded:
        return 0
    return fee_cents


def year_totals(year: int) -> YearTotals:
    start, end = year_bounds(year)
    rows = (
        Booking.objects.annotate(
            charged_at=Min("events__created_at", filter=Q(events__type="funds_held")),
            refunded=Exists(
                BookingEvent.objects.filter(
                    booking=OuterRef("pk"), type=Booking.Status.REFUNDED.lower()
                )
            ),
        )
        .filter(charged_at__gte=start, charged_at__lt=end)
        .values_list("fee_cents", "refunded", "settlement__kept_fee_cents", "poster__country")
    )
    total = bookings = cross = cross_n = domestic = non_eu = unknown = unknown_n = 0
    for fee_cents, refunded, settlement_kept, country in rows.iterator():
        kept = kept_fee_cents(fee_cents, refunded, settlement_kept)
        total += kept
        bookings += 1
        code = (country or "").strip().upper()
        if code == HOME_COUNTRY:
            domestic += kept
        elif code in EU_MEMBER_STATES:
            cross += kept
            cross_n += 1
        elif len(code) == 2 and code.isalpha():
            non_eu += kept
        else:
            unknown += kept
            unknown_n += 1
    return YearTotals(
        year=year,
        total_cents=total,
        bookings=bookings,
        cross_border_eu_cents=cross,
        cross_border_eu_bookings=cross_n,
        domestic_cents=domestic,
        non_eu_cents=non_eu,
        unknown_country_cents=unknown,
        unknown_country_bookings=unknown_n,
    )


def check_thresholds(now: dt.datetime | None = None) -> list[Threshold]:
    """Alert on every threshold this year's totals reached that hasn't alerted this year
    yet. Returns the thresholds alerted now. Safe to run any number of times."""
    year = timezone.localtime(now or timezone.now(), FEE_TIME_ZONE).year
    totals = year_totals(year)
    sent: list[Threshold] = []
    for threshold, (amount_cents, cross_border) in THRESHOLDS.items():
        measured = totals.cross_border_eu_cents if cross_border else totals.total_cents
        if measured < amount_cents:
            continue
        try:
            with transaction.atomic():
                VatThresholdAlert.objects.create(
                    year=year, threshold=threshold, total_cents=measured
                )
                # Inside the transaction: if the email fails, the row rolls back and the
                # next run tries again.
                _email(threshold, totals)
        except IntegrityError:
            continue
        _sentry(threshold, totals)
        sent.append(threshold)
    logger.info("VAT totals checked", extra={"vat": asdict(totals)})
    return sent


def _euros(cents: int) -> str:
    return f"EUR {cents // 100:,}.{cents % 100:02d}"


SUBJECTS: dict[Threshold, str] = {
    Threshold.CROSS_BORDER_7000: "VAT alert: cross-border EU fees passed EUR 7,000 this year",
    Threshold.CROSS_BORDER_9000: "VAT alert: cross-border EU fees passed EUR 9,000 this year",
    Threshold.TOTAL_50000: "VAT alert: total service fees passed EUR 50,000 this year",
}

CONSEQUENCES: dict[Threshold, str] = {
    Threshold.CROSS_BORDER_7000: (
        "Above EUR 10,000 a year of fees to private posters living in other EU Member "
        "States (ZPDV čl. 24.b st. 2, VAT Directive Art. 59c), the place of supply may move "
        "to the poster's country if the fee is classed as an electronically supplied "
        "service. Ask the accountant now."
    ),
    Threshold.CROSS_BORDER_9000: (
        "EUR 10,000 is close: above it the place of supply may move to the poster's "
        "country (ZPDV čl. 24.b st. 2, VAT Directive Art. 59c) if the fee is classed as an "
        "electronically supplied service, with VAT due there (OSS). Decide with the "
        "accountant before it is reached."
    ),
    Threshold.TOTAL_50000: (
        "The small-business exemption (ZPDV čl. 90) ends above EUR 60,000 of turnover a "
        "year. Plan entering the VAT system with the accountant: FEE_VAT would become "
        "INCLUDED and /fees, the receipts and the price list change."
    ),
}


def _email(threshold: Threshold, totals: YearTotals) -> None:
    """An internal alert to TaskBeavo's own staff, in English like the Sentry alerts:
    amounts and counts only (docs/vat-position.md)."""
    lines = [
        CONSEQUENCES[threshold],
        "",
        f"Year: {totals.year} (Europe/Zagreb)",
        f"Total service fees kept: {_euros(totals.total_cents)} ({totals.bookings} bookings)",
        f"Posters in other EU Member States: {_euros(totals.cross_border_eu_cents)} "
        f"({totals.cross_border_eu_bookings} bookings)",
        f"Posters in Croatia: {_euros(totals.domestic_cents)}",
        f"Posters outside the EU: {_euros(totals.non_eu_cents)}",
        f"Country unknown: {_euros(totals.unknown_country_cents)} "
        f"({totals.unknown_country_bookings} bookings; counted in the total only)",
        "",
        "How it is counted: docs/vat-position.md.",
    ]
    send_mail(
        SUBJECTS[threshold],
        "\n".join(lines) + "\n",
        settings.DEFAULT_FROM_EMAIL,
        settings.VAT_ALERT_EMAILS,
    )


def _sentry(threshold: Threshold, totals: YearTotals) -> None:
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("support_flag", "vat")
        scope.set_tag("vat.threshold", threshold.value)
        scope.set_tag("vat.year", str(totals.year))
        # Extras, not a context: the scrubber keeps only allow-listed contexts.
        for key, value in asdict(totals).items():
            scope.set_extra(f"vat.{key}", value)
        sentry_sdk.capture_message("VAT threshold reached", level="warning")
