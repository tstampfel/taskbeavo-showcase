"""GraphQL API of offers. Rules: offers/services.py; who sees what: below.

An offer is visible to its helper and to the poster of its task, nobody else. The live
events (offerReceived, offerWithdrawn, offerExpiring, offerUpdated) go to the task's poster
only, and each event is re-checked against the current offer (hard rule d). Neither sees
the other's phone number, email or any other contact detail (hard rule e). The poster sees
whether the helper is a trader, as the plan's marketplace disclosure requires, and for a
trader its business name and OIB (CRD Art. 6(1)(b)-(c), ZZP čl. 61(1)). The registered
business address goes to the task's poster only: many obrts are registered at the owner's
home (user decision 2026-11-07). A private helper's name, OIB and address are never on an
offer.
"""

import datetime as dt
from collections.abc import AsyncGenerator, Awaitable, Callable
from enum import Enum
from typing import Any
from uuid import UUID

import strawberry
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractBaseUser
from strawberry.types import Info

from accounts.models import User
from accounts.permissions import CanCreateWork, require_signed_in
from bookings.credit import available_cents
from bookings.money import fee_schedule_at
from core.realtime import LiveEvent, viewer_events
from moderation.contact_filter import Verdict
from offers import services
from offers.models import Offer


@strawberry.enum(name="OfferStatus")
class OfferStatusEnum(Enum):
    SENT = Offer.Status.SENT.value
    NEEDS_UPDATE = strawberry.enum_value(
        Offer.Status.NEEDS_UPDATE.value,
        description=(
            "The poster changed the task's date, address or scope; the helper confirms or "
            "changes the offer with updateOffer."
        ),
    )
    WITHDRAWN = Offer.Status.WITHDRAWN.value
    EXPIRED = strawberry.enum_value(
        Offer.Status.EXPIRED.value, description="Past its expiry; the helper may renew it."
    )
    ACCEPTED = strawberry.enum_value(
        Offer.Status.ACCEPTED.value, description="The poster accepted it; it is being booked."
    )
    DECLINED = strawberry.enum_value(
        Offer.Status.DECLINED.value,
        description="The poster accepted another offer. It reopens if that checkout fails.",
    )


@strawberry.enum(name="OfferWithdrawnReason")
class OfferWithdrawnReasonEnum(Enum):
    BUSINESS_POSTER_REGISTERED_ONLY = strawberry.enum_value(
        Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY.value,
        description=(
            "The task's poster books for a business, so the task takes offers from "
            "registered helpers only. It says nothing about the helper."
        ),
    )


@strawberry.type(name="Offer", description="A helper's priced offer on a task.")
class OfferType:
    id: strawberry.ID
    task_id: strawberry.ID
    helper_user_id: strawberry.ID = strawberry.field(
        description="The helper's user ID, for helperProfile."
    )
    helper_is_trader: bool = strawberry.field(
        description="The helper is a registered business (obrt or company), not a private person."
    )
    helper_business_name: str | None = strawberry.field(
        description="A trader helper's business name; null for a private helper."
    )
    helper_business_oib: str | None = strawberry.field(
        description="A trader helper's business OIB; null for a private helper."
    )
    helper_business_address: str | None = strawberry.field(
        description=(
            "A trader helper's registered business address on one line, never a home "
            "address. Only the task's poster sees it; null for everyone else and for a "
            "private helper."
        )
    )
    status: OfferStatusEnum
    withdrawn_reason: OfferWithdrawnReasonEnum | None = strawberry.field(
        description=(
            "Why TaskBeavo withdrew the offer, for its helper only; null when the helper "
            "withdrew it, when it isn't withdrawn, and for the poster."
        )
    )
    price_cents: int = strawberry.field(description="Euro cents; at least 1000.")
    service_fee_cents: int = strawberry.field(
        description=(
            "TaskBeavo's service fee on this price from the price list in force, before any credit."
        )
    )
    service_fee_credit_cents: int = strawberry.field(
        description=(
            "For the task's poster: the TaskBeavo credit checkout would take off the fee "
            "now (show it as its own line); 0 for everyone else."
        )
    )
    service_fee_schedule_version: str = strawberry.field(
        description=(
            "The price list the fee comes from; send it back as acceptOffer's "
            "expectedFeeScheduleVersion."
        )
    )
    service_fee_percent: int = strawberry.field(
        description="The rate of that fee in the price list in force (12 today)."
    )
    service_fee_minimum_cents: int = strawberry.field(
        description="The minimum fee in the price list in force (250 today)."
    )
    total_cents: int = strawberry.field(
        description=(
            "What the poster pays: price plus fee, less `serviceFeeCreditCents`. Show it "
            "first on offer cards; send it back as acceptOffer's expectedTotalCents."
        )
    )
    message: str = strawberry.field(description="Contact details are masked.")
    message_masked: bool | None = strawberry.field(
        description="Contact details were hidden in the message. Helper only."
    )
    expires_at: dt.datetime = strawberry.field(
        description="7 days after sending or renewing, never past the task's date."
    )
    is_mine: bool
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, offer: Offer, viewer: User, *, credit_left: int | None = None) -> "OfferType":
        """`credit_left`: the viewer's unspent credit, read once for a whole list."""
        mine = offer.helper_id == viewer.pk
        profile = getattr(offer.helper, "helper_profile", None)
        # One read of the clock, so the fee, its rule and the total come from one schedule.
        schedule = fee_schedule_at()
        fee = schedule.fee_cents(offer.price_cents)
        # A trader can't send offers until a company is verified and the registered address
        # is set (helpers.services), but an offer outlives later profile edits: check again.
        trader = profile if profile and profile.is_trader else None
        shown = trader if trader and not trader.needs_business_verification else None
        # Often the obrt owner's home: only for the poster deciding whether to book.
        for_poster = offer.task.poster_id == viewer.pk
        if for_poster and credit_left is None:
            credit_left = available_cents(viewer)
        credit = min(credit_left or 0, fee) if for_poster else 0
        return cls(
            id=strawberry.ID(str(offer.pk)),
            task_id=strawberry.ID(str(offer.task_id)),
            helper_user_id=strawberry.ID(str(offer.helper_id)),
            helper_is_trader=trader is not None,
            helper_business_name=(shown.business_name or None) if shown else None,
            helper_business_oib=(shown.business_oib or None) if shown else None,
            helper_business_address=(
                (shown.business_address or None) if shown and for_poster else None
            ),
            status=OfferStatusEnum(offer.status),
            withdrawn_reason=(
                OfferWithdrawnReasonEnum(offer.withdrawn_reason)
                if mine and offer.withdrawn_reason
                else None
            ),
            price_cents=offer.price_cents,
            service_fee_cents=fee,
            service_fee_credit_cents=credit,
            service_fee_schedule_version=schedule.version,
            service_fee_percent=schedule.percent,
            service_fee_minimum_cents=schedule.minimum_cents,
            total_cents=offer.price_cents + fee - credit,
            message=offer.message,
            message_masked=offer.moderation_verdict == Verdict.MASKED.value if mine else None,
            expires_at=offer.expires_at,
            is_mine=mine,
            created_at=offer.created_at,
            updated_at=offer.updated_at,
        )


@strawberry.input
class SendOfferInput:
    task_id: strawberry.ID
    price_cents: int = strawberry.field(description="Euro cents; at least 1000.")
    message: str = strawberry.field(default="", description="Optional, up to 1000 characters.")


@strawberry.input(description="Fields to change; omit all of them to confirm the offer as it is.")
class UpdateOfferInput:
    price_cents: int | None = None
    message: str | None = None


_HELPER_ERRORS = (
    "UNAUTHENTICATED, TERMS_UPDATE_REQUIRED, ACCOUNT_SUSPENDED, ACCOUNT_DELETION_PENDING, "
    "PHONE_NOT_VERIFIED, HELPER_PROFILE_INCOMPLETE, PAYOUT_ACCOUNT_NOT_READY, "
    "BUSINESS_ADDRESS_REQUIRED"
)
_TASK_ERRORS = (
    "TASK_NOT_OPEN_FOR_OFFERS, BUSINESS_POSTER_REGISTERED_ONLY, OUT_OF_SERVICE_AREA, "
    "TASK_OUTSIDE_HELPER_AREAS"
)
_DETAIL_ERRORS = "OFFER_PRICE_BELOW_MINIMUM, INVALID_OFFER_DETAILS, OFFER_MESSAGE_CONTACT_DETAILS"


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "Every offer on your task, newest first, including withdrawn and expired ones. "
            "If you book for a business, private helpers' offers aren't listed. "
            "Errors: UNAUTHENTICATED, TASK_NOT_FOUND."
        )
    )
    async def task_offers(self, info: Info, task_id: strawberry.ID) -> list[OfferType]:
        user = _user(info)
        return await sync_to_async(_task_offers)(user, str(task_id))

    @strawberry.field(description="Your offers as a helper, newest first. Errors: UNAUTHENTICATED.")
    async def my_offers(self, info: Info) -> list[OfferType]:
        user = _user(info)
        return await sync_to_async(_my_offers)(user)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Send an offer on an open task; it lasts 7 days, never past the task's date. "
            f"Errors: {_HELPER_ERRORS}, TASK_NOT_FOUND, OFFER_ON_OWN_TASK, "
            f"OFFER_ALREADY_SENT, {_TASK_ERRORS}, {_DETAIL_ERRORS}."
        ),
    )
    async def send_offer(self, info: Info, input: SendOfferInput) -> OfferType:
        user = _user(info)
        offer = await sync_to_async(services.send_offer)(
            user, str(input.task_id), input.price_cents, input.message
        )
        return await sync_to_async(OfferType.of)(offer, user)

    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Change your open offer, or confirm one that NEEDS_UPDATE; it is SENT again. "
            f"Errors: {_HELPER_ERRORS}, OFFER_NOT_FOUND, OFFER_NOT_EDITABLE, "
            f"{_TASK_ERRORS}, {_DETAIL_ERRORS}."
        ),
    )
    async def update_offer(
        self, info: Info, id: strawberry.ID, input: UpdateOfferInput
    ) -> OfferType:
        user = _user(info)
        offer = await sync_to_async(services.update_offer)(
            user, str(id), price_cents=input.price_cents, message=input.message
        )
        return await sync_to_async(OfferType.of)(offer, user)

    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Give your offer another 7 days from now, never past the task's date; an expired "
            f"offer is SENT again. Errors: {_HELPER_ERRORS}, OFFER_NOT_FOUND, "
            f"OFFER_NOT_EDITABLE, OFFER_ALREADY_SENT, {_TASK_ERRORS}."
        ),
    )
    async def renew_offer(self, info: Info, id: strawberry.ID) -> OfferType:
        user = _user(info)
        offer = await sync_to_async(services.renew_offer)(user, str(id))
        return await sync_to_async(OfferType.of)(offer, user)

    @strawberry.mutation(
        description=(
            "Withdraw your open offer. Errors: UNAUTHENTICATED, OFFER_NOT_FOUND, "
            "OFFER_NOT_EDITABLE."
        )
    )
    async def withdraw_offer(self, info: Info, id: strawberry.ID) -> OfferType:
        user = _user(info)
        offer = await sync_to_async(services.withdraw_offer)(user, str(id))
        return await sync_to_async(OfferType.of)(offer, user)


@strawberry.type
class Subscription:
    @strawberry.subscription(
        description="A helper sent an offer on your task. Errors: UNAUTHENTICATED."
    )
    async def offer_received(
        self, info: Info, task_id: strawberry.ID
    ) -> AsyncGenerator[OfferType, None]:
        async for offer in viewer_events(
            info, {services.RECEIVED_TOPIC}, _poster_loader(str(task_id))
        ):
            yield offer

    @strawberry.subscription(
        description="A helper withdrew an offer on your task. Errors: UNAUTHENTICATED."
    )
    async def offer_withdrawn(
        self, info: Info, task_id: strawberry.ID
    ) -> AsyncGenerator[OfferType, None]:
        async for offer in viewer_events(
            info, {services.WITHDRAWN_TOPIC}, _poster_loader(str(task_id))
        ):
            yield offer

    @strawberry.subscription(
        description=(
            "An offer on your task expires within a day; the helper may renew it. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def offer_expiring(
        self, info: Info, task_id: strawberry.ID
    ) -> AsyncGenerator[OfferType, None]:
        async for offer in viewer_events(
            info, {services.EXPIRING_TOPIC}, _poster_loader(str(task_id))
        ):
            yield offer

    @strawberry.subscription(
        description=(
            "A helper changed or renewed an offer on your task: its price, message or expiry. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def offer_updated(
        self, info: Info, task_id: strawberry.ID
    ) -> AsyncGenerator[OfferType, None]:
        async for offer in viewer_events(
            info, {services.UPDATED_TOPIC}, _poster_loader(str(task_id))
        ):
            yield offer


def _poster_loader(
    task_id: str,
) -> Callable[[AbstractBaseUser, LiveEvent], Awaitable[OfferType | None]]:
    async def load(viewer: AbstractBaseUser, event: LiveEvent) -> OfferType | None:
        return await sync_to_async(_offer_for_poster)(viewer, event.aggregate_id, task_id)

    return load


def _offer_for_poster(viewer: AbstractBaseUser, offer_id: str, task_id: str) -> OfferType | None:
    """The current offer, if it is on `task_id` and the viewer posted that task (the
    per-event check). Its status may have moved on since the event; the client shows what
    it is now. A malformed task ID simply matches nothing."""
    if not isinstance(viewer, User):
        return None
    try:
        offer_pk, task_pk = UUID(offer_id), UUID(task_id)
    except ValueError:
        return None
    offers = Offer.objects.filter(pk=offer_pk, task_id=task_pk, task__poster_id=viewer.pk)
    # A business poster never sees a private helper's offer (accounts.business).
    offer = (
        services.visible_to_poster(offers, viewer)
        .select_related("task", "helper__helper_profile")
        .first()
    )
    return None if offer is None else OfferType.of(offer, viewer)


def _task_offers(user: User, task_id: str) -> list[OfferType]:
    offers = services.offers_on_task(user, task_id)
    # One credit read for the list, not one per offer (only the poster sees a credit).
    left = available_cents(user) if any(o.task.poster_id == user.pk for o in offers) else 0
    return [OfferType.of(offer, user, credit_left=left) for offer in offers]


def _my_offers(user: User) -> list[OfferType]:
    return [OfferType.of(offer, user) for offer in services.my_offers(user)]


def _user(info: Info[Any, Any]) -> User:
    return require_signed_in(info.context.user)
