"""Demo payments in tests: the demo Stripe stand-in on, its events delivered only when a
test calls `deliver()`, so each test decides when "Stripe" sends its webhooks."""

import importlib
from collections.abc import Iterator

import pytest
from django.test import override_settings
from django.urls import clear_url_caches

from payments import demo_stripe, stripe_client


@pytest.fixture
def demo_payments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(demo_stripe, "schedule_delivery", lambda: None)
    stripe_client.get_gateway.cache_clear()
    with override_settings(DEMO_PAYMENTS=True, STRIPE_SECRET_KEY=""):
        yield
    stripe_client.get_gateway.cache_clear()


def reload_urls() -> None:
    """Rebuild config.urls under the current settings (the demo route is mounted at import)."""
    import config.urls

    importlib.reload(config.urls)
    clear_url_caches()


def deliver() -> int:
    return demo_stripe.deliver_events()
