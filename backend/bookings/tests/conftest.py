import importlib
from collections.abc import Iterator

import pytest

from bookings import checkout, payouts, tips
from bookings.tests.fakes import FakeGateway, FakePayoutStripe
from uploads import storage
from uploads.tests.fakes import FakeR2


@pytest.fixture(autouse=True)
def r2() -> Iterator[FakeR2]:
    """Booking tests post tasks that may publish photos: an in-memory R2, never the network."""
    fake = FakeR2()
    storage.use_client(fake)
    yield fake
    storage.use_client(None)


@pytest.fixture(autouse=True)
def payout_stripe(monkeypatch: pytest.MonkeyPatch) -> FakePayoutStripe:
    """A release opens a payout whose outbox handler runs Celery eagerly in tests: an
    in-memory Stripe for payouts and tips, never the network (bookings/tests/fakes.py)."""
    fake = FakePayoutStripe()
    monkeypatch.setattr(payouts, "get_gateway", lambda: fake)
    monkeypatch.setattr(tips, "get_gateway", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def seeded_areas(request: pytest.FixtureRequest) -> None:
    """A transactional test flushes the database, seeded service areas included, for the
    tests after it too. Seed Dubrovnik again (idempotent) in every database test."""
    if "django_db" not in request.keywords:
        return
    request.getfixturevalue("django_db_setup")
    from django.apps import apps

    importlib.import_module("areas.migrations.0002_seed_dubrovnik").seed(apps, None)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """Stripe as the checkout sees it (bookings/tests/fakes.py)."""
    fake = FakeGateway()
    monkeypatch.setattr(checkout, "get_gateway", lambda: fake)
    return fake
