"""On-site contacts in the poster's account deletion and data export (plan: "GDPR flows").

Contacts are deleted with the account (retention: "family/on-site contacts ... Delete").
Deleting the `User` row cascades to them; `delete_care_contacts` is for a deletion flow
that anonymises the user row instead of deleting it.

TODO(BEAV-153): call `delete_care_contacts` from the account deletion flow and include
`export_care_contacts` in "Download my data" once those flows exist.
"""

from typing import Any

from accounts.models import User
from care_contacts.models import CareContact


def delete_care_contacts(owner: User) -> int:
    """Delete every on-site contact of `owner`; returns how many there were."""
    deleted, _ = CareContact.objects.filter(owner=owner).delete()
    return deleted


def export_care_contacts(owner: User) -> list[dict[str, Any]]:
    """`owner`'s on-site contacts in full, for their data export."""
    return [
        {
            "id": contact.pk,
            "name": contact.name,
            "phone_number": contact.phone_number,
            "sms_capable": contact.sms_capable,
            "area": contact.area,
            "language": contact.language,
            "no_smartphone": contact.no_smartphone,
            "agreement_confirmed_at": contact.agreement_confirmed_at.isoformat(),
            "created_at": contact.created_at.isoformat(),
            "updated_at": contact.updated_at.isoformat(),
        }
        for contact in CareContact.objects.filter(owner=owner)
    ]
