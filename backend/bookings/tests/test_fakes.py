"""The fake Stripe IDs look like real ones, so the scrubber keeps them under a stripe.* key."""

import random
import re
from uuid import UUID

from bookings.tests.fakes import intent_id
from config.scrubbing import scrub_id


def test_fake_payment_intent_ids_never_hold_nine_digits_and_survive_the_scrubber() -> None:
    rng = random.Random("BEAV-189")  # noqa: S311 - a seeded sample, not a secret
    for _ in range(10_000):
        booking_id = str(UUID(int=rng.getrandbits(128), version=4))
        fake = intent_id(booking_id)
        assert not re.search(r"\d{9}", fake), fake
        assert scrub_id("stripe.object_id", fake) == fake


def test_fake_payment_intent_ids_are_deterministic_and_distinct_per_booking() -> None:
    first, second = (str(UUID(int=n, version=4)) for n in (1, 2**80))
    assert intent_id(first) == intent_id(first)
    assert intent_id(first) != intent_id(second)
