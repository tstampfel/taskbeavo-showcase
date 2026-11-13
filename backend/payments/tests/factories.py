from itertools import count

from helpers.models import HelperProfile
from payments.models import ConnectedAccount

_numbers = count(1)


def connect_account(
    profile: HelperProfile,
    status: ConnectedAccount.Status = ConnectedAccount.Status.ACTIVE,
    stripe_account_id: str | None = None,
) -> ConnectedAccount:
    """A stored Stripe Express account for `profile`, ACTIVE unless told otherwise."""
    active = status == ConnectedAccount.Status.ACTIVE
    return ConnectedAccount.objects.create(
        helper=profile,
        stripe_account_id=stripe_account_id or f"acct_1TestHelper{next(_numbers):05d}",
        status=status,
        details_submitted=status != ConnectedAccount.Status.INCOMPLETE,
        charges_enabled=active,
        payouts_enabled=active,
        card_payments_capability="active" if active else "inactive",
        transfers_capability="active" if active else "inactive",
    )
