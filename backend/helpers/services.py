"""Filling in the helper profile, and whether a helper may send offers.

Docs/PLAN.md "Becoming a helper" and "The DAC7 job": a helper can browse right away but
sends offers only once the status declaration, identity and tax data (DAC7), service
areas and categories are complete and their mobile number is verified. Every value is
checked here, on the server: the OIB's check digit, the IBAN's check digits, a Croatian
home address.

Never log or put in errors any value a helper typed: names, birth date, address, OIB,
IBAN or VAT ID (hard rule e).
"""

import datetime as dt
import re
from dataclasses import dataclass
from enum import Enum

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import User
from accounts.permissions import require_verified_phone
from areas.models import ServiceArea
from core.categories import TaskCategory
from core.crypto import pii_digest
from core.errors import ApiError, ErrorCode
from helpers.countries import RESIDENCE_COUNTRIES
from helpers.iban import normalize_iban
from helpers.models import BusinessVerification, HelperProfile
from helpers.oib import is_valid_oib
from moderation.contact_filter import Verdict, filter_contact_details
from payments.models import ConnectedAccount

ADULT_AGE = 18
_MAX_AGE = 120
# Croatian postal codes are five digits, 10000 (Zagreb) to 53xxx.
_HR_POSTAL_CODE = re.compile(r"[1-5][0-9]{4}")
# EU VAT numbers: a two-letter prefix (EL for Greece) and 2 to 13 letters or digits.
_VAT_ID = re.compile(r"[A-Z]{2}[A-Z0-9]{2,13}")


class Requirement(Enum):
    """Something a helper still has to provide before sending offers."""

    STATUS = "STATUS"
    BUSINESS_DETAILS = "BUSINESS_DETAILS"
    # A company's name and OIB wait for a check by staff (later the court register).
    BUSINESS_VERIFICATION = "BUSINESS_VERIFICATION"
    # An obrt's or company's registered business address, shown in the contract
    # confirmation (CRD Art. 6(1)(c)).
    BUSINESS_ADDRESS = "BUSINESS_ADDRESS"
    LEGAL_NAME = "LEGAL_NAME"
    BIRTH_DATE = "BIRTH_DATE"
    ADDRESS = "ADDRESS"
    OIB = "OIB"
    IBAN = "IBAN"
    RESIDENCE_COUNTRY = "RESIDENCE_COUNTRY"
    # A Stripe Express account that can receive destination charges (payments.connect).
    PAYOUT_ACCOUNT = "PAYOUT_ACCOUNT"
    SERVICE_AREAS = "SERVICE_AREAS"
    CATEGORIES = "CATEGORIES"
    PHONE_VERIFIED = "PHONE_VERIFIED"


@dataclass(frozen=True)
class Address:
    street: str
    postal_code: str
    city: str
    country: str


@dataclass(frozen=True)
class Business:
    """`oib` is the company's OIB, required for COMPANY. An obrt has no OIB of its own
    (it is the owner's): for OBRT leave it None, or give the owner's own OIB."""

    kind: HelperProfile.BusinessKind
    name: str
    oib: str | None
    issues_fiscalized_invoices: bool


@dataclass(frozen=True)
class ProfileChanges:
    """Fields to set; None leaves a field as it is. `vat_id=""` removes the VAT ID."""

    status: HelperProfile.Status | None = None
    business: Business | None = None
    # The registered business address of an obrt or company, never the home address.
    business_address: Address | None = None
    legal_first_name: str | None = None
    legal_last_name: str | None = None
    birth_date: dt.date | None = None
    address: Address | None = None
    oib: str | None = None
    iban: str | None = None
    vat_id: str | None = None
    residence_country: str | None = None
    service_area_ids: list[str] | None = None
    categories: list[TaskCategory] | None = None


def update_profile(user: User, changes: ProfileChanges) -> HelperProfile:
    """Creates the user's helper profile on first use, then applies `changes`.

    Validation fails the whole update: nothing is saved when one field is invalid.
    """
    try:
        with transaction.atomic():
            profile, _ = HelperProfile.objects.select_for_update().get_or_create(user=user)
            _apply(profile, changes)
            profile.save()
            if changes.service_area_ids is not None:
                profile.service_areas.set(_service_areas(changes.service_area_ids))
    except IntegrityError:
        # Two helpers saving the same OIB at once: the unique digest stops the second.
        # Any other integrity error is a bug, not the helper's OIB.
        if changes.oib is not None and _oib_taken(user, pii_digest("oib", changes.oib.strip())):
            raise ApiError(ErrorCode.OIB_IN_USE, "The OIB belongs to another helper.") from None
        raise
    return profile


def _oib_taken(user: User, digest: str) -> bool:
    return HelperProfile.objects.filter(oib_digest=digest).exclude(user=user).exists()


def profile_of(user: User) -> HelperProfile | None:
    return HelperProfile.objects.filter(user=user).first()


def missing_requirements(
    user: User, profile: HelperProfile | None, areas: list[ServiceArea] | None = None
) -> list[Requirement]:
    """What `user` still has to provide before sending offers, in onboarding order.

    `areas` are the profile's active service areas, if the caller already loaded them.

    TODO(BEAV-96): add the accepted helper terms.
    """
    missing: list[Requirement] = []
    if profile is None:
        business = {
            Requirement.BUSINESS_DETAILS,
            Requirement.BUSINESS_VERIFICATION,
            Requirement.BUSINESS_ADDRESS,
        }
        missing.extend(r for r in Requirement if r not in business)
        if user.phone_verified_at is not None:
            missing.remove(Requirement.PHONE_VERIFIED)
        return missing
    if not profile.declared_status:
        missing.append(Requirement.STATUS)
    if profile.is_trader and not _business_complete(profile):
        missing.append(Requirement.BUSINESS_DETAILS)
    elif profile.needs_business_verification:
        missing.append(Requirement.BUSINESS_VERIFICATION)
    if profile.is_trader and not profile.business_address:
        missing.append(Requirement.BUSINESS_ADDRESS)
    if not (profile.legal_first_name and profile.legal_last_name):
        missing.append(Requirement.LEGAL_NAME)
    if profile.birth_date is None:
        missing.append(Requirement.BIRTH_DATE)
    if not (
        profile.address_street
        and profile.address_postal_code
        and profile.address_city
        and profile.address_country == "HR"
    ):
        missing.append(Requirement.ADDRESS)
    if not profile.oib:
        missing.append(Requirement.OIB)
    if not profile.iban:
        missing.append(Requirement.IBAN)
    if profile.residence_country not in RESIDENCE_COUNTRIES:
        missing.append(Requirement.RESIDENCE_COUNTRY)
    if not ConnectedAccount.objects.filter(
        helper=profile, status=ConnectedAccount.Status.ACTIVE
    ).exists():
        missing.append(Requirement.PAYOUT_ACCOUNT)
    if not (areas if areas is not None else _active_areas(profile).exists()):
        missing.append(Requirement.SERVICE_AREAS)
    if not profile.categories:
        missing.append(Requirement.CATEGORIES)
    if user.phone_verified_at is None:
        missing.append(Requirement.PHONE_VERIFIED)
    return missing


# Payout account states in which the helper finished Stripe onboarding. While Stripe asks
# for more, checks or pauses the account, offers wait (ACTIVE only) but the public profile
# stays, so posters with open bookings still see their helper (plan: "Payout failures and
# account problems"; product decision on BEAV-95).
_PUBLIC_PAYOUT_STATUSES = frozenset(
    {
        ConnectedAccount.Status.ACTIVE,
        ConnectedAccount.Status.NEEDS_INFO,
        ConnectedAccount.Status.VERIFYING,
        ConnectedAccount.Status.RESTRICTED,
    }
)


def is_public(user: User, profile: HelperProfile, areas: list[ServiceArea] | None = None) -> bool:
    """Whether `helperProfile` shows this helper: an active user whose profile is complete
    apart from a payout account Stripe is still working on. Hidden without an account,
    before onboarding was submitted (INCOMPLETE) and once Stripe rejected it."""
    if not user.is_active:
        return False
    missing = set(missing_requirements(user, profile, areas)) - {Requirement.PAYOUT_ACCOUNT}
    return (
        not missing
        and ConnectedAccount.objects.filter(
            helper=profile, status__in=_PUBLIC_PAYOUT_STATUSES
        ).exists()
    )


def _business_complete(profile: HelperProfile) -> bool:
    if not (profile.business_kind and profile.business_name and profile.business_oib):
        return False
    # An obrt is identified by its owner's OIB (`_apply_oib` keeps the copy in sync).
    return (
        profile.business_kind != HelperProfile.BusinessKind.OBRT
        or profile.business_oib == profile.oib
    )


def verify_business(
    profile_id: int, *, business_oib: str, business_name: str, by: BusinessVerification
) -> HelperProfile:
    """Marks a company's name and OIB as confirmed: by staff now, by the court-register
    check (BEAV-180) later. The only place the flag is set.

    `business_oib` and `business_name` are what was actually checked. The profile is
    re-read under a row lock, and nothing is verified unless it is still a company with
    exactly that OIB and name: a helper who changed them after the check, or while the
    check ran, keeps an unverified profile. The name is compared with whitespace
    collapsed, as profiles store it.

    Raises ValueError (with IDs only) when the profile isn't a company with complete
    business details, or its details differ from the checked ones.
    """
    with transaction.atomic():
        profile = HelperProfile.objects.select_for_update().get(pk=profile_id)
        if not (
            profile.is_trader
            and profile.business_kind == HelperProfile.BusinessKind.COMPANY
            and _business_complete(profile)
        ):
            raise ValueError(f"HelperProfile {profile.pk} isn't a company with business details.")
        if (profile.business_oib, profile.business_name) != (
            business_oib.strip(),
            " ".join(business_name.split()),
        ):
            raise ValueError(
                f"HelperProfile {profile.pk}'s business name or OIB differs from the checked one."
            )
        profile.business_verified_at = timezone.now()
        profile.business_verified_by = by
        profile.save(update_fields=["business_verified_at", "business_verified_by", "updated_at"])
    return profile


def can_send_offers(user: User) -> bool:
    return not missing_requirements(user, profile_of(user))


def require_can_send_offers(user: AbstractBaseUser | AnonymousUser | None) -> HelperProfile:
    """The signed-in user's complete helper profile. `sendOffer` (BEAV-85) calls this first.

    Raises UNAUTHENTICATED when signed out, PHONE_NOT_VERIFIED when the mobile number is
    unverified, PAYOUT_ACCOUNT_NOT_READY when only the Stripe account is missing (not set
    up yet, or Stripe paused it), BUSINESS_ADDRESS_REQUIRED when only a trader's registered
    business address is, and HELPER_PROFILE_INCOMPLETE while anything else is.
    """
    helper = require_verified_phone(user)
    profile = profile_of(helper)
    missing = missing_requirements(helper, profile) if profile else None
    if missing == [Requirement.PAYOUT_ACCOUNT]:
        raise ApiError(
            ErrorCode.PAYOUT_ACCOUNT_NOT_READY,
            "The Stripe payout account can't receive payments yet.",
        )
    if missing == [Requirement.BUSINESS_ADDRESS]:
        raise ApiError(
            ErrorCode.BUSINESS_ADDRESS_REQUIRED,
            "A registered business needs its registered address before sending offers.",
        )
    if profile is None or missing:
        raise ApiError(
            ErrorCode.HELPER_PROFILE_INCOMPLETE, "Finish the helper profile before sending offers."
        )
    return profile


def active_service_areas(profile: HelperProfile) -> list[ServiceArea]:
    return list(_active_areas(profile).order_by("name"))


def _active_areas(profile: HelperProfile) -> QuerySet[ServiceArea]:
    return profile.service_areas.filter(active=True, country_code=ServiceArea.Country.HR)


def _apply(profile: HelperProfile, changes: ProfileChanges) -> None:
    if changes.status is not None:
        profile.declared_status = changes.status
        if changes.status != HelperProfile.Status.BUSINESS:
            profile.business_kind = ""
            profile.business_name = ""
            profile.business_oib = ""
            profile.business_oib_digest = ""
            profile.issues_fiscalized_invoices = False
            profile.business_address_street = ""
            profile.business_address_postal_code = ""
            profile.business_address_city = ""
            profile.business_address_country = ""
            _clear_business_verification(profile)
    # The personal OIB before the business: an obrt's business OIB is a copy of it.
    if changes.oib is not None:
        _apply_oib(profile, changes.oib)
    if changes.business is not None:
        if not profile.is_trader:
            raise _invalid("Business details apply to the BUSINESS status only.")
        _apply_business(profile, changes.business)
    if changes.business_address is not None:
        if not profile.is_trader:
            raise _invalid("A business address applies to the BUSINESS status only.")
        _apply_business_address(profile, changes.business_address)
    if changes.legal_first_name is not None:
        profile.legal_first_name = _text(changes.legal_first_name, 100)
    if changes.legal_last_name is not None:
        profile.legal_last_name = _text(changes.legal_last_name, 100)
    if changes.birth_date is not None:
        profile.birth_date = _birth_date(changes.birth_date)
    if changes.address is not None:
        _apply_address(profile, changes.address)
    if changes.iban is not None:
        iban = normalize_iban(changes.iban)
        if iban is None:
            raise ApiError(ErrorCode.INVALID_IBAN, "Not a valid IBAN of an EU bank account.")
        profile.iban = iban
    # After both OIBs, which a Croatian VAT ID must match.
    if changes.vat_id is not None:
        profile.vat_id = _vat_id(profile, changes.vat_id)
    elif profile.vat_id and not _vat_id_matches(profile, profile.vat_id):
        # The OIB it was HR plus changed: the old VAT ID is no longer the helper's.
        profile.vat_id = ""
    if changes.residence_country is not None:
        profile.residence_country = _country(changes.residence_country)
    if changes.categories is not None:
        profile.categories = [str(c) for c in dict.fromkeys(changes.categories)]


def _apply_business(profile: HelperProfile, business: Business) -> None:
    oib = business.oib.strip() if business.oib is not None else None
    if business.kind == HelperProfile.BusinessKind.OBRT:
        # An obrt has no OIB of its own: it is the owner's, copied from `oib`.
        if oib is not None and oib != profile.oib:
            raise ApiError(ErrorCode.INVALID_BUSINESS_OIB, "An obrt's OIB is its owner's own OIB.")
        business_oib = profile.oib
        business_digest = ""
    else:
        if not oib:
            raise _invalid("A company needs its OIB.")
        if not is_valid_oib(oib):
            raise ApiError(ErrorCode.INVALID_OIB, "The business OIB's check digit doesn't match.")
        business_digest = pii_digest("oib", oib)
        # A person's OIB, the owner's or any helper's, must never be shown as a company's.
        if oib == profile.oib or HelperProfile.objects.filter(oib_digest=business_digest).exists():
            raise ApiError(
                ErrorCode.INVALID_BUSINESS_OIB, "That OIB is a person's, not a company's."
            )
        business_oib = oib
    name = _text(business.name, 200)
    if (business.kind, name, business_oib) != (
        profile.business_kind,
        profile.business_name,
        profile.business_oib,
    ):
        _clear_business_verification(profile)
    profile.business_kind = business.kind
    profile.business_name = name
    profile.business_oib = business_oib
    profile.business_oib_digest = business_digest
    profile.issues_fiscalized_invoices = business.issues_fiscalized_invoices


def _clear_business_verification(profile: HelperProfile) -> None:
    profile.business_verified_at = None
    profile.business_verified_by = ""


def _apply_address(profile: HelperProfile, address: Address) -> None:
    # The country first: outside Croatia the client offers the waitlist (joinWaitlist).
    if address.country.strip().upper() != "HR":
        raise ApiError(
            ErrorCode.HELPER_ADDRESS_NOT_IN_CROATIA, "Helpers need a home address in Croatia."
        )
    postal_code = address.postal_code.strip()
    if _HR_POSTAL_CODE.fullmatch(postal_code) is None:
        raise _invalid("A Croatian postal code has five digits.")
    profile.address_street = _text(address.street, 200)
    profile.address_postal_code = postal_code
    profile.address_city = _text(address.city, 100)
    profile.address_country = "HR"


def _apply_business_address(profile: HelperProfile, address: Address) -> None:
    # Where the obrt or company is registered (CRD Art. 6(1)(c)), which may be in another
    # EU country; the home address above is the one that must be Croatian (hard rule j).
    country = address.country.strip().upper()
    if country not in RESIDENCE_COUNTRIES:
        raise _invalid("The registered business address must be in the EU.")
    postal_code = _text(address.postal_code, 10)
    if country == "HR" and _HR_POSTAL_CODE.fullmatch(postal_code) is None:
        raise _invalid("A Croatian postal code has five digits.")
    street, city = _text(address.street, 200), _text(address.city, 100)
    # Posters see it on offers before booking, so it must not carry a phone, email or link
    # (hard rule e; moderation.contact_filter).
    for text in (street, postal_code, city):
        if filter_contact_details(text, profile.user.locale).verdict != Verdict.CLEAN:
            raise _invalid("A business address can't hold contact details.")
    profile.business_address_street = street
    profile.business_address_postal_code = postal_code
    profile.business_address_city = city
    profile.business_address_country = country


def _apply_oib(profile: HelperProfile, value: str) -> None:
    oib = value.strip()
    if not is_valid_oib(oib):
        raise ApiError(ErrorCode.INVALID_OIB, "The OIB's check digit doesn't match.")
    digest = pii_digest("oib", oib)
    if (
        HelperProfile.objects.filter(oib_digest=digest).exclude(pk=profile.pk).exists()
        # A company's OIB on a profile, its owner's or another helper's, isn't a person's.
        or HelperProfile.objects.filter(business_oib_digest=digest).exists()
    ):
        raise ApiError(ErrorCode.OIB_IN_USE, "The OIB belongs to another helper.")
    profile.oib = oib
    profile.oib_digest = digest
    if profile.business_kind == HelperProfile.BusinessKind.OBRT:
        profile.business_oib = oib


def _birth_date(value: dt.date) -> dt.date:
    today = timezone.localdate()
    age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    if value > today or age > _MAX_AGE:
        raise _invalid("The date of birth isn't plausible.")
    if age < ADULT_AGE:
        raise ApiError(ErrorCode.UNDERAGE, "Helpers must be 18 or older.")
    return value


def _vat_id(profile: HelperProfile, value: str) -> str:
    vat_id = "".join(value.split()).upper()
    if not vat_id:
        return ""
    if _VAT_ID.fullmatch(vat_id) is None or not _vat_id_matches(profile, vat_id):
        raise ApiError(ErrorCode.INVALID_VAT_ID, "Not a valid EU VAT number of this helper.")
    return vat_id


def _vat_id_matches(profile: HelperProfile, vat_id: str) -> bool:
    """A Croatian VAT ID is HR and the taxpayer's OIB: the helper's own, or a trader's
    business OIB. Other countries' numbers can't be checked here."""
    if not vat_id.startswith("HR"):
        return True
    own = {profile.oib} | ({profile.business_oib} if profile.is_trader else set())
    return vat_id[2:] in own - {""}


def _country(value: str) -> str:
    country = value.strip().upper()
    if country not in RESIDENCE_COUNTRIES:
        raise ApiError(
            ErrorCode.RESIDENCE_COUNTRY_NOT_ALLOWED, "The residence country must be in the EU."
        )
    return country


def _service_areas(ids: list[str]) -> list[ServiceArea]:
    try:
        pks = {int(pk) for pk in ids}
    except ValueError:
        raise ApiError(ErrorCode.SERVICE_AREA_NOT_FOUND, "No such service area.") from None
    areas = list(
        ServiceArea.objects.filter(pk__in=pks, active=True, country_code=ServiceArea.Country.HR)
    )
    if len(areas) != len(pks):
        raise ApiError(ErrorCode.SERVICE_AREA_NOT_FOUND, "No such service area.")
    return areas


def _text(value: str, max_length: int) -> str:
    text = " ".join(value.split())
    if not text or len(text) > max_length or not text.isprintable():
        raise _invalid(f"Text fields take 1 to {max_length} characters.")
    return text


def _invalid(message: str) -> ApiError:
    return ApiError(ErrorCode.INVALID_HELPER_DETAILS, message)
