"""The VAT threshold monitor (BEAV-194): cross-border EU B2C fees and total turnover."""

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from celery.schedules import crontab
from django.core import mail
from django.utils import timezone

from bookings.models import Booking, BookingEvent, CancellationSettlement
from bookings.money import FEE_TIME_ZONE
from bookings.tests.factories import make_booking
from config import telemetry
from config.tests.test_telemetry import RecordingTransport
from core.models import ActorKind
from tasks.tests.factories import make_poster
from vat import monitor, tasks
from vat.models import VatThresholdAlert
from vat.monitor import Threshold

pytestmark = pytest.mark.django_db

IN_2027 = dt.datetime(2027, 6, 1, 12, tzinfo=FEE_TIME_ZONE)
_posters = iter(range(1, 10_000))


def charged(
    fee_cents: int,
    *,
    country: str = "DE",
    at: dt.datetime = IN_2027,
    status: Booking.Status = Booking.Status.FUNDS_HELD,
) -> Booking:
    """A booking whose payment Stripe confirmed `at`, with a fee of `fee_cents`, posted by
    someone living in `country`."""
    poster = make_poster(f"poster{next(_posters)}", country=country)
    booking = make_booking(status, poster=poster)
    Booking.objects.filter(pk=booking.pk).update(
        fee_cents=fee_cents, amount_cents=fee_cents * 10 + 1_000
    )
    BookingEvent.objects.create(
        booking=booking, type="funds_held", actor_kind=ActorKind.STRIPE_WEBHOOK, created_at=at
    )
    booking.refresh_from_db()
    return booking


@pytest.fixture(autouse=True)
def alert_emails(settings: Any) -> None:
    settings.VAT_ALERT_EMAILS = ["support@example.com", "founder@example.com"]


@pytest.fixture
def sentry_events() -> Iterator[list[dict[str, Any]]]:
    transport = RecordingTransport()
    assert telemetry.setup_sentry(
        dsn="https://public@o1.ingest.de.sentry.io/1", transport=transport
    )
    yield transport.events
    sentry_sdk.get_client().close()
    sentry_sdk.init(auto_enabling_integrations=False, default_integrations=False)


def test_totals_split_fees_by_where_the_poster_lives() -> None:
    charged(1_000, country="DE")
    charged(2_000, country="AT")
    charged(4_000, country="HR")
    charged(8_000, country="CH")
    charged(16_000, country="")

    totals = monitor.year_totals(2027)

    assert totals.cross_border_eu_cents == 3_000
    assert totals.cross_border_eu_bookings == 2
    assert totals.domestic_cents == 4_000
    assert totals.non_eu_cents == 8_000
    assert totals.unknown_country_cents == 16_000
    assert totals.unknown_country_bookings == 1
    assert totals.total_cents == 31_000
    assert totals.bookings == 5


def test_a_year_runs_on_zagreb_time() -> None:
    # 23:30 on 31 December in Zagreb is still 2026; 00:30 on 1 January is 2027.
    charged(1_000, at=dt.datetime(2026, 12, 31, 23, 30, tzinfo=FEE_TIME_ZONE))
    charged(2_000, at=dt.datetime(2027, 1, 1, 0, 30, tzinfo=FEE_TIME_ZONE))

    assert monitor.year_totals(2026).cross_border_eu_cents == 1_000
    assert monitor.year_totals(2027).cross_border_eu_cents == 2_000


def test_only_fees_taskbeavo_keeps_count() -> None:
    charged(1_000)
    refund_in_full(charged(2_000))
    late = charged(4_000, status=Booking.Status.PARTIALLY_SETTLED)
    CancellationSettlement.objects.create(
        booking=late,
        case="LATE",
        amount_cents=late.amount_cents,
        fee_cents=4_000,
        helper_compensation_cents=500,
        kept_fee_cents=60,
        refund_cents=0,
        transfer_reversal_cents=0,
        fee_refund_cents=0,
    )
    # Never paid: no funds_held event.
    make_booking(Booking.Status.PAYMENT_FAILED, poster=make_poster("unpaid", country="DE"))

    assert monitor.year_totals(2027).cross_border_eu_cents == 1_060


def refund_in_full(booking: Booking) -> None:
    """Stripe confirmed a full refund with no cancellation settlement (a dispute decided
    for the poster, say), as the state machine records it."""
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.REFUNDED)
    BookingEvent.objects.create(
        booking=booking, type="refunded", actor_kind=ActorKind.STRIPE_WEBHOOK, created_at=IN_2027
    )


def test_a_full_refund_still_counts_nothing_once_the_booking_closes() -> None:
    booking = charged(2_000)
    refund_in_full(booking)
    assert monitor.year_totals(2027).cross_border_eu_cents == 0

    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.CLOSED)

    assert monitor.year_totals(2027).cross_border_eu_cents == 0
    assert monitor.year_totals(2027).bookings == 1


def test_a_booking_back_in_funds_held_counts_once_in_its_first_year() -> None:
    booking = charged(1_000, at=dt.datetime(2026, 12, 30, tzinfo=FEE_TIME_ZONE))
    BookingEvent.objects.create(
        booking=booking, type="funds_held", actor_kind=ActorKind.ADMIN, created_at=IN_2027
    )

    assert monitor.year_totals(2026).cross_border_eu_cents == 1_000
    assert monitor.year_totals(2027).bookings == 0


def test_nothing_alerts_below_7000() -> None:
    charged(699_999)

    assert monitor.check_thresholds(IN_2027) == []
    assert mail.outbox == []


def test_7000_cross_border_alerts_support_and_the_founder_once(
    sentry_events: list[dict[str, Any]],
) -> None:
    charged(700_000, country="FR")
    charged(50_000, country="HR")
    charged(1_234, country="")

    assert monitor.check_thresholds(IN_2027) == [Threshold.CROSS_BORDER_7000]
    assert monitor.check_thresholds(IN_2027) == []

    [email] = mail.outbox
    assert email.to == ["support@example.com", "founder@example.com"]
    assert "EUR 7,000" in email.subject
    assert "EUR 7,000.00 (1 bookings)" in email.body
    assert "EUR 10,000" in email.body
    assert "čl. 24.b st. 2" in email.body
    assert "Art. 59c" in email.body
    assert "Country unknown: EUR 12.34 (1 bookings" in email.body
    [event] = [e for e in sentry_events if (e.get("tags") or {}).get("support_flag") == "vat"]
    assert event["level"] == "warning"
    assert event["tags"]["vat.threshold"] == "CROSS_BORDER_EU_7000"
    assert event["extra"]["vat.cross_border_eu_cents"] == 700_000
    assert event["extra"]["vat.unknown_country_bookings"] == 1
    assert VatThresholdAlert.objects.get().total_cents == 700_000


def test_alerts_carry_no_personal_data(sentry_events: list[dict[str, Any]]) -> None:
    booking = charged(900_000, country="DE")
    poster = booking.poster

    monitor.check_thresholds(IN_2027)

    for email in mail.outbox:
        for value in (poster.email, poster.first_name, poster.last_name, str(poster.pk)):
            assert value not in email.subject
            assert value not in email.body
    for event in sentry_events:
        text = str(event)
        for value in (poster.email, poster.first_name, str(booking.pk)):
            assert value not in text


def test_9000_alerts_again_and_both_alert_when_passed_at_once() -> None:
    charged(950_000, country="AT")

    assert monitor.check_thresholds(IN_2027) == [
        Threshold.CROSS_BORDER_7000,
        Threshold.CROSS_BORDER_9000,
    ]
    assert len(mail.outbox) == 2
    assert "EUR 9,000" in mail.outbox[1].subject


def test_domestic_fees_never_count_towards_the_cross_border_alerts() -> None:
    charged(950_000, country="HR")
    charged(950_000, country="CH")
    charged(950_000, country="")

    assert monitor.check_thresholds(IN_2027) == []


def test_50000_total_turnover_alerts_whoever_the_posters_are() -> None:
    charged(4_999_999, country="HR")
    assert monitor.check_thresholds(IN_2027) == []

    charged(1, country="")
    assert monitor.check_thresholds(IN_2027) == [Threshold.TOTAL_50000]
    [email] = mail.outbox
    assert "EUR 50,000" in email.subject
    assert "EUR 60,000" in email.body
    assert "čl. 90" in email.body


def test_each_year_alerts_afresh() -> None:
    charged(700_000, at=dt.datetime(2026, 6, 1, tzinfo=FEE_TIME_ZONE))
    charged(700_000, at=IN_2027)

    assert monitor.check_thresholds(dt.datetime(2026, 7, 1, tzinfo=FEE_TIME_ZONE)) == [
        Threshold.CROSS_BORDER_7000
    ]
    assert monitor.check_thresholds(IN_2027) == [Threshold.CROSS_BORDER_7000]
    assert set(VatThresholdAlert.objects.values_list("year", flat=True)) == {2026, 2027}


def test_a_failed_email_is_retried_on_the_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    charged(700_000)

    def fail(*args: object, **kwargs: object) -> None:
        raise ConnectionError("mail down")

    monkeypatch.setattr(monitor, "send_mail", fail)
    with pytest.raises(ConnectionError):
        monitor.check_thresholds(IN_2027)
    assert not VatThresholdAlert.objects.exists()

    monkeypatch.undo()
    assert monitor.check_thresholds(IN_2027) == [Threshold.CROSS_BORDER_7000]


def test_the_daily_task_checks_the_current_year(monkeypatch: pytest.MonkeyPatch) -> None:
    charged(700_000, at=timezone.now())

    assert tasks.check_thresholds_task() == 1


def test_beat_runs_the_check_daily_at_six_zagreb_time(settings: Any) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["vat-check-thresholds"]
    assert entry["task"] == "vat.check_thresholds"
    # Wall-clock, so a deploy restarting Beat doesn't push the next run back a day.
    assert entry["schedule"] == crontab(hour=6, minute=0)
    assert settings.CELERY_TIMEZONE == "Europe/Zagreb"
