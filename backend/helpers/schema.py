"""GraphQL API of the helper profile. Rules: helpers/services.py.

The owner reads their own profile (`myHelperProfile`), with the OIB and IBAN masked.
Anyone reads a complete helper's public side (`helperProfile`): its categories and
service areas, and only for a trader the business name, business OIB and the town of its
registered business address (hard rule e). Never the street address: many obrts are
registered at the owner's home, so only the poster of a task the helper offered on sees it,
on the offer (user decision 2026-11-07).
"""

import datetime as dt
from enum import Enum

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from accounts.models import User
from accounts.permissions import require_signed_in
from areas.models import ServiceArea
from areas.schema import ServiceAreaType
from core.categories import TaskCategory, TaskCategoryEnum
from helpers import services
from helpers.iban import masked_iban
from helpers.models import HelperProfile
from helpers.oib import masked_oib, masked_vat_id
from payments import connect
from payments.models import ConnectedAccount


@strawberry.enum(name="HelperStatus", description="The status a helper declares.")
class HelperStatusEnum(Enum):
    PRIVATE_INDIVIDUAL = HelperProfile.Status.PRIVATE_INDIVIDUAL.value
    BUSINESS = strawberry.enum_value(
        HelperProfile.Status.BUSINESS.value,
        description="A registered business, obrt or company: shown as a trader.",
    )
    PENSIONER = HelperProfile.Status.PENSIONER.value
    STUDENT = strawberry.enum_value(
        HelperProfile.Status.STUDENT.value,
        description="Helps as a private individual; student-service contracts aren't offered.",
    )


@strawberry.enum(name="HelperBusinessKind")
class HelperBusinessKindEnum(Enum):
    OBRT = HelperProfile.BusinessKind.OBRT.value
    COMPANY = HelperProfile.BusinessKind.COMPANY.value


HelperRequirement = strawberry.enum(
    services.Requirement,
    name="HelperRequirement",
    description="Something a helper still has to provide before sending offers.",
)


@strawberry.enum(
    name="PayoutAccountStatus", description="Where the helper's Stripe payout account stands."
)
class PayoutAccountStatusEnum(Enum):
    INCOMPLETE = strawberry.enum_value(
        ConnectedAccount.Status.INCOMPLETE.value,
        description="Stripe onboarding isn't finished: continue with startPayoutOnboarding.",
    )
    NEEDS_INFO = strawberry.enum_value(
        ConnectedAccount.Status.NEEDS_INFO.value,
        description=(
            "Stripe asks for more information; offers are paused until it is given "
            "(startPayoutOnboarding)."
        ),
    )
    VERIFYING = strawberry.enum_value(
        ConnectedAccount.Status.VERIFYING.value,
        description="Everything was given and Stripe is checking it.",
    )
    ACTIVE = strawberry.enum_value(
        ConnectedAccount.Status.ACTIVE.value, description="Can receive payments."
    )
    RESTRICTED = strawberry.enum_value(
        ConnectedAccount.Status.RESTRICTED.value,
        description="Stripe paused the account (for example under review); support helps.",
    )
    REJECTED = strawberry.enum_value(
        ConnectedAccount.Status.REJECTED.value,
        description="Stripe rejected the account; support explains why and how to appeal.",
    )


@strawberry.enum(
    name="PayoutAccountRejection",
    description="Why Stripe rejected a payout account, as far as the helper is told.",
)
class PayoutAccountRejectionEnum(Enum):
    INCOMPLETE_VERIFICATION = "INCOMPLETE_VERIFICATION"
    TERMS_OF_SERVICE = "TERMS_OF_SERVICE"
    # Fraud, sanctions-list matches (rejected.listed) and Stripe's "other" are not named to
    # the account holder; support explains (plan: "Rejected ... support explains, appeal
    # possible"). Decided with the product owner. NEEDS REVIEW: confirm with the lawyer that
    # this meets the DSA statement-of-reasons duty.
    OTHER = "OTHER"


_REJECTIONS = {
    "rejected.incomplete_verification": PayoutAccountRejectionEnum.INCOMPLETE_VERIFICATION,
    "rejected.terms_of_service": PayoutAccountRejectionEnum.TERMS_OF_SERVICE,
}


@strawberry.type(
    name="PayoutAccount",
    description=(
        "The helper's Stripe Express account, which receives their payments and pays them "
        "out. Its state comes from Stripe. Only its owner reads it."
    ),
)
class PayoutAccountType:
    id: strawberry.ID
    status: PayoutAccountStatusEnum
    payouts_enabled: bool = strawberry.field(
        description="Stripe can pay out to the helper's bank account."
    )
    requirements_due: list[str] = strawberry.field(
        description=(
            'Stripe\'s keys of what it needs now, e.g. "individual.verification.document".'
        )
    )
    current_deadline: dt.datetime | None = strawberry.field(
        description="When Stripe pauses the account if the requirements aren't met."
    )
    rejection: PayoutAccountRejectionEnum | None = strawberry.field(
        description="Set when the status is REJECTED."
    )
    updated_at: dt.datetime

    @classmethod
    def of(cls, account: ConnectedAccount) -> "PayoutAccountType":
        rejected = account.status == ConnectedAccount.Status.REJECTED
        return cls(
            id=strawberry.ID(str(account.pk)),
            status=PayoutAccountStatusEnum(account.status),
            payouts_enabled=account.payouts_enabled,
            requirements_due=list(account.requirements_due),
            current_deadline=account.current_deadline,
            rejection=(
                _REJECTIONS.get(account.disabled_reason, PayoutAccountRejectionEnum.OTHER)
                if rejected
                else None
            ),
            updated_at=account.updated_at,
        )


@strawberry.type(
    name="HelperProfile",
    description=(
        "The signed-in user's own helper profile: status, identity and tax data (DAC7), "
        "service areas and categories. Only its owner reads this type."
    ),
)
class HelperProfileType:
    id: strawberry.ID
    status: HelperStatusEnum | None
    is_trader: bool = strawberry.field(description="True for the BUSINESS status.")
    business_kind: HelperBusinessKindEnum | None
    business_name: str | None
    business_oib: str | None = strawberry.field(
        description="The obrt's or company's OIB, shown on the public profile."
    )
    issues_fiscalized_invoices: bool
    business_address_street: str | None = strawberry.field(
        description=(
            "The registered business address. Its poster sees it on each offer; the public "
            "profile shows only the town."
        )
    )
    business_address_postal_code: str | None
    business_address_city: str | None
    business_address_country: str | None = strawberry.field(
        description="ISO 3166-1 alpha-2, an EU member state."
    )
    business_verified: bool = strawberry.field(
        description=(
            "A company's name and OIB were confirmed; until then BUSINESS_VERIFICATION is "
            "missing. Always true for an obrt."
        )
    )
    legal_first_name: str | None
    legal_last_name: str | None
    birth_date: dt.date | None
    address_street: str | None
    address_postal_code: str | None
    address_city: str | None
    address_country: str | None
    oib_masked: str | None = strawberry.field(description='Masked, e.g. "•••••••0698".')
    iban_masked: str | None = strawberry.field(
        description='Masked, e.g. "HR•• •••• •••• •••• •016 0".'
    )
    vat_id_masked: str | None = strawberry.field(description='Masked, e.g. "HR•••••••0698".')
    residence_country: str | None = strawberry.field(
        description="DAC7 member state of residence, ISO 3166-1 alpha-2."
    )
    categories: list[TaskCategoryEnum]
    service_areas: list[ServiceAreaType]
    payout_account: PayoutAccountType | None = strawberry.field(
        description="Null until the helper starts Stripe onboarding (startPayoutOnboarding)."
    )
    missing: list[HelperRequirement] = strawberry.field(  # pyright: ignore[reportInvalidTypeForm]
        description="What is still needed before sending offers; empty when complete."
    )
    can_send_offers: bool
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, user: User, profile: HelperProfile) -> "HelperProfileType":
        """Reads the profile's service areas and requirements: call it in sync code."""
        areas = services.active_service_areas(profile)
        missing = services.missing_requirements(user, profile, areas)
        return cls(
            id=strawberry.ID(str(profile.pk)),
            status=HelperStatusEnum(profile.declared_status) if profile.declared_status else None,
            is_trader=profile.is_trader,
            business_kind=(
                HelperBusinessKindEnum(profile.business_kind) if profile.business_kind else None
            ),
            business_name=profile.business_name or None,
            business_oib=profile.business_oib or None,
            issues_fiscalized_invoices=profile.issues_fiscalized_invoices,
            business_address_street=profile.business_address_street or None,
            business_address_postal_code=profile.business_address_postal_code or None,
            business_address_city=profile.business_address_city or None,
            business_address_country=profile.business_address_country or None,
            business_verified=profile.is_trader and not profile.needs_business_verification,
            legal_first_name=profile.legal_first_name or None,
            legal_last_name=profile.legal_last_name or None,
            birth_date=profile.birth_date,
            address_street=profile.address_street or None,
            address_postal_code=profile.address_postal_code or None,
            address_city=profile.address_city or None,
            address_country=profile.address_country or None,
            oib_masked=masked_oib(profile.oib) if profile.oib else None,
            iban_masked=masked_iban(profile.iban) if profile.iban else None,
            vat_id_masked=masked_vat_id(profile.vat_id) if profile.vat_id else None,
            residence_country=profile.residence_country or None,
            categories=[TaskCategoryEnum(c) for c in profile.categories],
            service_areas=[ServiceAreaType.of(a) for a in areas],
            payout_account=(
                PayoutAccountType.of(account)
                if (account := ConnectedAccount.objects.filter(helper=profile).first())
                else None
            ),
            missing=missing,
            can_send_offers=not missing,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )


@strawberry.type(
    name="PublicHelperProfile",
    description=(
        "What anyone sees of a helper who can send offers. A trader (obrt or company) "
        "shows its business name, OIB and the town of its registered address, as a trader "
        "must; nothing else personal is here."
    ),
)
class PublicHelperProfileType:
    id: strawberry.ID
    is_trader: bool
    business_name: str | None = strawberry.field(
        description="Null unless a trader (and, for a company, until it is verified)."
    )
    business_oib: str | None = strawberry.field(
        description="Null unless a trader (and, for a company, until it is verified)."
    )
    business_town: str | None = strawberry.field(
        description=(
            "The town of the registered business address, never the street: the full "
            "address is on the trader's offers, for the task's poster only. Null unless a "
            "trader (and, for a company, until it is verified)."
        )
    )
    categories: list[TaskCategoryEnum]
    service_areas: list[ServiceAreaType]

    @classmethod
    def of(cls, profile: HelperProfile, areas: list[ServiceArea]) -> "PublicHelperProfileType":
        # An unverified company's name and OIB are never shown (such a profile is also
        # incomplete, so `helperProfile` returns null for it anyway).
        shown = profile.is_trader and not profile.needs_business_verification
        return cls(
            id=strawberry.ID(str(profile.pk)),
            is_trader=profile.is_trader,
            business_name=profile.business_name if shown else None,
            business_oib=profile.business_oib if shown else None,
            business_town=(profile.business_address_city or None) if shown else None,
            categories=[TaskCategoryEnum(c) for c in profile.categories],
            service_areas=[ServiceAreaType.of(a) for a in areas],
        )


@strawberry.input
class HelperAddressInput:
    street: str
    postal_code: str
    city: str
    country: str = strawberry.field(
        description="ISO 3166-1 alpha-2; must be HR, else HELPER_ADDRESS_NOT_IN_CROATIA."
    )


@strawberry.input(description="Where the obrt or company is registered; never the home address.")
class HelperBusinessAddressInput:
    street: str
    postal_code: str
    city: str
    country: str = strawberry.field(description="ISO 3166-1 alpha-2, an EU member state.")


@strawberry.input
class HelperBusinessInput:
    kind: HelperBusinessKindEnum
    name: str
    oib: str | None = strawberry.field(
        default=None,
        description=(
            "The company's OIB, required for COMPANY. An obrt's OIB is its owner's own: "
            "leave it out for OBRT (or give the same OIB)."
        ),
    )
    issues_fiscalized_invoices: bool = strawberry.field(
        default=False, description="The trader issues a fiscalized invoice for each task."
    )


@strawberry.input(description="Fields to set; omitted fields stay as they are.")
class UpdateHelperProfileInput:
    status: HelperStatusEnum | None = strawberry.field(
        default=None,
        description="Changing away from BUSINESS removes the business details.",
    )
    business: HelperBusinessInput | None = strawberry.field(
        default=None, description="Only with the BUSINESS status."
    )
    business_address: HelperBusinessAddressInput | None = strawberry.field(
        default=None,
        description=(
            "The registered business address, only with the BUSINESS status. The poster of "
            "a task sees it on the helper's offer, at checkout and in the contract "
            "confirmation; the public profile shows only the town."
        ),
    )
    legal_first_name: str | None = None
    legal_last_name: str | None = None
    birth_date: dt.date | None = None
    address: HelperAddressInput | None = None
    oib: str | None = None
    iban: str | None = strawberry.field(
        default=None, description="An EU account; spaces are ignored."
    )
    vat_id: str | None = strawberry.field(default=None, description='Empty string ("") removes it.')
    residence_country: str | None = None
    service_area_ids: list[strawberry.ID] | None = strawberry.field(
        default=None, description="Replaces the service areas; IDs from serviceAreas."
    )
    categories: list[TaskCategoryEnum] | None = strawberry.field(
        default=None, description="Replaces the categories."
    )


@strawberry.type(
    description=(
        "A single-use link to Stripe's hosted onboarding. Open `url` in the browser before "
        "`expiresAt`; Stripe sends the helper back to /helper/payouts/return, or to "
        "/helper/payouts/refresh to ask for a new link."
    )
)
class PayoutOnboardingLink:
    id: strawberry.ID = strawberry.field(description="The payout account's ID.")
    url: str
    expires_at: dt.datetime
    helper_profile: HelperProfileType


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "The signed-in user's helper profile; null until they start one. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def my_helper_profile(self, info: Info) -> HelperProfileType | None:
        user = require_signed_in(info.context.user)
        return await sync_to_async(_my_profile)(user)

    @strawberry.field(
        description=(
            "A helper's public profile by user ID; null unless the profile is complete and "
            "Stripe onboarding was submitted (offers may still be paused while Stripe asks "
            "for more). Needs no sign-in."
        )
    )
    async def helper_profile(self, user_id: strawberry.ID) -> PublicHelperProfileType | None:
        return await sync_to_async(_public_profile)(str(user_id))


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Create or change the signed-in user's helper profile, one onboarding step at a "
            "time. Errors: UNAUTHENTICATED, INVALID_OIB, OIB_IN_USE, INVALID_IBAN, "
            "INVALID_VAT_ID, INVALID_HELPER_DETAILS, UNDERAGE, HELPER_ADDRESS_NOT_IN_CROATIA "
            "(offer joinWaitlist), SERVICE_AREA_NOT_FOUND."
        )
    )
    async def update_helper_profile(
        self, info: Info, input: UpdateHelperProfileInput
    ) -> HelperProfileType:
        user = require_signed_in(info.context.user)
        changes = services.ProfileChanges(
            status=HelperProfile.Status(input.status.value) if input.status else None,
            business=(
                services.Business(
                    kind=HelperProfile.BusinessKind(input.business.kind.value),
                    name=input.business.name,
                    oib=input.business.oib,
                    issues_fiscalized_invoices=input.business.issues_fiscalized_invoices,
                )
                if input.business
                else None
            ),
            business_address=(
                services.Address(
                    street=input.business_address.street,
                    postal_code=input.business_address.postal_code,
                    city=input.business_address.city,
                    country=input.business_address.country,
                )
                if input.business_address
                else None
            ),
            legal_first_name=input.legal_first_name,
            legal_last_name=input.legal_last_name,
            birth_date=input.birth_date,
            address=(
                services.Address(
                    street=input.address.street,
                    postal_code=input.address.postal_code,
                    city=input.address.city,
                    country=input.address.country,
                )
                if input.address
                else None
            ),
            oib=input.oib,
            iban=input.iban,
            vat_id=input.vat_id,
            residence_country=input.residence_country,
            service_area_ids=(
                [str(pk) for pk in input.service_area_ids]
                if input.service_area_ids is not None
                else None
            ),
            categories=(
                [TaskCategory(c.value) for c in input.categories]
                if input.categories is not None
                else None
            ),
        )
        return await sync_to_async(_update)(user, changes)

    @strawberry.mutation(
        description=(
            "Open the signed-in helper's Stripe Express account on first use and return a "
            "link to Stripe's onboarding (identity check and IBAN). Also the way to give "
            "Stripe the information it asks for later. Errors: UNAUTHENTICATED, "
            "HELPER_PROFILE_INCOMPLETE (a Croatian address and the OIB come first), "
            "PAYOUT_ACCOUNT_REJECTED."
        )
    )
    async def start_payout_onboarding(self, info: Info) -> PayoutOnboardingLink:
        user = require_signed_in(info.context.user)
        return await sync_to_async(_start_payout_onboarding)(user)


def _my_profile(user: User) -> HelperProfileType | None:
    profile = services.profile_of(user)
    return HelperProfileType.of(user, profile) if profile else None


def _public_profile(user_id: str) -> PublicHelperProfileType | None:
    try:
        profile = HelperProfile.objects.select_related("user").get(user_id=int(user_id))
    except (ValueError, HelperProfile.DoesNotExist):
        return None
    areas = services.active_service_areas(profile)
    if not services.is_public(profile.user, profile, areas):
        return None
    return PublicHelperProfileType.of(profile, areas)


def _start_payout_onboarding(user: User) -> PayoutOnboardingLink:
    link = connect.start_onboarding(user)
    return PayoutOnboardingLink(
        id=strawberry.ID(str(link.account.pk)),
        url=link.url,
        expires_at=link.expires_at,
        helper_profile=HelperProfileType.of(user, link.account.helper),
    )


def _update(user: User, changes: services.ProfileChanges) -> HelperProfileType:
    return HelperProfileType.of(user, services.update_profile(user, changes))
