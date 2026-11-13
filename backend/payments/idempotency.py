"""Idempotency keys for outbound Stripe calls (hard rule a).

Every Stripe call that creates or moves money passes a key derived from the action,
never a random value per attempt, so a retried request (a double tap, a Celery retry,
a webhook redelivery) returns the first result instead of charging or paying twice:

    stripe.Payout.create(..., idempotency_key=idempotency_key("booking", booking.id, "release"))
    # "booking:42:release:v1"

    idempotency_key("booking", booking.id, "cancel", "late_cancel")
    # "booking:42:cancel:late_cancel:v1"

Bump `version` only when the same action must deliberately run again with new
parameters; Stripe rejects a reused key whose parameters changed.
"""

import re

# Stripe accepts keys of up to 255 characters.
MAX_LENGTH = 255
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
# What `idempotency_key` builds: aggregate, ID, action, any qualifiers, then the version.
_KEY = re.compile(r"[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+){2,}:v[1-9][0-9]*")


def idempotency_key(
    aggregate: str, aggregate_id: int | str, action: str, *qualifiers: str, version: int = 1
) -> str:
    """Build `{aggregate}:{id}:{action}[:{qualifier}...]:v{version}`."""
    if isinstance(aggregate_id, bool):
        raise ValueError("aggregate_id must be an ID, not a bool.")
    if version < 1:
        raise ValueError("version starts at 1.")
    segments = [aggregate, str(aggregate_id), action, *qualifiers]
    for segment in segments:
        if not _SEGMENT.fullmatch(segment):
            raise ValueError(f"Idempotency key segment {segment!r} must match [A-Za-z0-9_-]+.")
    key = ":".join([*segments, f"v{version}"])
    if len(key) > MAX_LENGTH:
        raise ValueError(f"Idempotency key is longer than {MAX_LENGTH} characters.")
    return key


def check_idempotency_key(key: str) -> str:
    """Return `key` if it has the shape `idempotency_key` builds, else raise ValueError.

    The Stripe client (payments/stripe_client.py) checks every key with this, so a
    random per-attempt value, which would let a retry charge or pay twice, is refused.
    """
    if not isinstance(key, str) or len(key) > MAX_LENGTH or not _KEY.fullmatch(key):
        raise ValueError(
            "Stripe idempotency keys must come from payments.idempotency.idempotency_key()."
        )
    return key
