from typing import Any

import pytest
from django.core.cache import caches

from accounts import providers
from accounts.tests.factories import TERMS_VERSION
from accounts.tests.id_tokens import (
    APPLE_BUNDLE_ID,
    APPLE_SERVICES_ID,
    GOOGLE_IOS_CLIENT_ID,
    GOOGLE_WEB_CLIENT_ID,
    PROVIDER_KEY,
)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with empty mutation rate-limit counters."""
    caches["graphql_rate_limit"].clear()
    yield
    caches["graphql_rate_limit"].clear()


@pytest.fixture
def provider_keys(monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
    """Google and Apple client IDs configured, and their ID tokens signed by PROVIDER_KEY."""
    settings.GOOGLE_OAUTH_CLIENT_IDS = [GOOGLE_WEB_CLIENT_ID, GOOGLE_IOS_CLIENT_ID]
    settings.APPLE_SIGN_IN_CLIENT_IDS = [APPLE_BUNDLE_ID, APPLE_SERVICES_ID]
    monkeypatch.setattr(
        providers, "_signing_key", lambda provider, token: PROVIDER_KEY.public_key()
    )


@pytest.fixture
def current_terms() -> list[Any]:
    """TERMS version TERMS_VERSION in force, in every language: sign-up accepts it."""
    from legal.tests.factories import publish_version

    return publish_version(version=TERMS_VERSION)
