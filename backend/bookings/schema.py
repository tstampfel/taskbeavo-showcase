"""GraphQL API of bookings: acceptOffer opens the checkout (bookings/checkout.py).

cancelBooking and withdrawFromContract (bookings/cancel.py, BEAV-122) are the poster's;
the refund preview and the withdrawal button's availability are shown to the poster only,
the settled amounts (`cancellation`) to both sides. cancelAsHelper and its preview are the
helper's, reopenTask the poster's (bookings/helper_cancel.py); either side requests a
no-fault cancellation and the other responds (bookings/no_fault.py, BEAV-123). The amounts
all come from bookings/cancellation.py (hard rule g).

A booking is visible to its poster and its helper only (`booking`, `bookingUpdated`). It
carries its task as `tasks.schema.TaskType` builds it for the viewer, so the exact address,
access details and on-site contact follow the one rule in `bookings.queries.active` (hard
rule e): the helper loses them in the first answer or live event after the booking stops
being active. A trader helper's business name and OIB go to the poster, as confirmed at
checkout (`helperBusiness`, BEAV-192); its registered business address stays on the offer
and the checkout (hard rule e), so the booking never carries it.

The client secret goes to the poster of the booking only: in the acceptOffer answer, and
again from `checkoutSession` while the checkout is open (BEAV-111), so a poster who left
the payment sheet retries on the same PaymentIntent. Stripe's payment sheet needs it to
confirm the payment (3D Secure). The card attempts of a checkout (a decline code, the
start of a 3D Secure check) are the poster's: the helper never sees them.
"""

import datetime as dt
from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any
from uuid import UUID

import strawberry
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Q
from django.utils import timezone
from strawberry.types import Info

from accounts.models import User
from accounts.permissions import CanCreateWork, require_signed_in
from accounts.schema import Locale, SessionPlatform
from bookings import arrival, cancel, cancellation, checkout, helper_cancel, money, no_fault
from bookings.arrival_schema import BookingArrivalType
from bookings.cancellation import NotACancellation, charged_cents, withdrawal_period_ends_at
from bookings.models import (
    Booking,
    BookingEvent,
    CancellationSettlement,
    ConsentRecord,
    NoFaultRequest,
)
from bookings.money import (
    CROATIAN_VAT_PERCENT,
    FEE_EXAMPLE_PRICES_CENTS,
    FeeSchedule,
    FeeVat,
    fee_schedule_at,
)
from bookings.settlement import outcome_of
from bookings.state_machine import UPDATED_TOPIC
from core.errors import ApiError, ErrorCode
from core.realtime import LiveEvent, viewer_events
from legal.checkout_consent import PRIVATE, TRADER
from tasks.schema import TaskType


@strawberry.enum(
    name="BookingStatus",
    description=(
        "Where a booking is, from checkout (AWAITING_PAYMENT) to CLOSED. FUNDS_HELD and the "
        "other money-confirmed statuses are set only from Stripe's webhook."
    ),
)
class BookingStatusEnum(Enum):
    AWAITING_PAYMENT = Booking.Status.AWAITING_PAYMENT.value
    PAYMENT_FAILED = Booking.Status.PAYMENT_FAILED.value
    FUNDS_HELD = Booking.Status.FUNDS_HELD.value
    CANCEL_REQUESTED = Booking.Status.CANCEL_REQUESTED.value
    NO_ACCESS_REPORTED = Booking.Status.NO_ACCESS_REPORTED.value
    INCIDENT_PAUSED = Booking.Status.INCIDENT_PAUSED.value
    PAYMENT_REQUESTED = Booking.Status.PAYMENT_REQUESTED.value
    DISPUTED = Booking.Status.DISPUTED.value
    RELEASED = Booking.Status.RELEASED.value
    PAID_OUT = Booking.Status.PAID_OUT.value
    PARTIALLY_SETTLED = Booking.Status.PARTIALLY_SETTLED.value
    REFUNDED = Booking.Status.REFUNDED.value
    CLOSED = Booking.Status.CLOSED.value


@strawberry.enum(description="Why a checkout ended without a payment (PAYMENT_FAILED).")
class CheckoutFailureReason(Enum):
    CHECKOUT_EXPIRED = strawberry.enum_value(
        "CHECKOUT_EXPIRED",
        description=(
            "Nobody paid within 30 minutes of the last card attempt, or 60 minutes of "
            "accepting. The offer is open again unless the helper withdrew it."
        ),
    )
    OFFER_WITHDRAWN = strawberry.enum_value(
        "OFFER_WITHDRAWN", description="The helper withdrew the offer during checkout."
    )
    PAYMENT_NOT_STARTED = strawberry.enum_value(
        "PAYMENT_NOT_STARTED", description="The payment couldn't be started at Stripe."
    )


@strawberry.enum(description="A card attempt during checkout, as Stripe's webhook reported it.")
class PaymentAttemptKind(Enum):
    DECLINED = strawberry.enum_value(
        checkout.PAYMENT_ATTEMPT_FAILED,
        description="The card was declined or 3D Secure failed; try again or another card.",
    )
    AUTHENTICATION_STARTED = strawberry.enum_value(
        checkout.PAYMENT_ACTION_REQUIRED,
        description="The bank asked the cardholder to confirm, in 3D Secure or a banking app.",
    )


@strawberry.type(description="The last card attempt of an open checkout. The poster's only.")
class PaymentAttempt:
    id: strawberry.ID
    kind: PaymentAttemptKind
    decline_code: str | None = strawberry.field(
        description=(
            "Stripe's decline code as an enum key (for example insufficient_funds), or "
            "unknown; null unless DECLINED. Translate it; never show it raw."
        )
    )
    at: dt.datetime

    @classmethod
    def of(cls, event: BookingEvent) -> "PaymentAttempt":
        kind = PaymentAttemptKind(event.type)
        code = (
            (event.data or {}).get("decline_code") if kind is PaymentAttemptKind.DECLINED else None
        )
        return cls(
            id=strawberry.ID(str(event.pk)),
            kind=kind,
            decline_code=code or ("unknown" if kind is PaymentAttemptKind.DECLINED else None),
            at=event.created_at,
        )


@strawberry.enum(
    description=(
        "The cancellation policy row that applies (bookings/cancellation.py): which "
        "refund the poster gets."
    )
)
class CancellationCase(Enum):
    GRACE_PERIOD = strawberry.enum_value(
        "GRACE_PERIOD", description="Within 1 h of accepting: full refund."
    )
    EARLY = strawberry.enum_value(
        "EARLY", description="24 h or more before the start: full refund."
    )
    LATE = strawberry.enum_value(
        "LATE", description="Under 24 h before the start: the helper keeps 25% (EUR 5 to 20)."
    )
    LATE_TRADER_WITHDRAWAL = strawberry.enum_value(
        "LATE_TRADER_WITHDRAWAL",
        description=(
            "Late, but a registered business inside its 14-day withdrawal period: full refund."
        ),
    )
    HELPER_CANCEL = strawberry.enum_value(
        "HELPER_CANCEL", description="The helper cancelled: full refund."
    )
    NO_FAULT = strawberry.enum_value("NO_FAULT", description="Nobody's fault: full refund.")
    NO_ACCESS_CALL_OUT = strawberry.enum_value(
        "NO_ACCESS_CALL_OUT", description="The helper couldn't get in: the call-out is paid."
    )
    WITHDRAWAL = strawberry.enum_value(
        "WITHDRAWAL", description="Withdrawal from the contract before the start: full refund."
    )
    WITHDRAWAL_PRO_RATA = strawberry.enum_value(
        "WITHDRAWAL_PRO_RATA",
        description="Withdrawal after the start: the helper keeps the share of the work done.",
    )


@strawberry.enum(description="Why the poster cancels. The no-fault reasons have their own flow.")
class PosterCancelReason(Enum):
    NO_LONGER_NEEDED = cancel.PosterCancelReason.NO_LONGER_NEEDED.value
    DIFFERENT_DATE = cancel.PosterCancelReason.DIFFERENT_DATE.value
    HELPER_ASKED = cancel.PosterCancelReason.HELPER_ASKED.value
    OTHER = cancel.PosterCancelReason.OTHER.value


@strawberry.enum(description="Why the helper cancels. The no-fault reasons have their own flow.")
class HelperCancelReason(Enum):
    ILL = helper_cancel.HelperCancelReason.ILL.value
    DOUBLE_BOOKED = helper_cancel.HelperCancelReason.DOUBLE_BOOKED.value
    OTHER = helper_cancel.HelperCancelReason.OTHER.value
    CANT_COME = helper_cancel.HelperCancelReason.CANT_COME.value


@strawberry.enum(
    description=(
        "A reason that makes a cancellation nobody's fault: a full refund once the other "
        "side agrees, or support upholds it."
    )
)
class NoFaultReason(Enum):
    WEATHER = cancellation.NoFaultReason.WEATHER.value
    HOSPITALISATION = cancellation.NoFaultReason.HOSPITALISATION.value
    BEREAVEMENT = cancellation.NoFaultReason.BEREAVEMENT.value
    EMERGENCY = cancellation.NoFaultReason.EMERGENCY.value
    MUTUAL_AGREEMENT = cancellation.NoFaultReason.MUTUAL_AGREEMENT.value
    HELPER_LATE = cancellation.NoFaultReason.HELPER_LATE.value


@strawberry.enum(
    description=(
        "What the other side sees of a no-fault reason: hospitalisation, bereavement and "
        "emergency all show as PERSONAL_EMERGENCY (GDPR Art. 9)."
    )
)
class NoFaultLabel(Enum):
    PERSONAL_EMERGENCY = cancellation.NoFaultLabel.PERSONAL_EMERGENCY.value
    WEATHER = cancellation.NoFaultLabel.WEATHER.value
    MUTUAL_AGREEMENT = cancellation.NoFaultLabel.MUTUAL_AGREEMENT.value
    HELPER_LATE = cancellation.NoFaultLabel.HELPER_LATE.value


@strawberry.enum(description="Which side of a booking.")
class BookingSide(Enum):
    POSTER = "POSTER"
    HELPER = "HELPER"


@strawberry.enum(description="How the other side answered a no-fault request.")
class NoFaultResponse(Enum):
    AGREED = NoFaultRequest.Response.AGREED.value
    DISAGREED = NoFaultRequest.Response.DISAGREED.value
    NO_REPLY = strawberry.enum_value(
        NoFaultRequest.Response.NO_REPLY.value,
        description=(
            "No answer within 48 hours: the poster's request stands; the helper's goes to support."
        ),
    )


@strawberry.enum(description="Support's decision on a no-fault request.")
class NoFaultDecision(Enum):
    UPHELD = strawberry.enum_value(
        NoFaultRequest.Decision.UPHELD.value, description="Nobody's fault: full refund."
    )
    REJECTED = strawberry.enum_value(
        NoFaultRequest.Decision.REJECTED.value,
        description="The ordinary policy applies to whoever asked, as of when they asked.",
    )


@strawberry.type(
    name="NoFaultRequest",
    description=(
        "A no-fault cancellation request on the booking: the open one, or the last one. "
        "Shown to both sides; IDs, enum keys and times only."
    ),
)
class NoFaultRequestType:
    id: strawberry.ID
    reason: NoFaultReason | None = strawberry.field(
        description="The specific reason, for the side that asked only; null for the other."
    )
    label: NoFaultLabel = strawberry.field(description="What both sides are shown.")
    requested_by: BookingSide
    requested_by_you: bool
    requested_at: dt.datetime
    respond_by: dt.datetime = strawberry.field(
        description="The other side answers by then (48 hours after the request)."
    )
    response: NoFaultResponse | None
    awaiting_support: bool = strawberry.field(description="Support is deciding.")
    can_respond: bool = strawberry.field(
        description=(
            "The viewer is the other side and may agree or disagree now: the helper until "
            "`respondBy`, the poster until support decided."
        )
    )
    decision: NoFaultDecision | None
    open: bool = strawberry.field(description="Still waiting for an answer or a decision.")

    @classmethod
    def of(cls, request: NoFaultRequest, viewer: User, booking: Booking) -> "NoFaultRequestType":
        viewer_side = "POSTER" if booking.poster_id == viewer.pk else "HELPER"
        is_open = request.closed_at is None
        return cls(
            id=strawberry.ID(str(request.pk)),
            reason=(NoFaultReason(request.reason) if request.requested_by == viewer_side else None),
            label=NoFaultLabel(
                cancellation.no_fault_label(cancellation.NoFaultReason(request.reason)).value
            ),
            requested_by=BookingSide(request.requested_by),
            requested_by_you=request.requested_by == viewer_side,
            requested_at=request.requested_at,
            respond_by=request.respond_by,
            response=NoFaultResponse(request.response) if request.response else None,
            awaiting_support=is_open and request.support_needed_at is not None,
            can_respond=(
                is_open
                and request.response in ("", NoFaultRequest.Response.NO_REPLY)
                and request.requested_by != viewer_side
                and booking.status == Booking.Status.CANCEL_REQUESTED
                # The 48 hours decide, even before the sweep ran (NO_FAULT_REPLY_CLOSED).
                and not no_fault.reply_closed(request, timezone.now())
            ),
            decision=NoFaultDecision(request.decision) if request.decision else None,
            open=is_open,
        )


@strawberry.type(
    description=(
        "What the helper's cancel would mean now, for the helper while FUNDS_HELD: the "
        "poster always gets everything back, plus a EUR 5 credit from TaskBeavo when it is "
        "under 24 h before the start. The helper never pays. `refusedWith` is set instead "
        "once payment was requested."
    )
)
class HelperCancelPreview:
    id: strawberry.ID = strawberry.field(description="The booking's ID.")
    refund_cents: int = strawberry.field(description="Back to the poster's card.")
    poster_credit_cents: int = strawberry.field(description="TaskBeavo's credit to the poster.")
    refused_with: ErrorCode | None


@strawberry.type(
    description=(
        "What cancelling the booking now would refund, for its poster while FUNDS_HELD. "
        "`refusedWith` is set instead of the amounts when cancelBooking would be refused "
        "(WORK_STARTED, BOOKING_ACTION_NOT_ALLOWED)."
    )
)
class CancellationPreview:
    id: strawberry.ID = strawberry.field(description="The booking's ID.")
    case: CancellationCase | None
    charge_cents: int = strawberry.field(description="What the poster paid.")
    refund_cents: int = strawberry.field(description="Back to the poster's card.")
    helper_compensation_cents: int = strawberry.field(description="What the helper keeps.")
    kept_fee_cents: int = strawberry.field(
        description="TaskBeavo's share of what the helper keeps, at the booking's fee rate."
    )
    credit_back_cents: int = strawberry.field(
        description=(
            "The TaskBeavo credit the booking used, which comes back to the poster once the "
            "refund is confirmed (rule g); 0 when none was used."
        )
    )
    refused_with: ErrorCode | None


@strawberry.type(
    description=(
        "A cancellation or withdrawal of the booking and its money. The amounts are set "
        "once known; a withdrawal after the start waits for support (`awaitingSupport`). "
        "`confirmed` once Stripe confirmed the refund."
    )
)
class Cancellation:
    id: strawberry.ID = strawberry.field(description="The booking's ID.")
    withdrawal: bool = strawberry.field(description="A withdrawal from the contract.")
    case: CancellationCase | None
    refund_cents: int | None
    helper_compensation_cents: int | None
    kept_fee_cents: int | None
    awaiting_support: bool
    confirmed: bool
    requested_at: dt.datetime | None
    requested_by: BookingSide | None = strawberry.field(description="Who cancelled.")
    poster_credit_cents: int = strawberry.field(
        description=(
            "TaskBeavo's EUR 5 credit to the poster for a helper's late cancel, granted once "
            "the refund is confirmed; 0 otherwise."
        )
    )
    credit_back_cents: int | None = strawberry.field(
        description=(
            "For the poster: the TaskBeavo credit the booking used, back once the refund is "
            "confirmed (rule g). Null for the helper, like `refundCents` and `keptFeeCents`, "
            "so the poster's credit stays between the poster and TaskBeavo."
        )
    )


@strawberry.type(
    name="BookingTrader",
    description=(
        "A registered-business helper as the poster confirmed it at checkout; later profile "
        "edits don't change it. Its `id` is the booking's."
    ),
)
class BookingTraderType:
    id: strawberry.ID
    business_name: str
    business_oib: str

    @classmethod
    def of(cls, booking: Booking) -> "BookingTraderType | None":
        consent = ConsentRecord.objects.filter(booking=booking).first()
        if consent is None or not consent.helper_is_trader:
            return None
        return cls(
            id=strawberry.ID(str(booking.pk)),
            business_name=consent.trader_name,
            business_oib=consent.trader_oib,
        )


@strawberry.type(name="Booking", description="An accepted offer, from checkout to payout.")
class BookingType:
    id: strawberry.ID
    task_id: strawberry.ID
    task: TaskType = strawberry.field(
        description=(
            "The booked task as you may see it: the exact address, access details and "
            "on-site contact for the poster, and for the helper only while the booking is "
            "active; otherwise the approximate area (hard rule e)."
        )
    )
    offer_id: strawberry.ID
    helper_user_id: strawberry.ID = strawberry.field(
        description="The helper's user ID, for helperProfile."
    )
    helper_is_trader: bool = strawberry.field(
        description=(
            "The helper is a registered business, as the poster saw it at checkout: it "
            "decides whether the task itself carries a withdrawal right."
        )
    )
    helper_business: BookingTraderType | None = strawberry.field(
        description=(
            "For the poster of a registered business's booking: its name and OIB. Null "
            "for a private helper, and for the helper."
        )
    )
    status: BookingStatusEnum
    amount_cents: int = strawberry.field(description="The offer's price, which the helper gets.")
    fee_cents: int = strawberry.field(
        description=(
            "TaskBeavo's service fee charged: from the price list in force when the offer "
            "was accepted (12% of the price, minimum EUR 2.50 today), less `creditCents`."
        )
    )
    credit_cents: int | None = strawberry.field(
        description=(
            "For the poster: the TaskBeavo credit taken off the fee at checkout (its own "
            "line on the receipt screens); null for the helper."
        )
    )
    total_cents: int = strawberry.field(description="What the poster pays: price plus fee.")
    slot_start: dt.datetime = strawberry.field(description="When the job starts.")
    terms_version: str = strawberry.field(
        description="The terms version this booking is governed by."
    )
    cancellation_version: str = strawberry.field(
        description="The cancellation policy version this booking is governed by."
    )
    checkout_expires_at: dt.datetime | None = strawberry.field(
        description=(
            "While AWAITING_PAYMENT: when the checkout ends unpaid. Each card attempt moves "
            "it to 30 minutes later, never past 60 minutes after accepting."
        )
    )
    last_payment_attempt: PaymentAttempt | None = strawberry.field(
        description="While AWAITING_PAYMENT, the last card attempt. Poster only."
    )
    checkout_failure_reason: CheckoutFailureReason | None = strawberry.field(
        description="Why the checkout ended, while PAYMENT_FAILED."
    )
    cancel_preview: CancellationPreview | None = strawberry.field(
        description="For the poster while FUNDS_HELD: what cancelling now would refund."
    )
    withdrawal_available: bool = strawberry.field(
        description=(
            "For the poster: the withdrawal button is offered now (a registered business "
            "helper, inside the 14-day period, before payment is requested)."
        )
    )
    withdrawal_ends_at: dt.datetime | None = strawberry.field(
        description="For the poster of a registered business's booking: when the 14 days end."
    )
    cancellation: Cancellation | None = strawberry.field(
        description="Set once the booking was cancelled or withdrawn from."
    )
    helper_cancel_preview: HelperCancelPreview | None = strawberry.field(
        description="For the helper while FUNDS_HELD: what their cancel would mean now."
    )
    no_fault_request: NoFaultRequestType | None = strawberry.field(
        description="The open no-fault request, or the last one."
    )
    can_request_no_fault: bool = strawberry.field(
        description="Either side may cite a no-fault reason now (while FUNDS_HELD)."
    )
    can_reopen_task: bool = strawberry.field(
        description=(
            "For the poster after a helper or no-fault cancel: the task can be re-opened to "
            "the other offers (reopenTask)."
        )
    )
    task_reopened: bool = strawberry.field(description="The poster re-opened the task.")
    arrival: BookingArrivalType = strawberry.field(
        description="The day of the job: On my way, arrival, the no-show timeline, early start."
    )
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, booking: Booking, viewer: User) -> "BookingType":
        """`booking` as `viewer`, its poster or helper, sees it. Database reads: call it
        from sync code."""
        in_checkout = booking.status == Booking.Status.AWAITING_PAYMENT
        attempt = (
            checkout.latest_attempt(booking)
            if in_checkout and booking.poster_id == viewer.pk
            else None
        )
        reason = checkout.failure_reason(booking)
        is_poster = booking.poster_id == viewer.pk
        shown_fee = booking.fee_cents + booking.credit_cents
        helper_is_trader = _helper_is_trader(booking)
        # Read once: the cancellation and whether the task can be re-opened both need it.
        settled = CancellationSettlement.objects.filter(booking=booking).first()
        return cls(
            id=strawberry.ID(str(booking.pk)),
            task_id=strawberry.ID(str(booking.task_id)),
            task=TaskType.of(booking.task, viewer),
            offer_id=strawberry.ID(str(booking.offer_id)),
            helper_user_id=strawberry.ID(str(booking.helper_id)),
            helper_is_trader=helper_is_trader,
            helper_business=BookingTraderType.of(booking) if is_poster else None,
            status=BookingStatusEnum(booking.status),
            amount_cents=booking.amount_cents,
            # The helper sees the schedule's fee, so a poster's credit from an earlier
            # helper cancel stays between the poster and TaskBeavo.
            fee_cents=booking.fee_cents if is_poster else shown_fee,
            credit_cents=booking.credit_cents if is_poster else None,
            total_cents=charged_cents(
                booking.amount_cents, booking.fee_cents if is_poster else shown_fee
            ),
            slot_start=booking.slot_start,
            terms_version=booking.terms_version,
            cancellation_version=booking.cancellation_version,
            checkout_expires_at=checkout.checkout_expires_at(booking) if in_checkout else None,
            last_payment_attempt=PaymentAttempt.of(attempt) if attempt else None,
            checkout_failure_reason=CheckoutFailureReason(reason) if reason else None,
            cancel_preview=_preview(booking) if is_poster else None,
            withdrawal_available=is_poster and cancel.withdrawal_available(booking),
            withdrawal_ends_at=(
                withdrawal_period_ends_at(booking.created_at)
                if is_poster and helper_is_trader
                else None
            ),
            cancellation=_cancellation(booking, settled, is_poster=is_poster),
            helper_cancel_preview=(
                _helper_preview(booking) if booking.helper_id == viewer.pk else None
            ),
            no_fault_request=(
                NoFaultRequestType.of(request, viewer, booking)
                if (request := no_fault.latest_request(booking))
                else None
            ),
            can_request_no_fault=no_fault.can_request(booking, "POSTER" if is_poster else "HELPER"),
            can_reopen_task=is_poster
            and settled is not None
            and helper_cancel.can_reopen(booking, case=settled.case),
            task_reopened=booking.task_reopened_at is not None,
            arrival=BookingArrivalType.of(booking, viewer),
            created_at=booking.created_at,
            updated_at=booking.updated_at,
        )


@strawberry.type(
    description=(
        "An open checkout: confirm the payment with Stripe's payment sheet using "
        "`clientSecret`, then wait for the booking to become FUNDS_HELD."
    )
)
class CheckoutSession:
    id: strawberry.ID = strawberry.field(description="The booking's ID.")
    booking: BookingType
    client_secret: str = strawberry.field(
        description="For Stripe's payment sheet only; never store or log it."
    )
    payment_pending: bool = strawberry.field(
        description=(
            "Stripe already has a payment that is succeeding or still being confirmed: "
            "don't pay again, wait for bookingUpdated."
        )
    )


@strawberry.enum(
    description=(
        "Which of the CANCELLATION policy's two tick-box pairs the checkout showed: the one "
        "for a private helper or the one for a registered business (trader)."
    )
)
class CheckoutConsentVariant(Enum):
    PRIVATE_HELPER = PRIVATE
    TRADER_HELPER = TRADER


@strawberry.input(
    description=(
        "The two separate, unticked checkout boxes. Both must be ticked, for the "
        "cancellation policy version shown, which must be the one in force."
    )
)
class CheckoutConsentInput:
    cancellation_policy_accepted: bool = strawberry.field(
        description="I accept the cancellation and refund policy."
    )
    early_start_requested: bool = strawberry.field(
        description=(
            "I ask for the booking to be arranged now and the task done on the date I chose, "
            "which may be within my 14-day withdrawal period."
        )
    )
    cancellation_version: str = strawberry.field(
        description="The CANCELLATION version the checkout showed."
    )
    platform: SessionPlatform
    language: Locale = strawberry.field(
        description="The language the checkout showed the policy in; its version is checked in it."
    )
    variant: CheckoutConsentVariant = strawberry.field(
        description=(
            "The tick-box pair shown, from the offer's helperIsTrader; it must match the "
            "helper's status when the offer is accepted (CONSENT_WORDING_CHANGED)."
        )
    )


@strawberry.type(
    name="ServiceFeeExample",
    description="A worked example of a price list: what a poster pays for one task price.",
)
class ServiceFeeExampleType:
    id: strawberry.ID = strawberry.field(description="The schedule's version and the price.")
    task_price_cents: int = strawberry.field(description="The helper's price for the task.")
    fee_cents: int = strawberry.field(description="TaskBeavo's service fee on that price.")
    total_cents: int = strawberry.field(description="What the poster pays: price plus fee.")
    vat_cents: int | None = strawberry.field(
        description="The VAT inside the fee when the schedule's `vat` is INCLUDED; else null."
    )


@strawberry.enum(
    name="ServiceFeeVat",
    description=(
        "Whether the service fee includes VAT. UNDECIDED until the accountant picks "
        "INCLUDED (variant A) or NOT_CHARGED (variant B); show neither while UNDECIDED."
    ),
)
class ServiceFeeVatEnum(Enum):
    UNDECIDED = FeeVat.UNDECIDED.value
    INCLUDED = FeeVat.INCLUDED.value
    NOT_CHARGED = FeeVat.NOT_CHARGED.value


@strawberry.enum(description="Where a service fee price list stands today.")
class ServiceFeeScheduleStatus(Enum):
    UPCOMING = "UPCOMING"
    CURRENT = "CURRENT"
    EARLIER = "EARLIER"


@strawberry.type(
    name="ServiceFeeSchedule",
    description=(
        "One published price list of TaskBeavo's service fee (ZZP čl. 7(9)): `percent` of "
        "the task price, at least `minimumCents`, from `effectiveFrom`. The numbers are "
        "the ones checkout charges (bookings/money.py); never copy them into a client."
    ),
)
class ServiceFeeScheduleType:
    id: strawberry.ID = strawberry.field(description="The same as `version`.")
    version: str
    effective_from: dt.date = strawberry.field(
        description="The day this list took (or takes) effect, Europe/Zagreb."
    )
    percent: int = strawberry.field(description="The fee as a whole percent of the task price.")
    minimum_cents: int = strawberry.field(description="The least fee charged, in euro cents.")
    status: ServiceFeeScheduleStatus = strawberry.field(
        description=(
            "CURRENT: checkout charges it now. UPCOMING: announced, charged from "
            "`effectiveFrom`. EARLIER: replaced, kept for the archive."
        )
    )
    vat: ServiceFeeVatEnum
    vat_percent: int = strawberry.field(description="The VAT rate when `vat` is INCLUDED.")
    examples: list[ServiceFeeExampleType] = strawberry.field(
        description="Worked examples for the /fees page, one of them at the minimum."
    )

    @classmethod
    def of(cls, schedule: FeeSchedule, current: FeeSchedule) -> "ServiceFeeScheduleType":
        if schedule is current:
            status = ServiceFeeScheduleStatus.CURRENT
        elif schedule.effective_from > current.effective_from:
            status = ServiceFeeScheduleStatus.UPCOMING
        else:
            status = ServiceFeeScheduleStatus.EARLIER
        return cls(
            id=strawberry.ID(schedule.version),
            version=schedule.version,
            effective_from=schedule.effective_from,
            percent=schedule.percent,
            minimum_cents=schedule.minimum_cents,
            status=status,
            vat=ServiceFeeVatEnum(schedule.vat.value),
            vat_percent=CROATIAN_VAT_PERCENT,
            examples=[
                ServiceFeeExampleType(
                    id=strawberry.ID(f"{schedule.version}:{price}"),
                    task_price_cents=price,
                    fee_cents=schedule.fee_cents(price),
                    total_cents=price + schedule.fee_cents(price),
                    vat_cents=schedule.vat_cents(schedule.fee_cents(price)),
                )
                for price in FEE_EXAMPLE_PRICES_CENTS
            ],
        )


@strawberry.type(
    name="CancellationAmounts",
    description=(
        "The numbers of the cancellation policy as bookings/cancellation.py applies them "
        "(hard rule g), for the refund table on /fees. Never copy them into a client."
    ),
)
class CancellationAmountsType:
    id: strawberry.ID = strawberry.field(description="Always `current`.")
    grace_period_minutes: int = strawberry.field(
        description="A cancellation this soon after accepting is a full refund."
    )
    late_window_hours: int = strawberry.field(
        description="Cancelling with less than this left before the start is late."
    )
    late_pay_percent: int
    late_pay_min_cents: int
    late_pay_max_cents: int
    call_out_percent: int
    call_out_min_cents: int
    call_out_max_cents: int
    kept_fee_percent: int = strawberry.field(
        description="The share of the helper's compensation TaskBeavo keeps: the fee rate."
    )
    helper_cancel_credit_cents: int
    withdrawal_period_days: int


@strawberry.type
class Mutation:
    @strawberry.mutation(
        permission_classes=[CanCreateWork],
        description=(
            "Accept an offer on your task and open its checkout; nothing is charged until "
            "you confirm the payment with Stripe. `expectedPriceCents` is the price you saw "
            "and `expectedTotalCents` the total on the pay button (price plus the service "
            "fee), `expectedFeeScheduleVersion` the price list the fee notice quoted "
            "(`Offer.serviceFeeScheduleVersion`); the server computes the fee from the list "
            "in force and refuses a total or list that no longer matches it (FEE_CHANGED: "
            "reload and show the new total). "
            "`slotStart` is the agreed day of a flexible-date task. Errors: UNAUTHENTICATED, "
            "TERMS_UPDATE_REQUIRED, ACCOUNT_SUSPENDED, ACCOUNT_DELETION_PENDING, "
            "PHONE_NOT_VERIFIED, CONSENT_REQUIRED, OFFER_NOT_FOUND, OFFER_NOT_EDITABLE, "
            "OFFER_NEEDS_UPDATE, OFFER_CHANGED, FEE_CHANGED, OFFER_NOT_AVAILABLE, "
            "TASK_ALREADY_BOOKED, BUSINESS_POSTER_REGISTERED_ONLY, "
            "OUT_OF_SERVICE_AREA, INVALID_TASK_DATES, HELPER_CANNOT_TAKE_PAYMENTS, "
            "PAYMENT_UNAVAILABLE."
        ),
    )
    async def accept_offer(
        self,
        info: Info,
        offer_id: strawberry.ID,
        expected_price_cents: int,
        expected_total_cents: int,
        expected_fee_schedule_version: str,
        consent: CheckoutConsentInput,
        slot_start: dt.datetime | None = None,
    ) -> CheckoutSession:
        user = _user(info)
        session = await sync_to_async(checkout.accept_offer)(
            user,
            str(offer_id),
            expected_price_cents=expected_price_cents,
            expected_total_cents=expected_total_cents,
            expected_fee_schedule_version=expected_fee_schedule_version,
            slot_start=slot_start,
            consent=checkout.CheckoutConsent(
                cancellation_policy_accepted=consent.cancellation_policy_accepted,
                early_start_requested=consent.early_start_requested,
                cancellation_version=consent.cancellation_version,
                platform=consent.platform.value,
                language=consent.language.value,
                variant=consent.variant.value,
            ),
        )
        booking = await sync_to_async(BookingType.of)(session.booking, user)
        return CheckoutSession(
            id=strawberry.ID(str(session.booking.pk)),
            booking=booking,
            client_secret=session.client_secret,
            payment_pending=False,
        )

    @strawberry.mutation(
        description=(
            "Cancel your booking as its poster, while the payment is held. The refund is the "
            "one `cancelPreview` shows: it is worked out again on the server, and Stripe "
            "confirms it (the booking becomes REFUNDED or PARTIALLY_SETTLED). "
            "`expectedRefundCents` is the preview's `refundCents` the poster saw. Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED, WORK_STARTED, "
            "CANCELLATION_CHANGED."
        )
    )
    async def cancel_booking(
        self, info: Info, id: strawberry.ID, reason: PosterCancelReason, expected_refund_cents: int
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(cancel.cancel_booking)(
            user,
            str(id),
            cancel.PosterCancelReason(reason.value),
            expected_refund_cents=expected_refund_cents,
        )
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "Withdraw from the contract (the withdrawal button), while `withdrawalAvailable`: "
            "before the start a full refund, after it support works out the part already "
            "done and refunds the rest. `name` and `email` are the poster's, prefilled from "
            "the account (ZZP čl. 81.a(3)); the confirmation is emailed to `email`. Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED, "
            "WITHDRAWAL_NOT_AVAILABLE, INVALID_WITHDRAWAL_DETAILS."
        )
    )
    async def withdraw_from_contract(
        self, info: Info, id: strawberry.ID, name: str, email: str
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(cancel.withdraw_from_contract)(
            user, str(id), name=name, email=email
        )
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "Cancel your booking as its helper, while the payment is held, before or after "
            "the start. The poster gets everything back, plus a EUR 5 credit from TaskBeavo "
            "when it is under 24 h before the start; you never pay anything, but the cancel "
            "counts against your reliability. Stripe confirms the refund (REFUNDED). Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def cancel_as_helper(
        self, info: Info, id: strawberry.ID, reason: HelperCancelReason
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(helper_cancel.cancel_as_helper)(
            user, str(id), helper_cancel.HelperCancelReason(reason.value)
        )
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "Ask to cancel for a no-fault reason, as the poster or the helper, while the "
            "payment is held (also after the start). The other side has 48 hours to agree "
            "(a full refund) or disagree (support decides). With no answer, the poster's "
            "request stands; the helper's goes to support. Errors: UNAUTHENTICATED, "
            "BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def request_no_fault_cancellation(
        self, info: Info, id: strawberry.ID, reason: NoFaultReason
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(no_fault.request_no_fault)(
            user, str(id), cancellation.NoFaultReason(reason.value)
        )
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "Answer the other side's no-fault request: agree (the poster gets everything "
            "back) or disagree (support decides): the helper within the poster's 48 hours "
            "(`respondBy`), the poster until support decided. "
            "Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED, "
            "NO_FAULT_REPLY_CLOSED."
        )
    )
    async def respond_to_no_fault_cancellation(
        self, info: Info, id: strawberry.ID, agree: bool
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(no_fault.respond_to_no_fault)(user, str(id), agree=agree)
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "Re-open your task to other offers after your helper cancelled or a no-fault "
            "cancel, while `canReopenTask`. The helpers whose offers were declined are told "
            "and their offers are live again. Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, "
            "BOOKING_ACTION_NOT_ALLOWED, TASK_NOT_OPEN_FOR_OFFERS."
        )
    )
    async def reopen_task(self, info: Info, booking_id: strawberry.ID) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(helper_cancel.reopen_task)(user, str(booking_id))
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the helper, on the day of the job: On my way, with an optional ETA in minutes "
            "(1 to 240). The poster is told. Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, "
            "BOOKING_ACTION_NOT_ALLOWED, INVALID_ETA."
        )
    )
    async def mark_on_my_way(
        self, info: Info, booking_id: strawberry.ID, eta_minutes: int | None = None
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.on_my_way)(user, str(booking_id), eta_minutes)
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the helper: Running late, with the new ETA in minutes from now, past the "
            "booked start. The poster may keep the booking or cancel for free. Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED, INVALID_ETA."
        )
    )
    async def report_running_late(
        self, info: Info, booking_id: strawberry.ID, eta_minutes: int
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.running_late)(user, str(booking_id), eta_minutes)
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the helper: I've arrived. Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, "
            "BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def mark_arrived(self, info: Info, booking_id: strawberry.ID) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.arrived)(user, str(booking_id))
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the poster, after Running late (`arrival.canAnswerLate`): keep the booking, "
            "or cancel for free (keep false): a full refund, nobody's fault. Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def respond_to_late_arrival(
        self, info: Info, booking_id: strawberry.ID, keep: bool
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.respond_to_late)(user, str(booking_id), keep=keep)
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the poster: did your helper come? Yes after the start; no once "
            "`arrival.noShowDueAt` passed. No cancels the booking as the helper's (a full "
            "refund, plus a EUR 5 credit from TaskBeavo when it was under 24 h before the "
            "start) once the helper's 24 hours to contest are over, or support upholds it. "
            "Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def confirm_helper_arrival(
        self, info: Info, booking_id: strawberry.ID, came: bool
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.confirm_helper_came)(user, str(booking_id), came=came)
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the helper, within 24 hours of the poster saying you didn't come "
            "(`arrival.canContestNoShow`): 'I was there'. Support decides from the booking's "
            "record; nothing is refunded or counted against you until then. Errors: "
            "UNAUTHENTICATED, BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def contest_no_show(self, info: Info, booking_id: strawberry.ID) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.contest_no_show)(user, str(booking_id))
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the arrived helper, before the booked start: ask the poster to start now. "
            "Lapses at the booked start. Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, "
            "BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def request_early_start(self, info: Info, booking_id: strawberry.ID) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.request_early_start)(user, str(booking_id))
        return await sync_to_async(BookingType.of)(booking, user)

    @strawberry.mutation(
        description=(
            "As the poster: confirm the helper's Start now (the job starts now: from then on "
            "you can't cancel, you open a dispute instead, or withdraw while that's offered) "
            "or decline it (the booked time stays). `locale` is the language the screen "
            "showed the confirmation in; it is recorded with it. Errors: UNAUTHENTICATED, "
            "BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED."
        )
    )
    async def respond_to_early_start(
        self,
        info: Info,
        booking_id: strawberry.ID,
        confirm: bool,
        locale: Locale | None = None,
    ) -> BookingType:
        user = _user(info)
        booking = await sync_to_async(arrival.respond_to_early_start)(
            user, str(booking_id), confirm=confirm, locale=locale.value if locale else None
        )
        return await sync_to_async(BookingType.of)(booking, user)


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "Every service fee price list, newest effective date first: announced ones, the "
            "one checkout charges now, then earlier ones (/fees, /fees/archive, "
            "pricelist.csv). Public."
        )
    )
    def service_fee_schedules(self) -> list[ServiceFeeScheduleType]:
        current = fee_schedule_at()
        newest_first = sorted(money.FEE_SCHEDULES, key=lambda s: s.effective_from, reverse=True)
        return [ServiceFeeScheduleType.of(schedule, current) for schedule in newest_first]

    @strawberry.field(
        description="The cancellation policy's amounts for the /fees refund table. Public."
    )
    def cancellation_amounts(self) -> CancellationAmountsType:
        return CancellationAmountsType(
            id=strawberry.ID("current"),
            grace_period_minutes=int(cancellation.GRACE_PERIOD.total_seconds() // 60),
            late_window_hours=int(cancellation.late_cancel_window().total_seconds() // 3600),
            late_pay_percent=cancellation.LATE_PAY_PERCENT,
            late_pay_min_cents=cancellation.LATE_PAY_MIN_CENTS,
            late_pay_max_cents=cancellation.LATE_PAY_MAX_CENTS,
            call_out_percent=cancellation.CALL_OUT_PERCENT,
            call_out_min_cents=cancellation.CALL_OUT_MIN_CENTS,
            call_out_max_cents=cancellation.CALL_OUT_MAX_CENTS,
            kept_fee_percent=fee_schedule_at().percent,
            helper_cancel_credit_cents=cancellation.HELPER_CANCEL_CREDIT_CENTS,
            withdrawal_period_days=cancellation.WITHDRAWAL_PERIOD_DAYS,
        )

    @strawberry.field(
        description=(
            "One of your bookings, as its poster or its helper. "
            "Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND."
        )
    )
    async def booking(self, info: Info, id: strawberry.ID) -> BookingType:
        user = _user(info)
        found = await sync_to_async(_booking_for)(user, str(id))
        if found is None:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        return found

    @strawberry.field(
        description=(
            "Your open checkout again, to pay on the same PaymentIntent after leaving the "
            "payment sheet. Poster only, while AWAITING_PAYMENT and before "
            "`checkoutExpiresAt`. Errors: UNAUTHENTICATED, BOOKING_NOT_FOUND, "
            "CHECKOUT_CLOSED, PAYMENT_UNAVAILABLE."
        )
    )
    async def checkout_session(self, info: Info, booking_id: strawberry.ID) -> CheckoutSession:
        user = _user(info)
        reopened = await sync_to_async(checkout.reopen_checkout)(user, str(booking_id))
        booking = await sync_to_async(BookingType.of)(reopened.booking, user)
        return CheckoutSession(
            id=strawberry.ID(str(reopened.booking.pk)),
            booking=booking,
            client_secret=reopened.client_secret,
            payment_pending=reopened.payment_pending,
        )


# The live events a booking's screens follow: every status change, and the card attempts
# of its checkout (the poster's only: bookings/outbox_handlers.py sends those to them).
LIVE_TOPICS = frozenset(
    {UPDATED_TOPIC, checkout.PAYMENT_ATTEMPT_FAILED_TOPIC, checkout.ACTION_REQUIRED_TOPIC}
)


@strawberry.type
class Subscription:
    @strawberry.subscription(
        description=(
            "Your booking changed: its status (FUNDS_HELD once Stripe confirms the payment), "
            "or, for the poster, a card attempt of its checkout. Yields the whole booking. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def booking_updated(
        self, info: Info, id: strawberry.ID
    ) -> AsyncGenerator[BookingType, None]:
        booking_id = str(id)
        user = getattr(info.context, "user", None)
        # The connect-time check (hard rule d); viewer_events refuses a signed-out socket.
        if (
            isinstance(user, User)
            and user.is_authenticated
            and await sync_to_async(_booking_for)(user, booking_id) is None
        ):
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")

        async def load(viewer: AbstractBaseUser, event: LiveEvent) -> BookingType | None:
            if event.aggregate_id != booking_id or not isinstance(viewer, User):
                return None
            # The per-event check (hard rule d): still this viewer's booking.
            return await sync_to_async(_booking_for)(viewer, booking_id)

        async for booking in viewer_events(info, LIVE_TOPICS, load):
            yield booking


def _booking_for(user: User, booking_id: str) -> BookingType | None:
    """The booking as its poster or helper sees it; None for anyone else or a bad ID."""
    try:
        pk = UUID(booking_id)
    except ValueError:
        return None
    booking = Booking.objects.filter(Q(poster_id=user.pk) | Q(helper_id=user.pk), pk=pk).first()
    return None if booking is None else BookingType.of(booking, user)


def _preview(booking: Booking) -> CancellationPreview | None:
    if booking.status not in (Booking.Status.FUNDS_HELD, Booking.Status.PAYMENT_REQUESTED):
        return None
    pk = strawberry.ID(str(booking.pk))
    charge = charged_cents(booking.amount_cents, booking.fee_cents)
    try:
        outcome = cancel.preview(booking)
    except NotACancellation as refused:
        return CancellationPreview(
            id=pk,
            case=None,
            charge_cents=charge,
            refund_cents=0,
            helper_compensation_cents=0,
            kept_fee_cents=0,
            credit_back_cents=0,
            refused_with=refused.error_code,
        )
    except ApiError:
        return None
    return CancellationPreview(
        id=pk,
        case=CancellationCase(outcome.case.value),
        charge_cents=outcome.charge_cents,
        refund_cents=outcome.refund_cents,
        helper_compensation_cents=outcome.helper_compensation_cents,
        kept_fee_cents=outcome.kept_fee_cents,
        credit_back_cents=cancellation.credit_back_cents(outcome, booking.credit_cents),
        refused_with=None,
    )


def _cancellation(
    booking: Booking, settlement: CancellationSettlement | None, *, is_poster: bool
) -> Cancellation | None:
    withdrawal = booking.cancel_reason == cancel.WITHDRAWAL_REASON
    if settlement is None and not (
        withdrawal and booking.status == Booking.Status.CANCEL_REQUESTED
    ):
        return None
    return Cancellation(
        id=strawberry.ID(str(booking.pk)),
        withdrawal=withdrawal,
        case=CancellationCase(settlement.case) if settlement else None,
        # The helper sees what they keep, never the refund or TaskBeavo's share: both
        # would give away the poster's credit (the helper sees the schedule fee).
        refund_cents=settlement.refund_cents if settlement and is_poster else None,
        helper_compensation_cents=settlement.helper_compensation_cents if settlement else None,
        kept_fee_cents=settlement.kept_fee_cents if settlement and is_poster else None,
        awaiting_support=settlement is None,
        confirmed=bool(settlement and settlement.confirmed_at),
        requested_at=booking.cancel_requested_at,
        requested_by=(
            BookingSide(booking.cancel_requested_by)
            if booking.cancel_requested_by in ("POSTER", "HELPER")
            else None
        ),
        poster_credit_cents=settlement.poster_credit_cents if settlement else 0,
        credit_back_cents=(
            None
            if not is_poster
            else cancellation.credit_back_cents(outcome_of(settlement), booking.credit_cents)
            if settlement
            # A withdrawal waiting for support's share: the whole credit comes back anyway.
            else booking.credit_cents
        ),
    )


def _helper_preview(booking: Booking) -> HelperCancelPreview | None:
    if booking.status not in (Booking.Status.FUNDS_HELD, Booking.Status.PAYMENT_REQUESTED):
        return None
    pk = strawberry.ID(str(booking.pk))
    try:
        outcome = helper_cancel.preview(booking)
    except NotACancellation as refused:
        return HelperCancelPreview(
            id=pk, refund_cents=0, poster_credit_cents=0, refused_with=refused.error_code
        )
    except ApiError:
        return None
    return HelperCancelPreview(
        id=pk,
        # A helper's cancel refunds everything; shown at the schedule fee, with any credit
        # the poster used (it comes back too), like the helper's view of the booking.
        refund_cents=outcome.refund_cents + booking.credit_cents,
        poster_credit_cents=outcome.poster_credit_cents,
        refused_with=None,
    )


def _helper_is_trader(booking: Booking) -> bool:
    consent = ConsentRecord.objects.filter(booking=booking).only("helper_is_trader").first()
    return bool(consent and consent.helper_is_trader)


def _user(info: Info[Any, Any]) -> User:
    return require_signed_in(info.context.user)
