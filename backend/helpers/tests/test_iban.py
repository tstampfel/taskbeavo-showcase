"""IBAN validation: an EU account, mod-97 check digits (docs/PLAN.md "Service area")."""

import pytest

from helpers.iban import masked_iban, normalize_iban


@pytest.mark.parametrize(
    ("typed", "normalized"),
    [
        ("HR0600000000000000160", "HR0600000000000000160"),
        ("hr06 0000 0000 0000 0016 0", "HR0600000000000000160"),
        ("DE09 0000 0000 0000 0000 01", "DE09000000000000000001"),
        ("AT88 0000 0000 0000 0001", "AT880000000000000001"),
    ],
)
def test_valid_eu_ibans_are_normalized(typed: str, normalized: str) -> None:
    assert normalize_iban(typed) == normalized


@pytest.mark.parametrize(
    "typed",
    [
        "HR0600000000000000161",  # wrong check digits
        "HR060000000000000016",  # too short for Croatia
        "DE0900000000000000000",  # too short for Germany
        "GB88000000000000000001",  # valid, but not an EU account
        "CH0900000000000000001",  # valid, but not an EU account
        "HR06-0000-0000-0000-0016-0",
        "",
        "1234",
    ],
)
def test_invalid_or_non_eu_ibans_are_rejected(typed: str) -> None:
    assert normalize_iban(typed) is None


def test_masked_iban_keeps_country_and_last_four() -> None:
    assert masked_iban("HR0600000000000000160") == "HR•• •••• •••• •••• •016 0"
