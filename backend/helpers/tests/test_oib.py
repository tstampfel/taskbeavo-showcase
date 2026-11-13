"""OIB check-digit validation (ISO 7064 MOD 11,10; docs/PLAN.md "The DAC7 job")."""

import pytest

from core.synthetic_identifiers import synthetic_oib
from helpers.oib import is_valid_oib, masked_oib, masked_vat_id

# Synthetic OIBs with correct check digits (core.synthetic_identifiers).
VALID = [synthetic_oib(69), synthetic_oib(94), synthetic_oib(0)]
# "00000000698" in full-width digits.
FULL_WIDTH_OIB = "\uff10" * 8 + "\uff16\uff19\uff18"


@pytest.mark.parametrize("oib", VALID)
def test_valid_oibs_pass(oib: str) -> None:
    assert is_valid_oib(oib)


@pytest.mark.parametrize(
    "oib",
    [
        "00000000699",  # wrong check digit
        "00000000940",  # wrong check digit
        "0000000069",  # too short
        "000000006980",  # too long
        "0000000069a",  # not a digit
        "",
        "00000 000698",
        FULL_WIDTH_OIB,  # not ASCII digits
    ],
)
def test_invalid_oibs_fail(oib: str) -> None:
    assert not is_valid_oib(oib)


def test_masked_oib_keeps_the_last_four_digits() -> None:
    assert masked_oib("00000000698") == "•••••••0698"


def test_masked_vat_id_keeps_the_prefix_and_last_four() -> None:
    assert masked_vat_id("HR00000000698") == "HR•••••••0698"
    assert masked_vat_id("ATU1234") == "AT•••••"
