import pytest

from payments.idempotency import check_idempotency_key, idempotency_key


def test_key_is_derived_from_the_action() -> None:
    assert idempotency_key("booking", 42, "release") == "booking:42:release:v1"
    assert idempotency_key("booking", 42, "release") == idempotency_key("booking", "42", "release")


def test_qualifiers_and_version() -> None:
    assert (
        idempotency_key("booking", 42, "cancel", "late_cancel", version=2)
        == "booking:42:cancel:late_cancel:v2"
    )
    assert idempotency_key("booking", 42, "payment_intent") == "booking:42:payment_intent:v1"


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        (("booking", 42, ""), {}),
        (("booking", "4:2", "release"), {}),
        (("booking", 42, "re lease"), {}),
        (("booking", True, "release"), {}),
        (("booking", 42, "release"), {"version": 0}),
        (("booking", "x" * 250, "release"), {}),
    ],
)
def test_ambiguous_or_invalid_keys_are_rejected(args, kwargs) -> None:
    with pytest.raises(ValueError):
        idempotency_key(*args, **kwargs)


@pytest.mark.parametrize(
    "key",
    [
        idempotency_key("booking", 42, "release"),
        idempotency_key("booking", 42, "cancel", "late_cancel", version=12),
        idempotency_key("account", "acct_1Test", "onboarding_link"),
    ],
)
def test_check_accepts_every_key_the_builder_makes(key: str) -> None:
    assert check_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "3f2c1b8e-0d5e-4b5e-9d1c-6b2f0a8e9c11",
        "booking:42:v1",
        "booking:42:release",
        "booking:42:release:v0",
        "booking:42:release:v1:",
        "booking:4 2:release:v1",
        "booking:42:" + "a" * 250 + ":v1",
    ],
)
def test_check_refuses_keys_not_from_the_builder(key: str) -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        check_idempotency_key(key)
