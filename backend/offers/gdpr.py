"""Offers in the helper's account deletion and data export (plan: "GDPR flows").

Deleting the `User` row cascades to their offers and the offers' events; deleting a
poster's task cascades to the offers on it.

TODO(BEAV-153): include `export_offers` in "Download my data". Once bookings exist, an
accepted offer falls under the booking's accounting retention instead of being deleted.
"""

from typing import Any

from accounts.models import User
from offers.models import Offer


def export_offers(helper: User) -> list[dict[str, Any]]:
    """`helper`'s offers in full, for their data export."""
    return [
        {
            "id": str(offer.pk),
            "task_id": str(offer.task_id),
            "status": offer.status,
            "withdrawn_reason": offer.withdrawn_reason,
            "price_cents": offer.price_cents,
            "message": offer.message,
            "expires_at": offer.expires_at.isoformat(),
            "created_at": offer.created_at.isoformat(),
            "closed_at": offer.closed_at.isoformat() if offer.closed_at else None,
        }
        for offer in Offer.objects.filter(helper=helper)
    ]
