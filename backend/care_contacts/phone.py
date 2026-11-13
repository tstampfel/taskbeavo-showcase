"""An on-site contact's phone: any valid number, mobile or landline, Croatian or foreign.

Unlike the account's own number (accounts/phone.py, mobile only), a contact may only have
a landline; many elderly parents do. The number is stored in E.164 with whether it can
receive SMS, so the evening-before reminder skips SMS for landlines and for countries
TaskBeavo can't text (`core.sms.sender_for`, PHONE_DENIED_REGIONS).
"""

from dataclasses import dataclass

import phonenumbers
from django.conf import settings
from phonenumbers import PhoneNumberType

from core.errors import ApiError, ErrorCode
from core.sms import sender_for

# The region a national number ("020 123 456") is read in: contacts live in Croatia.
CONTACT_PHONE_REGION = "HR"
_SMS_TYPES = frozenset({PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE})
# Numbers a person has. Premium-rate, shared-cost, toll-free, UAN, pager, voicemail and
# unknown types are refused: a helper may dial the number.
_PERSONAL_TYPES = _SMS_TYPES | {PhoneNumberType.FIXED_LINE}


@dataclass(frozen=True)
class ContactPhone:
    e164: str
    sms_capable: bool


def normalize_contact_phone(raw: str) -> ContactPhone:
    """The number in E.164 and whether TaskBeavo can text it, or INVALID_CARE_CONTACT_PHONE.

    Only mobile and fixed-line numbers qualify.
    """
    text = raw.strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    try:
        number = phonenumbers.parse(text, CONTACT_PHONE_REGION)
    except phonenumbers.NumberParseException:
        raise _invalid() from None
    if not phonenumbers.is_valid_number(number):
        raise _invalid()
    number_type = phonenumbers.number_type(number)
    if number_type not in _PERSONAL_TYPES:
        raise _invalid()
    region = phonenumbers.region_code_for_number(number) or ""
    sms_capable = (
        number_type in _SMS_TYPES
        and bool(region)
        and region not in settings.PHONE_DENIED_REGIONS
        and sender_for(region) is not None
    )
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return ContactPhone(e164=e164, sms_capable=sms_capable)


def _invalid() -> ApiError:
    return ApiError(ErrorCode.INVALID_CARE_CONTACT_PHONE, "Not a valid phone number.")
