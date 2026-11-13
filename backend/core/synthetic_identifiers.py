"""Visibly synthetic OIBs and IBANs for tests, fixtures and the demo seed.

Every OIB or IBAN in the repository comes from here (or has the same shape), so none can
be a real person's or company's. `scripts/check_synthetic_identifiers.py` fails CI on
any other checksum-valid OIB or IBAN. Tests that exercise validation may still use
values with a wrong check digit, which aren't OIBs or IBANs at all.
"""

# Every synthetic OIB starts with these eight zeros, then a two-digit counter and the
# ISO 7064 MOD 11,10 check digit: it passes the check digit, as the helper forms require,
# and is plainly not anyone's number.
SYNTHETIC_OIB_PREFIX = "00000000"

# IBAN lengths of the countries tests use (SWIFT IBAN registry). The account part is all
# zeros and the counter, so the bank code is zeros too and belongs to no bank.
_IBAN_LENGTHS = {"HR": 21, "DE": 22, "AT": 20, "CH": 21, "FR": 27, "GB": 22}
# The longest counter a synthetic IBAN carries (the repository check allows four digits).
_MAX_IBAN_COUNTER = 9999


def synthetic_oib(counter: int) -> str:
    """`SYNTHETIC_OIB_PREFIX`, the two-digit counter and the check digit."""
    if not 0 <= counter <= 99:
        raise ValueError("A synthetic OIB counter has two digits.")
    body = f"{SYNTHETIC_OIB_PREFIX}{counter:02d}"
    remainder = 10
    for digit in body:
        remainder = (remainder + int(digit)) % 10 or 10
        remainder = (remainder * 2) % 11
    return body + str((11 - remainder) % 10)


def synthetic_iban(counter: int, country: str = "HR") -> str:
    """An IBAN of `country` whose account part is zeros and the counter, with valid
    ISO 7064 mod-97 check digits."""
    length = _IBAN_LENGTHS.get(country)
    if length is None:
        raise ValueError(f"No synthetic IBAN length for {country!r}.")
    if not 0 <= counter <= _MAX_IBAN_COUNTER:
        raise ValueError("A synthetic IBAN counter has at most four digits.")
    bban = f"{counter:0{length - 4}d}"
    numeric = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(numeric) % 97:02d}{bban}"
