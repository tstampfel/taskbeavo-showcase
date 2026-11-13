"""The helper profile in the owner's data export, and its protection from deletion."""

import pytest
from django.db.models import ProtectedError
from django.utils import timezone

from accounts.tests.factories import make_user
from helpers import gdpr, services
from helpers.models import HelperProfile

pytestmark = pytest.mark.django_db


def test_the_export_holds_the_owners_full_data() -> None:
    luka = make_user("luka", phone_verified_at=timezone.now())
    assert gdpr.export_helper_profile(luka) is None
    services.update_profile(
        luka,
        services.ProfileChanges(
            status=HelperProfile.Status.STUDENT, oib="00000000698", iban="HR0600000000000000160"
        ),
    )

    exported = gdpr.export_helper_profile(luka)

    assert exported is not None
    assert (exported["oib"], exported["iban"]) == ("00000000698", "HR0600000000000000160")
    assert exported["declared_status"] == "STUDENT"


def test_the_user_row_cant_be_deleted_under_the_dac7_records() -> None:
    luka = make_user("luka")
    services.update_profile(luka, services.ProfileChanges(status=HelperProfile.Status.STUDENT))

    with pytest.raises(ProtectedError):
        luka.delete()
