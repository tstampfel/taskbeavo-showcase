from collections.abc import Iterator
from typing import Any

import pytest
from django.db.models.signals import post_save

from helpers.models import HelperProfile
from payments.tests.factories import connect_account


@pytest.fixture(autouse=True)
def _payout_accounts_active() -> Iterator[None]:
    """These tests are about the profile itself, so every new helper profile here gets an
    ACTIVE Stripe payout account. The PAYOUT_ACCOUNT requirement is tested in
    payments/tests/test_connect.py, without this fixture."""

    def activate(sender: Any, instance: HelperProfile, created: bool, **kwargs: Any) -> None:
        if created:
            connect_account(instance)

    post_save.connect(activate, sender=HelperProfile, dispatch_uid="test-payouts-active")
    yield
    post_save.disconnect(sender=HelperProfile, dispatch_uid="test-payouts-active")
