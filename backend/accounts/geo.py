"""Coarse location of a sign-in, for the new-device email: "Munich, Germany".

Looked up in a MaxMind GeoLite2 City database (GEOIP_CITY_DATABASE) and kept only as
that city-and-country text on the Session; the client address itself is never stored.
Without the database, or for an address it doesn't know, the location is "". Only the city
and country names are read (GeoLite EULA §5, §6.3: city-level output, never coordinates or
postal codes). geolite.py keeps the file current; a copy built more than 30 days ago is
never used.
"""

import datetime as dt
import functools
import ipaddress
import logging
import os
from typing import Any

import maxminddb
from django.conf import settings

from accounts import geolite

logger = logging.getLogger(__name__)

# GeoLite2 has no Croatian names; English is the fallback for every language.
_FALLBACK_LANGUAGE = "en"


def coarse_location(client_ip: str, locale: str) -> str:
    """The city and country of `client_ip` in `locale`, else the country alone, else ""."""
    reader = _reader(settings.GEOIP_CITY_DATABASE)
    if reader is None:
        return ""
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return ""
    if not address.is_global:
        return ""
    try:
        record = reader.get(str(address))
    except (ValueError, maxminddb.InvalidDatabaseError):
        return ""
    if not isinstance(record, dict):
        return ""
    parts = [_name(record.get("city"), locale), _name(record.get("country"), locale)]
    return ", ".join(part for part in parts if part)[:100]


def geoip_available() -> bool:
    """Whether GeoLite2 is in use: configured with the MaxMind account that keeps it current
    (accounts/geolite.py). True even while this instance is still downloading its first copy,
    so the imprint's MaxMind attribution doesn't depend on which instance answers."""
    return geolite.configured()


def _name(place: Any, locale: str) -> str:
    names = place.get("names") if isinstance(place, dict) else None
    if not isinstance(names, dict):
        return ""
    name = names.get(locale) or names.get(_FALLBACK_LANGUAGE) or ""
    return name if isinstance(name, str) else ""


def _reader(path: str) -> Any:
    """The open database at `path`, reopened when the weekly update replaces the file; None
    when there is none or its build is over 30 days old (GeoLite EULA)."""
    if not path or not geolite.configured():
        return None
    # Normally running since config/asgi.py; restarts it if this process has none (e.g. forked).
    geolite.start_background_updates()
    try:
        modified = os.stat(path).st_mtime_ns
    except OSError:
        return None
    reader = _open(path, modified)
    if reader is None:
        return None
    built = dt.datetime.fromtimestamp(reader.metadata().build_epoch, tz=dt.UTC)
    return None if geolite.is_expired(built) else reader


@functools.lru_cache(maxsize=2)
def _open(path: str, modified: int) -> Any:
    try:
        return maxminddb.open_database(path)
    except (OSError, maxminddb.InvalidDatabaseError) as exc:
        logger.error("GeoIP database not readable: %s", type(exc).__name__)
        return None
