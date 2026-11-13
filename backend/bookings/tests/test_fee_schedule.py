"""The public price list of the service fee (BEAV-191, ZZP čl. 7(9)): every fee is charged
from the schedule in force at that moment, and the API serves the same schedules, so the
/fees page and pricelist.csv can't drift from what checkout charges."""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.test import Client

from bookings import cancellation, money, state_machine
from bookings.money import (
    FEE_EXAMPLE_PRICES_CENTS,
    FEE_SCHEDULES,
    FeeSchedule,
    FeeVat,
    fee_schedule_at,
)

ZAGREB = ZoneInfo("Europe/Zagreb")

PRICE_LIST = """
query PriceList {
  serviceFeeSchedules {
    id version effectiveFrom percent minimumCents status vat vatPercent
    examples { id taskPriceCents feeCents totalCents vatCents }
  }
}
"""

TODAY = FeeSchedule(
    version="2026-11", effective_from=dt.date(2026, 11, 1), percent=12, minimum_cents=250
)
ANNOUNCED = FeeSchedule(
    version="2027-03", effective_from=dt.date(2027, 3, 1), percent=15, minimum_cents=300
)


def price_list(client: Client) -> list[dict[str, Any]]:
    response = client.post("/graphql", {"query": PRICE_LIST}, content_type="application/json")
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    return body["data"]["serviceFeeSchedules"]


@pytest.fixture
def announced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fee change announced for 1 March 2027, after today's list."""
    monkeypatch.setattr(money, "FEE_SCHEDULES", (TODAY, ANNOUNCED))


def zagreb(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=ZAGREB)


def test_the_fee_in_force_is_twelve_percent_at_least_two_fifty() -> None:
    # Hard rule b: a change is a material terms change (30 days' notice, BEAV-182).
    schedule = fee_schedule_at()
    assert (schedule.version, schedule.percent, schedule.minimum_cents) == ("1", 12, 250)
    # Dated the launch date (FEE_LAUNCH_DATE, a fixed default in tests).
    assert schedule.effective_from == settings.FEE_LAUNCH_DATE


@pytest.mark.parametrize("amount", [1000, 1500, 2083, 2084, 3000, 4000, 12_345])
def test_checkout_charges_the_schedule_in_force(amount: int) -> None:
    assert state_machine.service_fee_cents(amount) == fee_schedule_at().fee_cents(amount)


def test_schedules_are_unique_and_in_effective_order() -> None:
    versions = [schedule.version for schedule in FEE_SCHEDULES]
    dates = [schedule.effective_from for schedule in FEE_SCHEDULES]
    assert len(set(versions)) == len(versions)
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_every_later_list_takes_effect_after_the_launch_date() -> None:
    # The first list is dated FEE_LAUNCH_DATE (a setting); a later list hard-coded with an
    # earlier date would silently take over at launch (review r2 note).
    assert FEE_SCHEDULES[0].effective_from == settings.FEE_LAUNCH_DATE
    assert all(later.effective_from > settings.FEE_LAUNCH_DATE for later in FEE_SCHEDULES[1:])


@pytest.mark.usefixtures("announced")
def test_an_announced_fee_is_not_charged_before_its_date() -> None:
    last_minute = zagreb(2027, 2, 28, 23, 59, 59)
    assert fee_schedule_at(last_minute) is TODAY
    assert state_machine.service_fee_cents(4000, last_minute) == 480
    assert state_machine.service_fee_cents(1500, last_minute) == 250


@pytest.mark.usefixtures("announced")
def test_an_announced_fee_is_charged_from_midnight_zagreb_on_its_date() -> None:
    midnight = zagreb(2027, 3, 1, 0, 0)
    assert fee_schedule_at(midnight) is ANNOUNCED
    assert state_machine.service_fee_cents(4000, midnight) == 600
    assert state_machine.service_fee_cents(1500, midnight) == 300
    assert fee_schedule_at(zagreb(2027, 6, 1, 12, 0)) is ANNOUNCED


@pytest.mark.usefixtures("announced")
def test_the_boundary_is_the_zagreb_day_not_utc() -> None:
    # 23:30 UTC on 28 Feb is already 00:30 on 1 March in Zagreb.
    assert fee_schedule_at(dt.datetime(2027, 2, 28, 23, 30, tzinfo=dt.UTC)) is ANNOUNCED
    assert fee_schedule_at(dt.datetime(2027, 2, 28, 22, 59, tzinfo=dt.UTC)) is TODAY


def test_a_cancellation_keeps_the_rate_the_booking_was_charged_at() -> None:
    """K is the booking's stored rate (`Booking.fee_percent`), never the schedule in force
    at the cancellation; tests/test_fee_switch.py runs it through a real switch."""
    late = cancellation.cancellation_outcome(
        cancelled_by=cancellation.Party.POSTER,
        no_fault_reason=None,
        amount_cents=3_000,
        fee_cents=360,
        fee_percent=12,
        accepted_at=zagreb(2027, 2, 28, 12, 0),
        slot_start=zagreb(2027, 3, 2, 10, 0),
        now=zagreb(2027, 3, 2, 9, 0),
        helper_is_trader=False,
    )
    assert (late.helper_compensation_cents, late.kept_fee_percent, late.kept_fee_cents) == (
        750,
        12,
        90,
    )
    call_out = cancellation.no_access_call_out(amount_cents=3_000, fee_cents=450, fee_percent=15)
    assert call_out.kept_fee_cents == 113  # 15% of 750, half up


@pytest.mark.usefixtures("announced")
def test_the_first_list_also_applies_before_its_date() -> None:
    # Before the launch date the first list applies, so dev, tests and CI never hit a gap;
    # a later list still applies only from its own date.
    assert fee_schedule_at(zagreb(2026, 1, 1, 9, 0)) is TODAY
    assert fee_schedule_at(zagreb(2026, 10, 31, 23, 59)) is TODAY


@pytest.mark.django_db
def test_the_api_serves_the_schedule_checkout_charges(client: Client) -> None:
    schedule = fee_schedule_at()
    assert price_list(client) == [
        {
            "id": schedule.version,
            "version": schedule.version,
            "effectiveFrom": schedule.effective_from.isoformat(),
            "percent": schedule.percent,
            "minimumCents": schedule.minimum_cents,
            "status": "CURRENT",
            "vat": "UNDECIDED",
            "vatPercent": 25,
            "examples": [
                {
                    "id": f"{schedule.version}:{price}",
                    "taskPriceCents": price,
                    "feeCents": state_machine.service_fee_cents(price),
                    "totalCents": price + state_machine.service_fee_cents(price),
                    "vatCents": None,
                }
                for price in FEE_EXAMPLE_PRICES_CENTS
            ],
        }
    ]


@pytest.mark.django_db
def test_the_worked_examples_of_the_drafting_pack(client: Client) -> None:
    # €40 → €4.80 → €44.80; €15 → the €2.50 minimum → €17.50.
    assert [
        (row["taskPriceCents"], row["feeCents"], row["totalCents"])
        for row in price_list(client)[0]["examples"]
    ] == [(4000, 480, 4480), (1500, 250, 1750)]


@pytest.mark.django_db
@pytest.mark.usefixtures("announced")
def test_an_announced_list_is_upcoming_until_its_date(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(money.timezone, "now", lambda: zagreb(2027, 2, 1, 9, 0))
    before = price_list(client)
    assert [(row["version"], row["status"]) for row in before] == [
        ("2027-03", "UPCOMING"),
        ("2026-11", "CURRENT"),
    ]
    assert before[0]["examples"][0]["feeCents"] == 600

    monkeypatch.setattr(money.timezone, "now", lambda: zagreb(2027, 3, 1, 0, 0))
    assert [(row["version"], row["status"]) for row in price_list(client)] == [
        ("2027-03", "CURRENT"),
        ("2026-11", "EARLIER"),
    ]
    # NN 101/2026: a published list stays downloadable at least 30 days; ours stay for good.
    monkeypatch.setattr(money.timezone, "now", lambda: zagreb(2028, 3, 1, 8, 0))
    assert [row["version"] for row in price_list(client)] == ["2027-03", "2026-11"]


def test_vat_stays_undecided_until_the_accountant_picks_a_variant() -> None:
    assert all(schedule.vat == FeeVat.UNDECIDED for schedule in FEE_SCHEDULES)


def test_vat_inside_a_vat_inclusive_fee() -> None:
    # Variant A of the drafting pack: €0.96 of the €4.80 fee is 25% VAT.
    included = FeeSchedule("x", dt.date(2026, 11, 1), 12, 250, vat=FeeVat.INCLUDED)
    assert included.vat_cents(480) == 96
    assert included.vat_cents(250) == 50
    assert included.fee_cents(4000) == 480  # VAT never adds to what the poster pays
    assert FeeSchedule("x", dt.date(2026, 11, 1), 12, 250).vat_cents(480) is None
    assert (
        FeeSchedule("x", dt.date(2026, 11, 1), 12, 250, vat=FeeVat.NOT_CHARGED).vat_cents(480)
        is None
    )


CANCELLATION_AMOUNTS = """
query { cancellationAmounts {
  id gracePeriodMinutes lateWindowHours latePayPercent latePayMinCents latePayMaxCents
  callOutPercent callOutMinCents callOutMaxCents keptFeePercent helperCancelCreditCents
  withdrawalPeriodDays
} }
"""


@pytest.mark.django_db
def test_the_refund_table_reads_the_cancellation_rules(client: Client) -> None:
    response = client.post(
        "/graphql", {"query": CANCELLATION_AMOUNTS}, content_type="application/json"
    )
    # Hard rule g, as bookings/cancellation.py applies it.
    assert response.json()["data"]["cancellationAmounts"] == {
        "id": "current",
        "gracePeriodMinutes": cancellation.GRACE_PERIOD.total_seconds() // 60,
        "lateWindowHours": cancellation.late_cancel_window().total_seconds() // 3600,
        "latePayPercent": cancellation.LATE_PAY_PERCENT,
        "latePayMinCents": cancellation.LATE_PAY_MIN_CENTS,
        "latePayMaxCents": cancellation.LATE_PAY_MAX_CENTS,
        "callOutPercent": cancellation.CALL_OUT_PERCENT,
        "callOutMinCents": cancellation.CALL_OUT_MIN_CENTS,
        "callOutMaxCents": cancellation.CALL_OUT_MAX_CENTS,
        "keptFeePercent": fee_schedule_at().percent,
        "helperCancelCreditCents": cancellation.HELPER_CANCEL_CREDIT_CENTS,
        "withdrawalPeriodDays": cancellation.WITHDRAWAL_PERIOD_DAYS,
    }
    assert tuple(
        response.json()["data"]["cancellationAmounts"][key]
        for key in (
            "gracePeriodMinutes",
            "lateWindowHours",
            "latePayPercent",
            "latePayMinCents",
            "latePayMaxCents",
            "callOutPercent",
            "callOutMinCents",
            "callOutMaxCents",
            "keptFeePercent",
            "helperCancelCreditCents",
            "withdrawalPeriodDays",
        )
    ) == (60, 24, 25, 500, 2000, 25, 500, 1500, 12, 500, 14)
