"""The helper profile in "Download my data" and account deletion (plan: "GDPR flows").

The identity, address, OIB, IBAN and VAT ID are DAC7 records: Porezna requires them for
the DAC7 retention period (plan: "GDPR: deletion requests and what must be kept", NEEDS
REVIEW for the exact period), so account deletion archives them in the restricted
archive before deleting the profile; it never just deletes them. That is why
`HelperProfile.user` is PROTECT.

TODO(BEAV-153): include `export_helper_profile` in "Download my data", and in account
deletion copy `dac7_record` to the restricted archive, then delete the profile. The
profile's Stripe account (`payments.ConnectedAccount`, PROTECT) links payouts to the
accounting records: archive its Stripe account ID with them before deleting it.
"""

from typing import Any

from accounts.models import User
from helpers.models import HelperProfile


def export_helper_profile(owner: User) -> dict[str, Any] | None:
    """`owner`'s helper profile in full, for their own data export; None without one."""
    profile = HelperProfile.objects.filter(user=owner).first()
    if profile is None:
        return None
    return {
        **dac7_record(profile),
        "id": profile.pk,
        "business_address_street": profile.business_address_street,
        "business_address_postal_code": profile.business_address_postal_code,
        "business_address_city": profile.business_address_city,
        "business_address_country": profile.business_address_country,
        "issues_fiscalized_invoices": profile.issues_fiscalized_invoices,
        "categories": list(profile.categories),
        "service_areas": [area.name for area in profile.service_areas.order_by("name")],
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def dac7_record(profile: HelperProfile) -> dict[str, Any]:
    """The DAC7 seller data of `profile`, as the restricted archive keeps it."""
    return {
        "declared_status": profile.declared_status,
        "business_kind": profile.business_kind,
        "business_name": profile.business_name,
        "business_oib": profile.business_oib,
        "legal_first_name": profile.legal_first_name,
        "legal_last_name": profile.legal_last_name,
        "birth_date": profile.birth_date.isoformat() if profile.birth_date else None,
        "address_street": profile.address_street,
        "address_postal_code": profile.address_postal_code,
        "address_city": profile.address_city,
        "address_country": profile.address_country,
        "oib": profile.oib,
        "iban": profile.iban,
        "vat_id": profile.vat_id,
        "residence_country": profile.residence_country,
    }
