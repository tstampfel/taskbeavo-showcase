"""The "IDs only" check for JSON stored in outbox payloads and audit-event data.

Both carry IDs, enum keys and amounts in cents. A key with one of the words below
(`full_name`, `phoneNumber`, `gate_code`) names personal data or access details, and a
string value with an `@` looks like an email address. Either is a bug in the caller.
"""

import re
from typing import Any

_PII_WORDS = frozenset(
    {
        # Identity and contact details.
        "name",
        "email",
        "phone",
        "contact",
        "dob",
        "birth",
        "oib",
        "iban",
        "card",
        # Location and access details (hard rule e).
        "address",
        "street",
        "city",
        "postcode",
        "zip",
        "lat",
        "lng",
        "latitude",
        "longitude",
        "location",
        "gps",
        "doorbell",
        "floor",
        "gate",
        "access",
        "key",
        # Free text.
        "message",
        "chat",
        "text",
        "body",
        "description",
        "note",
        "notes",
        "comment",
        # Credentials.
        "token",
        "password",
        "secret",
    }
)
_KEY_WORDS = re.compile(r"[_\W]+|(?<=[a-z])(?=[A-Z])")
_SCALARS = (str, int, float, bool, type(None))


def check_ids_only(value: Any, path: str) -> None:
    """Raise ValueError if `value` holds anything but IDs, enum keys and amounts."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _PII_WORDS & _key_words(key):
                raise ValueError(f"{path} key {key!r} is not allowed: {path} holds IDs.")
            check_ids_only(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_ids_only(item, f"{path}[{index}]")
    elif isinstance(value, str) and "@" in value:
        # The value itself is not echoed: it is the personal data.
        raise ValueError(f"{path} looks like an email address: {path} holds IDs.")
    elif not isinstance(value, _SCALARS):
        raise ValueError(f"{path} must be JSON scalars, lists or objects.")


def _key_words(key: str) -> set[str]:
    return {word.lower() for word in _KEY_WORDS.split(key) if word}
