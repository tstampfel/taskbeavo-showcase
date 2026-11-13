"""Sliding hourly limits on sign-in and phone codes, counted in the rate-limit Redis.

Shared by email codes (accounts/signin.py) and SMS codes (accounts/phone.py). Over a
limit the API answers RATE_LIMITED with `retryAfterSeconds`. While Redis is down the
limits fail closed (RATE_LIMITED): every process counting on its own would multiply
each cap by the process count. Keys never hold an address or number in the clear, only
its keyed digest (`core.crypto.pii_digest`).
"""

import ipaddress
import logging
import math
import time
from collections.abc import Callable
from typing import Any

from django.core.cache import caches
from redis.exceptions import RedisError

from config.graphql_limits import RATE_LIMIT_CACHE, incr_in_window
from core.errors import ApiError, ErrorCode

logger = logging.getLogger(__name__)

HOUR = 60 * 60


def check_hourly(key: str, limit: int) -> None:
    """Count one call against a sliding one-hour window; RATE_LIMITED over `limit`.

    The window is the current clock hour plus the previous one weighted by how much of
    it is still inside the last 60 minutes, so a burst across the hour boundary counts
    as one hour.
    """
    now = time.time()
    bucket = int(now // HOUR)
    elapsed = (now % HOUR) / HOUR
    current = store(lambda cache: incr_in_window(cache, f"{key}:{bucket}", 2 * HOUR))
    previous = store(lambda cache: cache.get(f"{key}:{bucket - 1}")) or 0
    if previous * (1 - elapsed) + current <= limit:
        return
    if current > limit:
        # Wait for the next hour, then until the weighted remainder of this one fits.
        wait = (1 - elapsed) * HOUR + HOUR * (1 - limit / current)
    else:
        # Wait until enough of the previous hour has slid out of the window.
        wait = HOUR * (1 - elapsed - (limit - current) / previous)
    # A true sliding hour is free again within the hour; the estimate can overshoot that.
    raise rate_limited(min(max(math.ceil(wait), 1), HOUR))


def address_bucket(client_ip: str) -> str:
    """The client address; IPv6 by its /64, which one subscriber usually holds whole."""
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def store[T](operation: Callable[[Any], T]) -> T:
    """Run `operation` on the limit store; fail closed (RATE_LIMITED) while Redis is down."""
    try:
        return operation(caches[RATE_LIMIT_CACHE])
    except RedisError as exc:
        logger.warning("Code limit store unavailable: %s", type(exc).__name__)
        raise rate_limited(60) from None


def rate_limited(retry_after_seconds: int) -> ApiError:
    return ApiError(
        ErrorCode.RATE_LIMITED,
        "Too many code requests; try again later.",
        retry_after_seconds=retry_after_seconds,
    )
