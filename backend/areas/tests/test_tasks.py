"""Waitlist retention: the daily Beat task deletes entries past their purpose."""

import datetime as dt

import pytest
from django.conf import settings
from django.utils import timezone

from areas.models import WAITLIST_CONSENT_VERSION, WaitlistEntry
from areas.tasks import purge_waitlist
from core.categories import TaskCategory

pytestmark = pytest.mark.django_db


def entry(city: str, *, age_days: int = 0, launch_email_sent: bool = False) -> WaitlistEntry:
    now = timezone.now()
    created = WaitlistEntry.objects.create(
        email="ana@example.com",
        city=city,
        city_key=city.casefold(),
        country_code="HR",
        category=TaskCategory.GARDEN,
        consent_launch_email=True,
        consent_version=WAITLIST_CONSENT_VERSION,
        consent_given_at=now,
        consent_locale="de",
        launch_email_sent_at=now if launch_email_sent else None,
    )
    # created_at is auto_now_add; age it with an update.
    WaitlistEntry.objects.filter(pk=created.pk).update(created_at=now - dt.timedelta(days=age_days))
    return created


def test_retention_is_at_most_24_months() -> None:
    assert settings.WAITLIST_RETENTION_DAYS <= 730


def test_entries_whose_launch_email_was_sent_are_deleted() -> None:
    entry("Osijek", launch_email_sent=True)
    assert purge_waitlist() == 1
    assert not WaitlistEntry.objects.exists()


def test_entries_past_the_retention_are_deleted() -> None:
    entry("Osijek", age_days=731)
    assert purge_waitlist() == 1
    assert not WaitlistEntry.objects.exists()


def test_waiting_entries_within_the_retention_stay() -> None:
    entry("Osijek", age_days=729)
    entry("Split")
    assert purge_waitlist() == 0
    assert WaitlistEntry.objects.count() == 2


def test_the_purge_runs_daily_from_beat() -> None:
    scheduled = settings.CELERY_BEAT_SCHEDULE["areas-purge-waitlist"]
    assert scheduled == {"task": "areas.purge_waitlist", "schedule": 24 * 60 * 60.0}
