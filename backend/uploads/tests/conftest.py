from collections.abc import Iterator

import pytest
from django.core.cache import caches

from uploads import storage
from uploads.tests.fakes import FakeR2


@pytest.fixture(autouse=True)
def r2() -> Iterator[FakeR2]:
    """Every uploads test talks to an in-memory R2, never the network."""
    fake = FakeR2()
    storage.use_client(fake)
    caches["graphql_rate_limit"].clear()
    yield fake
    storage.use_client(None)
