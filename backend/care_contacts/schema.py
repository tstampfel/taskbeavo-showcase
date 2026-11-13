"""GraphQL API of the signed-in poster's on-site contacts. Rules: care_contacts/services.py."""

import datetime as dt
from typing import Any

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from accounts.models import User
from accounts.schema import Locale
from care_contacts import policy, services
from care_contacts.models import CareContact
from core.errors import ApiError, ErrorCode


@strawberry.type(
    name="CareContact",
    description=(
        'An on-site contact of the signed-in poster, e.g. "Mama Kata". The contact has no '
        "account. Only the owner reads this type."
    ),
)
class CareContactType:
    id: strawberry.ID
    name: str
    phone: str = strawberry.field(
        description="E.164. Shown to the owner; helpers see it only in an active booking."
    )
    sms_capable: bool = strawberry.field(
        description="Whether reminders can reach the number by SMS; false for landlines."
    )
    area: str = strawberry.field(description='Free text, e.g. "Gruž, Dubrovnik".')
    language: Locale = strawberry.field(description="The language SMS to the contact use.")
    no_smartphone: bool = strawberry.field(
        description="Reminders reach the contact by SMS instead of push."
    )
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, contact: CareContact) -> "CareContactType":
        return cls(
            id=strawberry.ID(str(contact.pk)),
            name=contact.name,
            phone=contact.phone_number,
            sms_capable=contact.sms_capable,
            area=contact.area,
            language=Locale(contact.language),
            no_smartphone=contact.no_smartphone,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
        )


@strawberry.type(
    name="OnSiteContact",
    description=(
        "Whom the helper meets on site, as the task's poster or the helper of its active "
        "booking sees them. Null for everyone else, and for the helper once the booking closes."
    ),
)
class OnSiteContactType:
    id: strawberry.ID
    name: str
    area: str
    language: Locale
    phone: str | None = strawberry.field(
        description=(
            "E.164. The poster and the helper of an active booking see it; null otherwise."
        )
    )

    @classmethod
    def of(
        cls, viewer: User | None, contact: CareContact, booking: policy.BookingLike | None
    ) -> "OnSiteContactType":
        return cls(
            id=strawberry.ID(str(contact.pk)),
            name=contact.name,
            area=contact.area,
            language=Locale(contact.language),
            phone=policy.visible_phone(viewer, contact, booking),
        )


@strawberry.input
class CreateCareContactInput:
    name: str
    phone: str = strawberry.field(
        description="Mobile or landline, international (+49 ...) or national Croatian (020 ...)."
    )
    area: str
    language: Locale = Locale.HR
    no_smartphone: bool = False
    has_their_agreement: bool = strawberry.field(
        description="The poster confirmed the person agreed to having their details saved."
    )


@strawberry.input
class UpdateCareContactInput:
    name: str | None = None
    phone: str | None = None
    area: str | None = None
    language: Locale | None = None
    no_smartphone: bool | None = None


@strawberry.type
class Query:
    @strawberry.field(
        description="The signed-in user's on-site contacts, oldest first. Errors: UNAUTHENTICATED."
    )
    async def my_care_contacts(self, info: Info) -> list[CareContactType]:
        user = _signed_in_user(info)
        contacts = await sync_to_async(list)(CareContact.objects.filter(owner=user))
        return [CareContactType.of(contact) for contact in contacts]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Save an on-site contact. Errors: UNAUTHENTICATED, CARE_CONTACT_AGREEMENT_REQUIRED, "
            "INVALID_CARE_CONTACT, INVALID_CARE_CONTACT_PHONE."
        )
    )
    async def create_care_contact(
        self, info: Info, input: CreateCareContactInput
    ) -> CareContactType:
        user = _signed_in_user(info)
        changes = services.ContactChanges(
            name=input.name,
            phone=input.phone,
            area=input.area,
            language=input.language.value,
            no_smartphone=input.no_smartphone,
        )
        contact = await sync_to_async(services.create_contact)(
            user, changes, has_their_agreement=input.has_their_agreement
        )
        return CareContactType.of(contact)

    @strawberry.mutation(
        description=(
            "Change an on-site contact; omitted fields stay as they are. Active bookings see "
            "the change at once. Errors: UNAUTHENTICATED, CARE_CONTACT_NOT_FOUND, "
            "CARE_CONTACT_LOCKED, INVALID_CARE_CONTACT, INVALID_CARE_CONTACT_PHONE."
        )
    )
    async def update_care_contact(
        self, info: Info, id: strawberry.ID, input: UpdateCareContactInput
    ) -> CareContactType:
        user = _signed_in_user(info)
        changes = services.ContactChanges(
            name=input.name,
            phone=input.phone,
            area=input.area,
            language=input.language.value if input.language else None,
            no_smartphone=input.no_smartphone,
        )
        contact = await sync_to_async(services.update_contact)(user, str(id), changes)
        return CareContactType.of(contact)

    @strawberry.mutation(
        description=(
            "Delete an on-site contact; returns it as it was. Errors: UNAUTHENTICATED, "
            "CARE_CONTACT_NOT_FOUND, CARE_CONTACT_IN_USE."
        )
    )
    async def delete_care_contact(self, info: Info, id: strawberry.ID) -> CareContactType:
        user = _signed_in_user(info)
        contact = await sync_to_async(services.delete_contact)(user, str(id))
        return CareContactType.of(contact)


def _signed_in_user(info: Info[Any, Any]) -> User:
    user = info.context.user
    if not isinstance(user, User) or not user.is_active:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return user
