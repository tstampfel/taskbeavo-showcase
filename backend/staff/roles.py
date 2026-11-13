"""Staff roles and what each may see (BEAV-156; user decision 2026-11-04).

A role is a Django group (`Role`), created with its permissions by migration 0002 (and
later grants such as 0004).
Every staff member sees the booking list, its `booking_events`, the checkout consent,
amounts and normal staff notes. On top of that:

| Capability | support | finance | trust |
|---|---|---|---|
| Exact address, access details, on-site contact's name | yes | | yes |
| Chat text | yes | | yes |
| Task and evidence photos | yes | | yes |
| Stripe IDs, payout account status | yes | yes | |
| On-site contact's phone (others see it masked) | | | yes |
| Reveal a helper's OIB, IBAN, VAT ID (masked otherwise) | | yes | |
| Restricted notes (incident health details) | | | yes |
| Helper profile page (tax IDs masked); edit a trader's business address only | yes | yes | |

The specific reason of a health-revealing cancellation (hospitalisation, bereavement, a
helper's illness; GDPR Art. 9) needs `NO_FAULT_DETAIL` (`view_no_fault_detail`). No group
has it: the founder grants it by hand, to the user or through a group of their own, to the
people who decide no-fault cases. Everyone else sees a label that also covers a reason that
isn't health, so it can't be worked back: "Personal emergency" for hospitalisation,
bereavement and a declared emergency alike, and "personal reason" for a helper's illness or
other reason (BEAV-123, coordinator decision 2026-11-07; `staff.evidence`).

Users' own phone numbers stay out of the admin for everyone (`accounts.admin`).

Users' names and emails (the user admin): support and trust only, and every view of a
user's page is logged (user decision 2026-11-04, review BEAV-156 R1-1). Finance works
from IDs.

Superusers pass every Django permission check, so they see what support and trust
see. The most sensitive capabilities (`GROUP_ONLY`: tax ID reveal, restricted notes, the
contact's full phone) ignore that: a superuser must also be in the finance or trust
group, and otherwise sees the phone masked. `NO_FAULT_DETAIL` ignores it too: a superuser
needs it granted like anyone else. A superuser account is for break-glass only.
"""

from enum import Enum
from typing import Any

from django.db.models import TextChoices


class Role(TextChoices):
    """The group names. Migration 0002 creates them; staff are added to them by hand."""

    SUPPORT = "support"
    FINANCE = "finance"
    TRUST = "trust"


class Capability(Enum):
    """A `staff.StaffAccess` permission codename."""

    EXACT_PLACE = "view_exact_place"
    CHAT = "view_chat"
    PHOTOS = "view_photos"
    STRIPE_IDS = "view_stripe_ids"
    CONTACT_PHONE = "view_contact_phone"
    REVEAL_TAX_IDS = "reveal_tax_ids"
    RESTRICTED_NOTES = "restricted_notes"
    NO_FAULT_DETAIL = "view_no_fault_detail"

    @property
    def perm(self) -> str:
        return f"staff.{self.value}"


# The table above. Migration 0002 grants exactly this; `test_roles` checks they agree.
ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.SUPPORT: frozenset(
        {Capability.EXACT_PLACE, Capability.CHAT, Capability.PHOTOS, Capability.STRIPE_IDS}
    ),
    Role.FINANCE: frozenset({Capability.STRIPE_IDS, Capability.REVEAL_TAX_IDS}),
    Role.TRUST: frozenset(
        {
            Capability.EXACT_PLACE,
            Capability.CHAT,
            Capability.PHOTOS,
            Capability.CONTACT_PHONE,
            Capability.RESTRICTED_NOTES,
        }
    ),
}

# Granted only through a group, never through superuser status or a user permission.
GROUP_ONLY = frozenset(
    {Capability.REVEAL_TAX_IDS, Capability.RESTRICTED_NOTES, Capability.CONTACT_PHONE}
)


# Granted only explicitly (to the user or a group), never through superuser status; no role
# has it by default.
EXPLICIT_ONLY = frozenset({Capability.NO_FAULT_DETAIL})


def is_staff(user: Any) -> bool:
    return bool(getattr(user, "is_active", False) and getattr(user, "is_staff", False))


def can(user: Any, capability: Capability) -> bool:
    """Whether an active staff `user` has `capability`."""
    if not is_staff(user):
        return False
    if capability in GROUP_ONLY:
        return user.groups.filter(
            permissions__content_type__app_label="staff",
            permissions__codename=capability.value,
        ).exists()
    if capability in EXPLICIT_ONLY:
        given = {"content_type__app_label": "staff", "codename": capability.value}
        return (
            user.user_permissions.filter(**given).exists()
            or user.groups.filter(
                **{f"permissions__{key}": value for key, value in given.items()}
            ).exists()
        )
    return bool(user.has_perm(capability.perm))
