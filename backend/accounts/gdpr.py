"""The poster's business in "Download my data" and account deletion (plan: "GDPR flows").

Deleting the `User` row cascades to their `PosterBusiness` and its audit events
(`PosterBusinessEvent`), so nothing is left to anonymise.

TODO(BEAV-153): include `export_poster_business` in "Download my data".
"""

from typing import Any

from accounts.models import PosterBusiness, PosterBusinessEvent, User


def export_poster_business(owner: User) -> dict[str, Any]:
    """`owner`'s business in full (their own OIB unmasked) and its audit trail, for their
    own data export. `business` is None without one."""
    business = PosterBusiness.objects.filter(user=owner).first()
    events = PosterBusinessEvent.objects.filter(poster=owner).order_by("created_at", "id")
    return {
        "business": None
        if business is None
        else {
            "kind": business.kind,
            "name": business.name,
            "oib": business.oib,
            "created_at": business.created_at.isoformat(),
            "updated_at": business.updated_at.isoformat(),
        },
        "events": [
            {"type": event.type, "data": event.data, "created_at": event.created_at.isoformat()}
            for event in events
        ],
    }
