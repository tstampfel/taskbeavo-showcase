"""The service-area check (hard rule j) and the waitlist: joining, leaving, retention."""

import datetime as dt
import hashlib
import re
import secrets

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone

from accounts.models import normalize_email
from areas.models import WAITLIST_CONSENT_VERSION, ServiceArea, WaitlistEntry
from core.categories import TaskCategory
from core.errors import ApiError, ErrorCode

WGS84 = 4326

_COUNTRY_CODE = re.compile(r"[A-Z]{2}")
_CITY_MAX_LENGTH = 100
# The email column's length; validate_email alone accepts up to 320 characters.
_EMAIL_MAX_LENGTH = WaitlistEntry._meta.get_field("email").max_length or 254


def point_from_lat_lng(lat: float, lng: float) -> Point:
    """A WGS84 point from latitude and longitude, as clients send them.

    Raises ValueError for coordinates no place has; resolvers turn that into their own
    validation error code.
    """
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Latitude or longitude out of range.")
    return Point(lng, lat, srid=WGS84)


def assert_in_service_area(point: Point) -> ServiceArea:
    """Returns the active Croatian service area covering `point`, or raises
    `ApiError(OUT_OF_SERVICE_AREA)`.

    This is the server-side rule; the client's check is only a convenience. Call it, with
    the task's point, in every place a task's location starts to matter or changes:

    - `postTask`, before the task is saved;
    - any mutation that changes a task's address, before saving (the task keeps its old
      address when this raises);
    - `sendOffer`, on the task's stored point;
    - booking creation (`acceptOffer`), on the task's stored point, before any payment.

    Coverage uses PostGIS ST_Covers, so a point on an area's boundary counts as inside.
    Only areas with `active` set and country HR count. A point without an SRID is read as
    WGS84; one in another SRID is transformed. On OUT_OF_SERVICE_AREA the client offers
    the waitlist (`joinWaitlist`).
    """
    if point.srid is None:
        point = Point(point.x, point.y, srid=WGS84)
    elif point.srid != WGS84:
        point = point.transform(WGS84, clone=True)
    area = (
        ServiceArea.objects.filter(
            active=True, country_code=ServiceArea.Country.HR, polygon__covers=point
        )
        .order_by("pk")
        .first()
    )
    if area is None:
        raise ApiError(ErrorCode.OUT_OF_SERVICE_AREA, "The point is outside every service area.")
    return area


def join_waitlist(
    *,
    email: str,
    city: str,
    country_code: str,
    category: TaskCategory,
    locale: str,
    consent_launch_email: bool,
) -> WaitlistEntry:
    """Saves or refreshes the waitlist entry for this email and city.

    The consent (one launch email for this city) is required and recorded with its text
    version, time and language. Joining again for the same email, city and country
    updates that entry instead of adding one. The caller only gets back what it sent plus
    the entry's ID (random, so it says nothing about when or whether the address joined
    before), so the response doesn't tell whether an address was already on the list.
    """
    if not consent_launch_email:
        raise ApiError(
            ErrorCode.WAITLIST_CONSENT_REQUIRED, "Joining the waitlist needs the consent."
        )
    address = normalize_email(email)
    try:
        if len(address) > _EMAIL_MAX_LENGTH:
            raise ValidationError("Too long.")
        validate_email(address)
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_EMAIL, "Not a valid email address.") from None
    city = " ".join(city.split())
    country_code = country_code.strip().upper()
    if (
        not city
        or len(city) > _CITY_MAX_LENGTH
        or not city.isprintable()
        or _COUNTRY_CODE.fullmatch(country_code) is None
    ):
        raise ApiError(ErrorCode.INVALID_WAITLIST_DETAILS, "Check the city and country code.")

    entry, _created = WaitlistEntry.objects.update_or_create(
        email=address,
        city_key=city_key(city),
        country_code=country_code,
        defaults={
            "city": city,
            "category": category,
            "consent_launch_email": True,
            "consent_version": WAITLIST_CONSENT_VERSION,
            "consent_given_at": timezone.now(),
            "consent_locale": locale,
        },
    )
    return entry


def city_key(city: str) -> str:
    """What entries for one city share however it was typed: casefolded, single spaces."""
    return " ".join(city.split()).casefold()[:_CITY_MAX_LENGTH]


def issue_leave_token(entry: WaitlistEntry) -> str:
    """A new withdrawal token for `entry`, for the launch email's "leave the waitlist" link.

    Only its SHA-256 is stored; issuing another replaces it, so an older link stops
    working. Never return it from the API: only the email holds it.
    """
    token = secrets.token_urlsafe(32)
    entry.leave_token_digest = _token_digest(token)
    entry.save(update_fields=["leave_token_digest"])
    return token


def leave_waitlist(token: str) -> None:
    """Deletes the entry the withdrawal token belongs to.

    Says nothing about whether one did: an unknown, used or malformed token does nothing,
    the same as a valid one from the caller's point of view.
    """
    if not token or len(token) > 200:
        return
    WaitlistEntry.objects.filter(leave_token_digest=_token_digest(token)).delete()


def purge_waitlist() -> int:
    """Deletes entries whose launch email was sent, and entries older than
    WAITLIST_RETENTION_DAYS, whose city never launched (storage limitation, rule h)."""
    cutoff = timezone.now() - dt.timedelta(days=settings.WAITLIST_RETENTION_DAYS)
    deleted, _ = WaitlistEntry.objects.filter(
        Q(launch_email_sent_at__isnull=False) | Q(created_at__lt=cutoff)
    ).delete()
    return deleted


def _token_digest(token: str) -> str:
    # A random 256-bit token needs no salt or slow hash; equality on the digest also
    # keeps the lookup's timing independent of the token.
    return hashlib.sha256(token.encode()).hexdigest()
