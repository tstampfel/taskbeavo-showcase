"""IBAN validation for the helper's payout account, reported under DAC7.

Any EU account works with Stripe (a Croatian one is preferred; docs/PLAN.md "Service
area: tasks in Croatia only"). The IBAN is checked for its country's length and its
ISO 7064 mod-97 check digits. Never log an IBAN or send it to Sentry or Honeycomb, and
never show it to anyone but its owner, masked (hard rule e).
"""

import re

# IBAN lengths of the EU member states (SWIFT IBAN registry).
EU_IBAN_LENGTHS = {
    "AT": 20, "BE": 16, "BG": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IT": 27,
    "LT": 20, "LU": 20, "LV": 21, "MT": 31, "NL": 18, "PL": 28, "PT": 25, "RO": 24,
    "SE": 24, "SI": 19, "SK": 24,
}  # fmt: skip

_SHAPE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]+")


def normalize_iban(value: str) -> str | None:
    """The IBAN in electronic form (no spaces, upper case), or None if it isn't a valid
    IBAN of an EU account."""
    iban = "".join(value.split()).upper()
    if not iban.isascii() or _SHAPE.fullmatch(iban) is None:
        return None
    if EU_IBAN_LENGTHS.get(iban[:2]) != len(iban):
        return None
    rearranged = iban[4:] + iban[:4]
    if int("".join(str(int(char, 36)) for char in rearranged)) % 97 != 1:
        return None
    return iban


def masked_iban(iban: str) -> str:
    """The IBAN in groups of four, all but the country and last four characters hidden."""
    hidden = iban[:2] + "•" * (len(iban) - 6) + iban[-4:]
    return " ".join(hidden[i : i + 4] for i in range(0, len(hidden), 4))
