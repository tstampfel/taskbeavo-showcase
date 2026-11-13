"""GraphQL API of service areas and the waitlist. The service-area check itself has no
field: task, offer and booking mutations call `areas.services.assert_in_service_area`."""

import strawberry
from asgiref.sync import sync_to_async

from accounts.schema import Locale
from areas import services
from areas.models import ServiceArea, WaitlistEntry
from core.categories import TaskCategory, TaskCategoryEnum


@strawberry.type(name="ServiceArea", description="A city TaskBeavo is open in.")
class ServiceAreaType:
    id: strawberry.ID
    name: str
    slug: str

    @classmethod
    def of(cls, area: ServiceArea) -> "ServiceAreaType":
        return cls(id=strawberry.ID(str(area.pk)), name=area.name, slug=area.slug)


@strawberry.input
class JoinWaitlistInput:
    email: str
    city: str = strawberry.field(description="The city the person wanted help in, as typed.")
    country_code: str = strawberry.field(description="ISO 3166-1 alpha-2 country of the city.")
    category: TaskCategoryEnum
    locale: Locale = strawberry.field(
        description="The language the consent was shown in; the launch email uses it."
    )
    consent_launch_email: bool = strawberry.field(
        description=(
            "The person ticked the consent to one email when TaskBeavo launches in this "
            "city, and nothing else. Must be true."
        )
    )


@strawberry.type(
    name="WaitlistEntry",
    description=(
        "A place on the waitlist for a city. Only what the caller sent comes back, so the "
        "answer doesn't tell whether the address was already on the list."
    ),
)
class WaitlistEntryType:
    id: strawberry.ID
    city: str
    country_code: str
    category: TaskCategoryEnum

    @classmethod
    def of(cls, entry: WaitlistEntry) -> "WaitlistEntryType":
        return cls(
            id=strawberry.ID(str(entry.pk)),
            city=entry.city,
            country_code=entry.country_code,
            category=TaskCategoryEnum(entry.category),
        )


@strawberry.type
class Query:
    @strawberry.field(
        description="The active service areas, e.g. for helpers choosing where to help."
    )
    async def service_areas(self) -> list[ServiceAreaType]:
        areas = await sync_to_async(list)(
            ServiceArea.objects.filter(active=True, country_code=ServiceArea.Country.HR)
        )
        return [ServiceAreaType.of(area) for area in areas]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Join the waitlist for a city TaskBeavo doesn't serve yet, e.g. after "
            "OUT_OF_SERVICE_AREA. Needs no sign-in. Joining again for the same email and "
            "city updates that entry. Errors: WAITLIST_CONSENT_REQUIRED, INVALID_EMAIL, "
            "INVALID_WAITLIST_DETAILS."
        )
    )
    async def join_waitlist(self, input: JoinWaitlistInput) -> WaitlistEntryType:
        entry = await sync_to_async(services.join_waitlist)(
            email=input.email,
            city=input.city,
            country_code=input.country_code,
            category=TaskCategory(input.category.value),
            locale=input.locale.value,
            consent_launch_email=input.consent_launch_email,
        )
        return WaitlistEntryType.of(entry)

    @strawberry.mutation(
        description=(
            "Leave the waitlist with the token from the launch email's link, withdrawing the "
            "consent: the entry is deleted. Needs no sign-in. Always returns true, whether "
            "or not the token matched an entry."
        )
    )
    async def leave_waitlist(self, token: str) -> bool:
        await sync_to_async(services.leave_waitlist)(token)
        return True
