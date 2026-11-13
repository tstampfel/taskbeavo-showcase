"""A trader's registered business address, set the way staff set it in the admin (helpers
set it themselves with updateHelperProfile's businessAddress)."""

from accounts.models import User
from helpers.models import HelperProfile

ADDRESS = {
    "business_address_street": "Obala Stjepana Radića 12",
    "business_address_postal_code": "20000",
    "business_address_city": "Dubrovnik",
    "business_address_country": "HR",
}


def give_business_address(user: User) -> None:
    HelperProfile.objects.filter(user=user).update(**ADDRESS)
