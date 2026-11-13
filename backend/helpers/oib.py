"""OIB (Croatian personal identification number) validation.

An OIB is 11 ASCII digits; the last is a check digit over the first ten computed with
ISO 7064 MOD 11,10 (docs/PLAN.md "The DAC7 job turns payouts into an annual report").
Never log an OIB or send it to Sentry or Honeycomb (hard rule e).
"""

_LENGTH = 11


def is_valid_oib(value: str) -> bool:
    if len(value) != _LENGTH or not (value.isascii() and value.isdigit()):
        return False
    remainder = 10
    for digit in value[:-1]:
        remainder = (remainder + int(digit)) % 10 or 10
        remainder = (remainder * 2) % 11
    check = (11 - remainder) % 10
    return check == int(value[-1])


def masked_oib(value: str) -> str:
    """The OIB with all but its last four digits hidden, for showing it to its owner."""
    return "•" * (len(value) - 4) + value[-4:]


def masked_vat_id(value: str) -> str:
    """The VAT ID with its country prefix and, if it is long enough, its last four
    characters showing. A Croatian VAT ID is HR and the OIB, so it is masked like one."""
    tail = value[-4:] if len(value) > 8 else ""
    return value[:2] + "•" * (len(value) - 2 - len(tail)) + tail
