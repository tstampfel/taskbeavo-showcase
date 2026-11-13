from collections.abc import Iterator

import pytest

from uploads import storage
from uploads.tests.fakes import FakeR2


@pytest.fixture(autouse=True)
def r2() -> Iterator[FakeR2]:
    """Task tests that publish photos talk to an in-memory R2, never the network."""
    fake = FakeR2()
    storage.use_client(fake)
    yield fake
    storage.use_client(None)
