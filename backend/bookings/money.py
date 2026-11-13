"""The service fee rate and the one rounding rule every booking amount uses: whole euro
cents, half up. `state_machine.service_fee_cents` and `cancellation` both build on it, so
K (the booking's fee rate of the compensation) rounds exactly like the fee.

The fee is published as a price list (ZZP čl. 7(9), BEAV-191): FEE_SCHEDULES holds every
list, each charged from its effective date (`fee_schedule_at`). The API's
`serviceFeeSchedules` serves the same lists to the /fees page and pricelist.csv, so the page
shows exactly what checkout charges."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

# Effective dates are Croatian calendar days.
FEE_TIME_ZONE = ZoneInfo("Europe/Zagreb")


def percent_half_up(cents: int, percent: int) -> int:
    """`percent`% of `cents`, rounded half up to a whole cent."""
    return (cents * percent + 50) // 100


class FeeVat(StrEnum):
    """Whether the service fee includes VAT (ZET čl. 6(2), ZZP čl. 4 point 14). The
    accountant picks INCLUDED (variant A: in the VAT system, the fee includes 25% Croatian
    VAT) or NOT_CHARGED (variant B: small-business scheme, no VAT); until then UNDECIDED,
    and /fees states neither. The first schedule's comes from the FEE_VAT setting, which
    deployed environments require to be decided."""

    UNDECIDED = "UNDECIDED"
    INCLUDED = "INCLUDED"
    NOT_CHARGED = "NOT_CHARGED"


# Croatian standard VAT rate, used only when a schedule's `vat` is INCLUDED.
CROATIAN_VAT_PERCENT = 25


@dataclass(frozen=True)
class FeeSchedule:
    """One published price list of TaskBeavo's service fee: `percent` of the task price, at
    least `minimum_cents`, charged from 00:00 Europe/Zagreb on `effective_from`. The fee is
    VAT-inclusive whatever `vat` says: VAT never adds to what the poster pays."""

    version: str
    effective_from: dt.date
    percent: int
    minimum_cents: int
    vat: FeeVat = FeeVat.UNDECIDED

    def fee_cents(self, amount_cents: int) -> int:
        return max(percent_half_up(amount_cents, self.percent), self.minimum_cents)

    def vat_cents(self, fee_cents: int) -> int | None:
        """The VAT inside a fee (25/125 of it, half up) when the fee includes VAT."""
        if self.vat != FeeVat.INCLUDED:
            return None
        gross = 100 + CROATIAN_VAT_PERCENT
        return (2 * fee_cents * CROATIAN_VAT_PERCENT + gross) // (2 * gross)


# Every price list ever published or announced, oldest first. Earlier ones stay here so
# /fees/archive keeps serving them (NN 101/2026: each list stays available at least 30 days).
# A fee change is a material terms change: announce it 30 days ahead (BEAV-182) and add the
# new schedule with that effective date; it is charged from that day, never before. A
# booking keeps the version and rate it was charged under (`Booking.fee_schedule_version`,
# `Booking.fee_percent`); a version string is never reused.
FEE_SCHEDULES: tuple[FeeSchedule, ...] = (
    # 12% of the task price, at least €2.50 (hard rule b; PLAN.md, "Proposed fees and unit
    # economics"), from the launch date (`FEE_LAUNCH_DATE`; user decision 2026-11-07).
    # `vat` comes from FEE_VAT, the accountant's decision; deployed environments refuse to
    # start while it is UNDECIDED (user decision 2026-11-07).
    FeeSchedule(
        version="1",
        effective_from=settings.FEE_LAUNCH_DATE,
        percent=12,
        minimum_cents=250,
        vat=FeeVat(settings.FEE_VAT),
    ),
)

# The task prices of the worked examples on /fees: one above the minimum, one below it.
FEE_EXAMPLE_PRICES_CENTS = (4000, 1500)


def fee_schedule_at(when: dt.datetime | None = None) -> FeeSchedule:
    """The schedule in force at `when` (default: now): the latest one whose effective date
    has begun in Europe/Zagreb. Before the first one's date (the launch date) the first one
    applies, so dev, tests and CI are never without a fee (user decision 2026-11-07); every
    later schedule applies only from its own date. Every fee calculation goes through here."""
    day = timezone.localtime(when or timezone.now(), FEE_TIME_ZONE).date()
    in_force = [schedule for schedule in FEE_SCHEDULES if schedule.effective_from <= day]
    if not in_force:
        return min(FEE_SCHEDULES, key=lambda schedule: schedule.effective_from)
    return max(in_force, key=lambda schedule: schedule.effective_from)
