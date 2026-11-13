"""Encryption at rest and keyed digests (core/crypto.py)."""

import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.test import override_settings

from core import crypto


@pytest.fixture(autouse=True)
def _fresh_keys():
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def test_round_trip_and_randomised() -> None:
    first, second = crypto.encrypt("+385921234567"), crypto.encrypt("+385921234567")

    assert first != second
    assert "385921234567" not in first
    assert crypto.decrypt(first) == crypto.decrypt(second) == "+385921234567"


def test_rotation_decrypts_with_an_older_key() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    with override_settings(FIELD_ENCRYPTION_KEY=old):
        token = crypto.encrypt("secret")
    crypto._fernet.cache_clear()

    with override_settings(FIELD_ENCRYPTION_KEY=f"{new},{old}"):
        assert crypto.decrypt(token) == "secret"
        assert Fernet(new.encode()).decrypt(crypto.encrypt("x").encode()) == b"x"
    crypto._fernet.cache_clear()

    with override_settings(FIELD_ENCRYPTION_KEY=new), pytest.raises(InvalidToken):
        crypto.decrypt(token)


def test_digest_is_stable_keyed_and_purpose_bound() -> None:
    digest = crypto.pii_digest("phone", "+385921234567")

    assert digest == crypto.pii_digest("phone", "+385921234567")
    assert digest != crypto.pii_digest("oib", "+385921234567")
    with override_settings(PII_DIGEST_KEY="another-key"):
        assert digest != crypto.pii_digest("phone", "+385921234567")
    # Independent of SECRET_KEY, so that one can be rotated after a leak.
    with override_settings(SECRET_KEY="another-secret-key"):
        assert digest == crypto.pii_digest("phone", "+385921234567")
