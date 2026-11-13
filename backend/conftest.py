from collections.abc import Iterator

import pytest
from celery._state import _set_task_join_will_block
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from config.telemetry import AllowListSpanProcessor

_exporter = InMemorySpanExporter()
_provider = TracerProvider()
# Spans pass the same allow-list as in production, so tests see what Honeycomb would.
_provider.add_span_processor(AllowListSpanProcessor(SimpleSpanProcessor(_exporter)))
# The global provider can be set once per process; tests read spans from the exporter.
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def _reset_celery_join_guard() -> Iterator[None]:
    # Eager tasks save and restore Celery's process-wide "inside a task" flag. Race tests
    # run them in concurrent threads, which restore it out of order and can leave it set,
    # so a later result.get() raises "Never call result.get() within a task!".
    yield
    _set_task_join_will_block(False)


@pytest.fixture(autouse=True)
def _reset_helper_oibs() -> None:
    # make_helper hands out synthetic OIBs from a short range; each test starts it again.
    from offers.tests.factories import reset_helper_oibs

    reset_helper_oibs()


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _exporter.clear()
    return _exporter
