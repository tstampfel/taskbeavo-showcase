"""Encryption at rest for personal data that must be read back: phone, OIB, IBAN (rule e).

Values are encrypted with Fernet (AES-128-CBC with an HMAC-SHA256 tag) under
FIELD_ENCRYPTION_KEY, a comma-separated list of Fernet keys: the first encrypts, every
one decrypts, so a key is rotated by putting the new key first and re-saving the rows.

Fernet is randomised, so an encrypted column can't be filtered or made unique. Store a
`pii_digest` of the value beside it for lookups and unique constraints.

Rotating FIELD_ENCRYPTION_KEY: put the new key first, deploy, run
`manage.py reencrypt_fields`, and only then drop the old key. A value no configured key
opens fails every load of its row, which for `User.phone_number` means every request of
that user.
"""

import functools
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models
from django.utils.crypto import salted_hmac


@functools.cache
def _fernet() -> MultiFernet:
    keys = [key.strip() for key in settings.FIELD_ENCRYPTION_KEY.split(",") if key.strip()]
    return MultiFernet([Fernet(key) for key in keys])


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Raises `cryptography.fernet.InvalidToken` when no configured key opened it."""
    return _fernet().decrypt(token.encode()).decode()


def pii_digest(purpose: str, value: str) -> str:
    """A keyed SHA-256 digest of `value`, for finding or de-duplicating encrypted values.

    Keyed with PII_DIGEST_KEY, so a leaked table can't be reversed by hashing every phone
    number. `purpose` separates uses ("phone", "oib"). The key is never rotated without
    running `manage.py recompute_pii_digests` right after: stored digests would stop
    matching new ones, and unique constraints on them would stop catching duplicates.
    """
    # django-types lacks salted_hmac's `algorithm` parameter.
    digest = salted_hmac(
        f"taskbeavo.pii.{purpose}.v1",
        value,
        secret=settings.PII_DIGEST_KEY,
        algorithm="sha256",  # pyright: ignore[reportCallIssue]
    )
    return digest.hexdigest()


class EncryptedTextField(models.TextField):
    """A text column stored encrypted (`encrypt`). "" and NULL are stored as they are.

    Lookups other than `isnull` don't work on it: query the digest column instead.
    """

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return encrypt(value)

    def from_db_value(self, value: str | None, expression: Any, connection: Any) -> str | None:
        if value is None or value == "":
            return value
        return decrypt(value)


__all__ = ["EncryptedTextField", "InvalidToken", "decrypt", "encrypt", "pii_digest"]
