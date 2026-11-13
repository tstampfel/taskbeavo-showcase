import importlib
from collections.abc import Iterator

import pytest

from uploads import storage
from uploads.tests.fakes import FakeR2


@pytest.fixture(autouse=True)
def r2() -> Iterator[FakeR2]:
    """Staff tests post tasks with photos: an in-memory R2, never the network."""
    fake = FakeR2()
    storage.use_client(fake)
    yield fake
    storage.use_client(None)


@pytest.fixture(autouse=True)
def seeded_areas(request: pytest.FixtureRequest) -> None:
    """Seed Dubrovnik again (idempotent) in every database test (see bookings' conftest)."""
    if "django_db" not in request.keywords:
        return
    request.getfixturevalue("django_db_setup")
    from django.apps import apps

    importlib.import_module("areas.migrations.0002_seed_dubrovnik").seed(apps, None)
