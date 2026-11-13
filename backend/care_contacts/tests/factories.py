"""Test on-site contacts."""

from typing import Any

from django.utils import timezone

from accounts.models import User
from care_contacts.models import CareContact


def make_contact(owner: User, **fields: Any) -> CareContact:
    details: dict[str, Any] = {
        "name": "Mama Kata",
        "phone_number": "+385912345678",
        "sms_capable": True,
        "area": "Gruž, Dubrovnik",
        "language": "hr",
        "agreement_confirmed_at": timezone.now(),
        **fields,
    }
    return CareContact.objects.create(owner=owner, **details)
