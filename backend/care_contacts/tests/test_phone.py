"""An on-site contact's phone may be any valid number; only mobiles are marked SMS-capable."""

from typing import Any

import pytest

from care_contacts.phone import normalize_contact_phone
from core.errors import ApiError, ErrorCode


@pytest.mark.parametrize(
    ("raw", "e164", "sms_capable"),
    [
        ("091 234 5678", "+385912345678", True),  # Croatian mobile, national format
        ("+385 20 123 456", "+38520123456", False),  # Dubrovnik landline
        ("020 123 456", "+38520123456", False),
        ("0049 151 23456789", "+4915123456789", True),  # German mobile
        ("+49 30 1234567", "+49301234567", False),  # Berlin landline
    ],
)
def test_valid_numbers_are_normalised(raw: str, e164: str, sms_capable: bool) -> None:
    phone = normalize_contact_phone(raw)
    assert (phone.e164, phone.sms_capable) == (e164, sms_capable)


def test_mobiles_of_countries_we_cant_text_are_not_sms_capable(settings: Any) -> None:
    settings.PHONE_DENIED_REGIONS = frozenset({"DE"})
    assert normalize_contact_phone("+49 151 23456789").sms_capable is False


@pytest.mark.parametrize("raw", ["", "not a number", "123", "+385 1"])
def test_invalid_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ApiError) as raised:
        normalize_contact_phone(raw)
    assert raised.value.code is ErrorCode.INVALID_CARE_CONTACT_PHONE


@pytest.mark.parametrize(
    "raw",
    [
        "+385 60 123 456",  # Croatian premium rate (060)
        "+385 800 1234",  # Croatian toll free (0800)
        "+49 900 1234567",  # German premium rate (0900)
    ],
)
def test_service_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ApiError) as raised:
        normalize_contact_phone(raw)
    assert raised.value.code is ErrorCode.INVALID_CARE_CONTACT_PHONE
