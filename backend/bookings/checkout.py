"""Checkout: accepting an offer and paying for it (docs/PLAN.md, "Checkout with card
authentication (3D Secure)"; "Consumer withdrawal law turns TaskBeavo's fee into a
refundable item").

`accept_offer` (the acceptOffer mutation):

1. Consent first, before anything is locked or charged (hard rule g). Both unticked
   checkboxes must be ticked, the cancellation policy and the express request to start
   inside the 14-day withdrawal period, for the CANCELLATION version in force in the
   language the checkout showed; otherwise CONSENT_REQUIRED and nothing happens. The
   wording ticked must be the policy's pair for the offer's helper type (private or
   trader); if the helper's status changed since, CONSENT_WORDING_CHANGED.
2. The helper's payout account must be ACTIVE (HELPER_CANNOT_TAKE_PAYMENTS). The stored
   status can be out of date, so Stripe's own answer below is what counts.
3. One transaction: `state_machine.create_booking` locks the task and the offer, checks the
   offer and the service area (hard rule j) and creates the booking AWAITING_PAYMENT; the
   booking gets its TERMS and CANCELLATION versions, a `TermsAcceptance` of the
   cancellation policy and a `ConsentRecord`. A second accept waits on the task lock and
   gets TASK_ALREADY_BOOKED.
4. Outside every lock: the destination-charge PaymentIntent (hard rule b), key
   `booking:{id}:payment_intent:v1` (hard rule a). Stripe refusing it because of the
   helper's account fails the checkout (PAYMENT_FAILED, the offers reopen), queues
   `payments.connect.reconcile_account` so our copy of the account is corrected, and answers
   HELPER_CANNOT_TAKE_PAYMENTS; any other failure answers PAYMENT_UNAVAILABLE.
5. Under the booking lock the PaymentIntent ID is stored, and the client secret is returned
   only while the booking is still AWAITING_PAYMENT. If the helper withdrew in between, the
   PaymentIntent is cancelled (`settle_failed_checkout`) and the answer is
   OFFER_NOT_EDITABLE.

No money moves here: the booking becomes FUNDS_HELD only from the Stripe webhook
(`confirm_payment`, hard rule a).

`confirm_payment` (payment_intent.succeeded, inside the webhook transaction):
AWAITING_PAYMENT becomes FUNDS_HELD, the only way it does (hard rule a), and a
`booking.funds_held` outbox row tells the helper, the helpers whose offers were declined
and the poster (bookings/outbox_handlers.py, with the contract confirmation email). No
fiscal receipt here: the fee receipt is issued at release (BEAV-128). A payment that
succeeds on a checkout that already failed (offer withdrawn, checkout abandoned) is
refunded in full, fee included, and the poster is told in their language; the webhook
answers 200 instead of failing on every retry. Any later status is an out-of-order event
and changes nothing.

`record_failed_attempt` (payment_intent.payment_failed): a declined card or a failed 3D
Secure check. User decision 2026-11-04: the checkout stays open (AWAITING_PAYMENT) and
the poster tries another card on the same PaymentIntent, in the same payment sheet; the
attempt is recorded (Stripe's decline code only) and the poster's app gets a live event.
`record_action_required` (payment_intent.requires_action) records the start of a 3D
Secure or bank-app confirmation the same way.

`expire_checkout` (Beat, `bookings.checkout.expire_abandoned`): a checkout nobody paid
within `CHECKOUT_ABANDON_AFTER_MINUTES` (30) of its last card attempt, or of acceptance if
there was none. Each attempt (a decline, or the start of a 3D Secure confirmation)
restarts the 30 minutes, but `CHECKOUT_MAX_MINUTES` (60) from acceptance always ends it
(user decision 2026-11-04). Its PaymentIntent is cancelled
first, key `booking:{id}:abandon:v{n}`, then the booking becomes PAYMENT_FAILED and the
offers reopen, which lifts TASK_ALREADY_BOOKED. If Stripe says the payment succeeded or
is processing, the booking is left for the webhook. A booking whose PaymentIntent ID never
reached us (acceptOffer lost Stripe's answer) has it looked up by metadata first.

`settle_failed_checkout` (Celery, `bookings.checkout.settle_failed`): for a PAYMENT_FAILED
booking with a PaymentIntent, cancel it so it can't be paid; if Stripe says it already
succeeded, refund it in full with `booking:{id}:refund:withdrawn:v1`, the one key every
path uses, so a payment is refunded once. Idempotent; it reloads the booking every time.
`sweep_unsettled_checkouts` (Beat) runs it again for any failed checkout still unsettled
after its retries ran out (a Stripe outage, a lost broker message), so a refund owed to a
poster is never forgotten.
"""

import contextlib
import datetime as dt
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from accounts.models import Session, User
from accounts.permissions import require_signed_in
from accounts.states import require_helper_takes_bookings
from bookings import state_machine
from bookings.alerts import money_alert
from bookings.models import Booking, BookingEvent, ConsentRecord
from core.errors import ApiError, ErrorCode
from core.i18n import SUPPORTED_LOCALES
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from helpers.models import HelperProfile
from legal.checkout_consent import VARIANTS, variant_for
from legal.models import LegalDocument, TermsAcceptance
from legal.services import current_document
from offers.models import Offer
from payments.idempotency import idempotency_key
from payments.models import ConnectedAccount
from payments.stripe_client import StripeRequestError, get_gateway

logger = logging.getLogger(__name__)

S = Booking.Status

# A cancel Stripe refused while the payment was processing, or failed on Stripe's side:
# the next try takes the next key version. `data.reason` says which.
CANCEL_DEFERRED = "payment_intent_cancel_deferred"
DEFERRED_PROCESSING = "processing"
DEFERRED_STRIPE_ERROR = "stripe_error"
# How long the sweep leaves a cancel deferred while the payment processes before trying
# again (SEPA debits process for days). A Stripe-side error is retried on the next run.
CANCEL_RETRY_AFTER = dt.timedelta(hours=6)
# The expiry found the abandoned checkout paid at Stripe (its webhook is on the way).
ABANDON_FOUND_PAID = "abandon_found_paid"
# One alert per booking and reason, for problems the Beat jobs see on every run.
ALERT_SENT = "support_alert_sent"
# Stripe-side failures of one cancel before support is alerted; the retries go on.
STRIPE_ERRORS_BEFORE_ALERT = 3
# Stripe refused a cancel or refund for good (a 4xx that no retry changes): support
# takes over, alerted through Sentry, and the sweeps stop.
CANCEL_REFUSED = "payment_intent_cancel_refused"
REFUND_REFUSED = "late_payment_refund_refused"
# Stripe holds no PaymentIntent for a failed checkout that never stored one.
INTENT_NOT_FOUND = "payment_intent_not_found"
# The support-flag reason of a failed checkout the sweep stopped looking at, past its
# lookback; support settles it.
SETTLE_GIVEN_UP = "settle_given_up"
# Reasons a checkout failed without a payment.
ABANDONED_REASON = "checkout_abandoned"
# The money is held: tell the helpers and send the poster the contract confirmation.
FUNDS_HELD_TOPIC = "booking.funds_held"
# A card was declined or 3D Secure failed; the poster may try another card.
PAYMENT_ATTEMPT_FAILED_TOPIC = "booking.payment_attempt_failed"
PAYMENT_ATTEMPT_FAILED = "payment_attempt_failed"
# Support-flag reason: a paid PaymentIntent that doesn't match its booking.
PAYMENT_MISMATCH = "payment_intent_mismatch"
ACTION_REQUIRED_TOPIC = "booking.payment_action_required"
PAYMENT_ACTION_REQUIRED = "payment_action_required"
# The card attempts that restart the checkout's 30 minutes.
ATTEMPTS = (PAYMENT_ATTEMPT_FAILED, PAYMENT_ACTION_REQUIRED)
# A payment succeeded on a checkout that had already failed: refund it.
LATE_PAYMENT_TOPIC = "booking.late_payment"
# That refund was made: tell the poster.
LATE_PAYMENT_REFUNDED_TOPIC = "booking.late_payment_refunded"


@dataclass(frozen=True)
class CheckoutConsent:
    """What the poster ticked at checkout, and for which cancellation policy version."""

    cancellation_policy_accepted: bool
    early_start_requested: bool
    cancellation_version: str
    platform: str
    # The language the checkout showed the documents in; the versions are checked in it.
    language: str
    # Which tick-box pair the checkout showed: "private" or "trader" (legal.checkout_consent).
    variant: str


@dataclass(frozen=True)
class CheckoutSession:
    booking: Booking
    # For Stripe's payment sheet only; never logged or stored.
    client_secret: str


def accept_offer(
    poster: User,
    offer_id: str,
    *,
    expected_price_cents: int,
    expected_total_cents: int,
    expected_fee_schedule_version: str,
    consent: CheckoutConsent,
    slot_start: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> CheckoutSession:
    """Accept `offer_id` for `poster` and open its checkout (see the module docstring)."""
    require_signed_in(poster)
    now = now or timezone.now()
    terms, cancellation = _check_consent(consent, now)
    offer = _poster_offer(poster, offer_id)
    require_helper_takes_bookings(offer.helper)
    account = _destination(offer)
    if consent.variant != variant_for(account.helper.is_trader):
        # The helper's status changed after the screen loaded: the poster ticked the other
        # helper type's wording, which isn't what they're agreeing to now.
        raise ApiError(
            ErrorCode.CONSENT_WORDING_CHANGED,
            "The tick boxes shown were for the other helper type.",
        )
    with transaction.atomic():
        booking = state_machine.create_booking(
            poster,
            offer,
            expected_price_cents=expected_price_cents,
            expected_total_cents=expected_total_cents,
            expected_fee_schedule_version=expected_fee_schedule_version,
            slot_start=slot_start,
            now=now,
        )
        _record_consent(booking, poster, terms, cancellation, consent, account, now)
    try:
        intent = get_gateway().create_destination_charge(
            booking_id=str(booking.pk),
            amount_cents=booking.amount_cents + booking.fee_cents,
            application_fee_cents=booking.fee_cents,
            destination_account=account.stripe_account_id,
            idempotency_key=idempotency_key("booking", str(booking.pk), "payment_intent"),
        )
    except StripeRequestError as exc:
        if exc.is_connected_account_problem:
            _fail_checkout(booking, "helper_account")
            _reconcile(account)
            raise ApiError(
                ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS, "Stripe refused the helper's account."
            ) from None
        _fail_checkout(booking, "payment_intent_failed")
        raise ApiError(ErrorCode.PAYMENT_UNAVAILABLE, "Stripe refused the payment.") from None
    except Exception:
        # Never leave a checkout open that no client can pay.
        _fail_checkout(booking, "payment_intent_failed")
        raise
    return _open_checkout(booking, intent.id, getattr(intent, "client_secret", None))


def _check_consent(
    consent: CheckoutConsent, now: dt.datetime
) -> tuple[LegalDocument, LegalDocument]:
    if consent.language not in SUPPORTED_LOCALES:
        raise ApiError(ErrorCode.CONSENT_REQUIRED, "Unknown checkout language.")
    language = consent.language
    cancellation = current_document(LegalDocument.Kind.CANCELLATION, language, at=now)
    terms = current_document(LegalDocument.Kind.TERMS, language, at=now)
    if (
        not consent.cancellation_policy_accepted
        or not consent.early_start_requested
        or cancellation is None
        or terms is None
        or consent.cancellation_version != cancellation.version
        or consent.variant not in VARIANTS
        or consent.platform not in Session.Platform.values
    ):
        raise ApiError(
            ErrorCode.CONSENT_REQUIRED,
            "Accept the cancellation policy in force and ask for the early start.",
        )
    return terms, cancellation


def _poster_offer(poster: User, offer_id: str) -> Offer:
    """The offer on one of `poster`'s tasks; OFFER_NOT_FOUND for anyone else's, before
    anything about the helper is revealed. create_booking checks it again under lock."""
    try:
        pk = uuid.UUID(str(offer_id))
    except ValueError:
        raise ApiError(ErrorCode.OFFER_NOT_FOUND, "No such offer on your task.") from None
    offer = (
        Offer.objects.select_related("helper__helper_profile__connected_account")
        .filter(pk=pk, task__poster=poster)
        .first()
    )
    if offer is None:
        raise ApiError(ErrorCode.OFFER_NOT_FOUND, "No such offer on your task.")
    return offer


def _destination(offer: Offer) -> ConnectedAccount:
    """The helper's payout account, if our copy says it can take a destination charge."""
    profile = getattr(offer.helper, "helper_profile", None)
    account = getattr(profile, "connected_account", None) if profile is not None else None
    if (
        not offer.helper.is_active
        or account is None
        or account.status != ConnectedAccount.Status.ACTIVE
    ):
        raise ApiError(
            ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS, "The helper's account can't take payments."
        )
    return account


def _record_consent(
    booking: Booking,
    poster: User,
    terms: LegalDocument,
    cancellation: LegalDocument,
    consent: CheckoutConsent,
    account: ConnectedAccount,
    now: dt.datetime,
) -> None:
    """In the booking's transaction: the versions it is governed by, the acceptance of the
    cancellation policy and the early-start consent (plan: "Checkout: accept cancellation
    policy and early-start consent")."""
    booking.terms_version = terms.version
    booking.cancellation_version = cancellation.version
    booking.save(update_fields=["terms_version", "cancellation_version", "updated_at"])
    TermsAcceptance.objects.create(
        user=poster,
        document=cancellation,
        accepted_at=now,
        platform=consent.platform,
        booking=booking,
    )
    ConsentRecord.objects.create(
        booking=booking,
        express_request_at=now,
        terms_version=terms.version,
        cancellation_version=cancellation.version,
        # The withdrawal information and model form are part of the cancellation policy
        # page ("Withdrawal information" link at checkout).
        withdrawal_notice_version=cancellation.version,
        language=cancellation.language,
        helper_is_trader=account.helper.is_trader,
        consent_variant=consent.variant,
        **_trader_identity(account.helper),
        platform=consent.platform,
    )


def _trader_identity(profile: HelperProfile) -> dict[str, str]:
    """What the contract confirmation names a trader helper by, as of checkout. Nothing for
    a private helper, nor for a company whose name and OIB aren't confirmed yet."""
    if not profile.is_trader or profile.needs_business_verification:
        return {}
    # Only the registered business address, never the home address (user decision
    # 2026-11-04). A trader can't send offers without it (helpers.services), so it is set.
    return {
        "trader_name": profile.business_name,
        "trader_oib": profile.business_oib,
        "trader_address": profile.business_address,
    }


def _fail_checkout(booking: Booking, reason: str) -> None:
    """End a checkout no client can pay. A withdrawal may have ended it already."""
    with contextlib.suppress(ApiError):
        state_machine.transition(
            booking.pk, S.PAYMENT_FAILED, actor_kind=ActorKind.SYSTEM, data={"reason": reason}
        )


def _reconcile(account: ConnectedAccount) -> None:
    # Imported here: payments.tasks imports payments.connect, which imports helpers.
    from payments.tasks import reconcile_connected_account

    reconcile_connected_account.delay(account.pk)


def _open_checkout(booking: Booking, intent_id: str, client_secret: Any) -> CheckoutSession:
    """Store the PaymentIntent and hand out its client secret, only while the booking is
    still in checkout (known gap 4). A booking that failed meanwhile keeps the ID, so the
    PaymentIntent is cancelled, or refunded if it was paid after all."""
    with locked_for_update(Booking.objects, booking.pk) as locked:
        locked.payment_intent_id = intent_id
        locked.save(update_fields=["payment_intent_id", "updated_at"])
        still_open = locked.status == S.AWAITING_PAYMENT
        if not still_open:
            settle_after_commit(str(locked.pk))
    if not still_open:
        raise ApiError(ErrorCode.OFFER_NOT_EDITABLE, "The offer is no longer open.")
    if not isinstance(client_secret, str) or not client_secret:
        with transaction.atomic():
            _fail_checkout(locked, "payment_intent_failed")
            settle_after_commit(str(locked.pk))
        raise ApiError(ErrorCode.PAYMENT_UNAVAILABLE, "Stripe returned no client secret.")
    return CheckoutSession(booking=locked, client_secret=client_secret)


def confirm_payment(intent: dict[str, Any], *, stripe_event_id: str) -> Booking | None:
    """Apply a verified payment_intent.succeeded event. Webhook transaction only.

    Returns the booking it found, or None when no booking of ours has this PaymentIntent.
    A payment that doesn't match the booking (another PaymentIntent, amount, currency or
    helper account) changes nothing and is flagged for support, which a person settles at
    Stripe; the webhook answers 200 (review R1-2), since a redelivery can't fix it.
    """
    booking_id = _booking_id_of(intent)
    if booking_id is None:
        return None
    try:
        with locked_for_update(Booking.objects, booking_id) as booking:
            mismatch = _intent_mismatch(booking, intent)
            if mismatch is not None:
                state_machine.flag_for_support(
                    booking,
                    PAYMENT_MISMATCH,
                    {
                        "mismatch": mismatch,
                        "payment_intent": str(intent.get("id") or ""),
                        "stripe_event": stripe_event_id,
                    },
                )
                return booking
            if not booking.payment_intent_id:
                # The webhook beat acceptOffer to storing it.
                booking.payment_intent_id = intent["id"]
                booking.save(update_fields=["payment_intent_id", "updated_at"])
            if booking.status == S.AWAITING_PAYMENT:
                # The fee's fiscal receipt is issued at release, not here (working
                # assumption 2026-11-04, pending the tax advisor): TODO(BEAV-128).
                held = state_machine.apply_stripe_transition(
                    booking.pk,
                    S.FUNDS_HELD,
                    stripe_event_id=stripe_event_id,
                    data={"payment_intent": intent["id"]},
                )
                if held is not None:
                    enqueue(FUNDS_HELD_TOPIC, aggregate=booking, payload=_ids(booking))
            elif booking.status == S.PAYMENT_FAILED and not _late_payment_seen(booking):
                # The checkout failed first (withdrawn, abandoned): refund it in full.
                # TODO(BEAV-128, BEAV-131): no fiscal receipt may be issued for this
                # payment's fee; if one ever is, it needs a storno receipt (rule f).
                BookingEvent.objects.create(
                    booking=booking,
                    type="late_payment",
                    actor_kind=ActorKind.STRIPE_WEBHOOK,
                    data={"payment_intent": intent["id"], "stripe_event": stripe_event_id},
                )
                enqueue(LATE_PAYMENT_TOPIC, aggregate=booking, payload=_ids(booking))
                money_alert(
                    "bookings: a payment arrived after its checkout ended; it is refunded",
                    booking_id=booking.pk,
                    reason="late_payment",
                    stripe_event_type="payment_intent.succeeded",
                    stripe_event_id=stripe_event_id,
                    stripe_object_id=str(intent["id"]),
                )
            # Anything later: an out-of-order event, nothing to do.
    except Booking.DoesNotExist:
        return None
    return booking


def _late_payment_seen(booking: Booking) -> bool:
    """A redelivery under another event ID, before or after the refund was made."""
    return bool(booking.late_payment_refund_id) or (
        BookingEvent.objects.filter(booking=booking, type="late_payment").exists()
    )


def record_failed_attempt(intent: dict[str, Any], *, stripe_event_id: str) -> Booking | None:
    """Apply a verified payment_intent.payment_failed event. Webhook transaction only.

    The checkout stays open so the poster can try another card (module docstring). Returns
    the booking, or None when no booking of ours has this PaymentIntent. An attempt on a
    checkout that is no longer open, or on another PaymentIntent, changes nothing.
    """
    code = _decline_code(intent)
    return _record_attempt(
        intent,
        stripe_event_id=stripe_event_id,
        event_type=PAYMENT_ATTEMPT_FAILED,
        topic=PAYMENT_ATTEMPT_FAILED_TOPIC,
        data={"decline_code": code},
    )


def record_action_required(intent: dict[str, Any], *, stripe_event_id: str) -> Booking | None:
    """Apply a verified payment_intent.requires_action event (a 3D Secure or bank-app
    confirmation started). Webhook transaction only; same rules as `record_failed_attempt`."""
    return _record_attempt(
        intent,
        stripe_event_id=stripe_event_id,
        event_type=PAYMENT_ACTION_REQUIRED,
        topic=ACTION_REQUIRED_TOPIC,
        data={},
    )


def _record_attempt(
    intent: dict[str, Any],
    *,
    stripe_event_id: str,
    event_type: str,
    topic: str,
    data: dict[str, str],
) -> Booking | None:
    booking_id = _booking_id_of(intent)
    if booking_id is None:
        return None
    try:
        with locked_for_update(Booking.objects, booking_id) as booking:
            ours = not booking.payment_intent_id or booking.payment_intent_id == intent.get("id")
            if booking.status != S.AWAITING_PAYMENT or not ours:
                return booking
            if not booking.payment_intent_id:
                booking.payment_intent_id = intent["id"]
                booking.save(update_fields=["payment_intent_id", "updated_at"])
            BookingEvent.objects.create(
                booking=booking,
                type=event_type,
                actor_kind=ActorKind.STRIPE_WEBHOOK,
                data={"payment_intent": intent["id"], **data, "stripe_event": stripe_event_id},
            )
            enqueue(topic, aggregate=booking, payload={**_ids(booking), **data})
    except Booking.DoesNotExist:
        return None
    return booking


_CODE = re.compile(r"[a-z0-9_]{1,64}")


def _decline_code(intent: dict[str, Any]) -> str:
    """Stripe's machine-readable reason, never its message (which can quote what the card
    holder typed): the decline code, else the error code, as an enum key."""
    error = intent.get("last_payment_error") or {}
    for value in (error.get("decline_code"), error.get("code")):
        if isinstance(value, str) and _CODE.fullmatch(value):
            return value
    return "unknown"


def _booking_id_of(intent: dict[str, Any]) -> uuid.UUID | None:
    metadata = intent.get("metadata") or {}
    try:
        return uuid.UUID(str(metadata.get("booking_id")))
    except ValueError:
        pass
    found = (
        Booking.objects.filter(payment_intent_id=intent.get("id") or "")
        .values_list("pk", flat=True)
        .first()
    )
    return found


def _intent_mismatch(booking: Booking, intent: dict[str, Any]) -> str | None:
    """How a paid PaymentIntent differs from its booking, as an enum key, or None."""
    if booking.payment_intent_id and booking.payment_intent_id != intent.get("id"):
        return "payment_intent"
    if (
        intent.get("amount") != booking.amount_cents + booking.fee_cents
        or intent.get("currency") != "eur"
    ):
        return "amount"
    destination = (
        ConnectedAccount.objects.filter(helper__user_id=booking.helper_id)
        .values_list("stripe_account_id", flat=True)
        .first()
    )
    transfer_data = intent.get("transfer_data") or {}
    if (
        destination is None
        or transfer_data.get("destination") != destination
        or intent.get("on_behalf_of") != destination
    ):
        return "destination"
    return None


def settle_failed_checkout(booking_id: str) -> None:
    """Cancel the PaymentIntent of a failed checkout, or refund it in full if it was paid.

    Safe to run any number of times, from any path: the offer.withdrawn handler, a late
    payment_intent.succeeded and acceptOffer finding the checkout ended. Stripe calls happen
    outside every row lock; the result is recorded under the booking lock afterwards.
    """
    booking = Booking.objects.select_related("offer").filter(pk=booking_id).first()
    if booking is None or booking.status != S.PAYMENT_FAILED or booking.late_payment_refund_id:
        return
    if not booking.payment_intent_id:
        booking = _adopt_orphan(booking)
        if booking is None:
            return
    if BookingEvent.objects.filter(booking=booking, type="late_payment").exists():
        # Stripe told us it was paid: nothing left to cancel.
        _refund(booking)
        return
    try:
        get_gateway().cancel_payment_intent(
            booking_id=str(booking.pk),
            payment_intent=booking.payment_intent_id,
            idempotency_key=_cancel_key(booking),
        )
    except StripeRequestError as exc:
        status = exc.payment_intent_status
        if status == "succeeded":
            _refund(booking)
            return
        if status == "processing":
            # Still being paid: payment_intent.succeeded (refund) or
            # payment_intent.payment_failed (nothing to do) follows. Stripe replays a
            # key's first answer, errors included, so the next try needs a new key.
            _record_cancel_deferred(booking, DEFERRED_PROCESSING)
            return
        if status != "canceled":
            if _is_transient(exc):
                # Stripe saved this failure under the key too: retry under the next one.
                # A cancel that did go through answers `canceled` then.
                _record_cancel_deferred(booking, DEFERRED_STRIPE_ERROR)
                raise
            # A 4xx no retry changes: support takes over (BEAV-109 review r2).
            _record_refused(booking, CANCEL_REFUSED, exc)
            return
        # Already cancelled: recorded below, like our own cancel, so the sweep stops.
    _record_cancelled(booking)


def _is_transient(exc: StripeRequestError) -> bool:
    """Stripe failed on its side or couldn't be reached (no HTTP status): worth a retry."""
    return exc.http_status is None or exc.http_status >= 500


def _record_refused(booking: Booking, event_type: str, exc: StripeRequestError) -> None:
    """Stripe refused a cancel or refund of `booking` for good: a BookingEvent the sweeps
    skip, and a Sentry alert with IDs and Stripe's code only."""
    BookingEvent.objects.create(
        booking=booking,
        type=event_type,
        actor_kind=ActorKind.SYSTEM,
        data={"payment_intent": booking.payment_intent_id, "stripe_code": _code_key(exc.code)},
    )
    money_alert(
        "bookings: Stripe refused to settle a failed checkout; support must settle it",
        booking_id=booking.pk,
        reason=event_type,
        stripe_object_id=booking.payment_intent_id,
        stripe_error_code=_code_key(exc.code),
    )


def _code_key(code: str) -> str:
    return code if _CODE.fullmatch(code or "") else "unknown"


# How long after a checkout failed its PaymentIntent is looked up by metadata: Stripe's
# search index can lag behind by about a minute.
ORPHAN_SEARCH_AFTER = dt.timedelta(minutes=5)


def _adopt_orphan(booking: Booking, now: dt.datetime | None = None) -> Booking | None:
    """A failed checkout that never stored its PaymentIntent ID: acceptOffer's request to
    Stripe failed or timed out after Stripe had created it (BEAV-109 "L2"). Look it up by
    metadata and store it, so it is cancelled or refunded like any other. Returns the
    booking with the ID, or None when Stripe holds nothing to settle (recorded once)."""
    now = now or timezone.now()
    events = BookingEvent.objects.filter(booking=booking)
    if booking.status_changed_at > now - ORPHAN_SEARCH_AFTER or (
        events.filter(type__in=[INTENT_NOT_FOUND, "payment_intent_cancelled"]).exists()
    ):
        return None
    intent = _live_intent(booking)
    with locked_for_update(Booking.objects, booking.pk) as locked:
        if intent is None:
            BookingEvent.objects.create(
                booking=locked, type=INTENT_NOT_FOUND, actor_kind=ActorKind.SYSTEM, data={}
            )
            return None
        if not locked.payment_intent_id:
            locked.payment_intent_id = intent
            locked.save(update_fields=["payment_intent_id", "updated_at"])
    return Booking.objects.select_related("offer").get(pk=booking.pk)


def _live_intent(booking: Booking) -> str | None:
    """The booking's PaymentIntent at Stripe that still needs cancelling or refunding.
    One booking has one (its key is `booking:{id}:payment_intent:v1`); a cancelled one
    needs nothing."""
    found = get_gateway().find_booking_payment_intents(booking_id=str(booking.pk))
    live = [intent["id"] for intent in found if intent["status"] != "canceled"]
    if len(live) > 1:
        _alert_once(
            booking, "several_payment_intents", "bookings: several PaymentIntents for one booking"
        )
    return live[0] if live else None


def _cancel_key(booking: Booking) -> str:
    """`offer:{id}:withdraw:v{n}` for the checkout the offer's withdrawal ended (a withdrawn
    offer can't be accepted again, so that is one booking); a booking-scoped key for every
    other failed checkout, since one offer can be reopened and accepted again. `n` grows
    with each cancel Stripe deferred while the payment was processing."""
    events = BookingEvent.objects.filter(booking=booking)
    version = events.filter(type=CANCEL_DEFERRED).count() + 1
    if events.filter(type="payment_failed", data__reason=state_machine.WITHDRAWN_REASON).exists():
        return idempotency_key("offer", str(booking.offer_id), "withdraw", version=version)
    return idempotency_key("booking", str(booking.pk), "checkout_cancel", version=version)


def _record_cancel_deferred(booking: Booking, reason: str) -> None:
    BookingEvent.objects.create(
        booking=booking,
        type=CANCEL_DEFERRED,
        actor_kind=ActorKind.SYSTEM,
        data={"payment_intent": booking.payment_intent_id, "reason": reason},
    )
    if reason != DEFERRED_STRIPE_ERROR:
        return
    failures = BookingEvent.objects.filter(
        booking=booking, type=CANCEL_DEFERRED, data__reason=DEFERRED_STRIPE_ERROR
    ).count()
    logger.warning(
        "bookings: Stripe failed to cancel a checkout's PaymentIntent; retrying",
        extra={"booking.id": str(booking.pk), "stripe.object_id": booking.payment_intent_id},
    )
    if failures >= STRIPE_ERRORS_BEFORE_ALERT:
        # The retries' backoff has run a while: a person should look (BEAV-109 review r2).
        _alert_once(
            booking,
            "stripe_unavailable",
            "bookings: Stripe keeps failing to cancel a checkout's PaymentIntent",
        )


def _refund(booking: Booking) -> None:
    try:
        refund = get_gateway().create_full_refund(
            booking_id=str(booking.pk),
            payment_intent=booking.payment_intent_id,
            idempotency_key=idempotency_key("booking", str(booking.pk), "refund", "withdrawn"),
        )
    except StripeRequestError as exc:
        if _is_transient(exc):
            raise
        # A 4xx no retry changes: support refunds the poster by hand.
        _record_refused(booking, REFUND_REFUSED, exc)
        return
    with locked_for_update(Booking.objects, booking.pk) as locked:
        if locked.late_payment_refund_id:
            return
        locked.late_payment_refund_id = refund.id
        locked.save(update_fields=["late_payment_refund_id", "updated_at"])
        BookingEvent.objects.create(
            booking=locked,
            type="late_payment_refunded",
            actor_kind=ActorKind.SYSTEM,
            data={"payment_intent": locked.payment_intent_id, "refund": refund.id},
        )
        enqueue(LATE_PAYMENT_REFUNDED_TOPIC, aggregate=locked, payload=_ids(locked))


def _record_cancelled(booking: Booking) -> None:
    with locked_for_update(Booking.objects, booking.pk) as locked:
        if BookingEvent.objects.filter(booking=locked, type="payment_intent_cancelled").exists():
            return
        BookingEvent.objects.create(
            booking=locked,
            type="payment_intent_cancelled",
            actor_kind=ActorKind.SYSTEM,
            data={"payment_intent": locked.payment_intent_id},
        )


def settle_after_commit(booking_id: str) -> None:
    """Queue `settle_failed_checkout` once the current transaction commits."""
    # Imported here: bookings.tasks imports this module.
    from bookings.tasks import settle_failed_checkout_task

    transaction.on_commit(lambda: settle_failed_checkout_task.delay(booking_id))


# How long the settle task's own retries get before the sweep steps in, and how far back
# it looks (Stripe refunds card payments for months; older ones go to support).
SETTLE_GRACE = dt.timedelta(minutes=30)
SETTLE_LOOKBACK = dt.timedelta(days=30)


def sweep_unsettled_checkouts(now: dt.datetime | None = None, limit: int = 100) -> int:
    """Queue `settle_failed_checkout` again for failed checkouts that still owe a refund
    (a late payment with no refund yet) or a cancel (no cancel recorded). Returns how many
    were queued. Beat task `bookings.checkout.sweep_unsettled`."""
    # Imported here: bookings.tasks imports this module.
    from bookings.tasks import settle_failed_checkout_task

    now = now or timezone.now()
    events = BookingEvent.objects.filter(booking=OuterRef("pk"))
    unsettled = Booking.objects.filter(status=S.PAYMENT_FAILED, late_payment_refund_id="").exclude(
        payment_intent_id=""
    )
    unsettled = unsettled.exclude(Exists(events.filter(type__in=[CANCEL_REFUSED, REFUND_REFUSED])))
    late_payments = events.filter(type="late_payment")
    # Refunds owed first, queried apart, so cancels never crowd them out of the batch. A
    # refund is owed from when the late payment arrived, however long after the failure.
    refunds = list(
        unsettled.filter(
            Exists(
                late_payments.filter(
                    created_at__lt=now - SETTLE_GRACE, created_at__gte=now - SETTLE_LOOKBACK
                )
            )
        )
        .order_by("status_changed_at")
        .values_list("pk", flat=True)[:limit]
    )
    cancels = list(
        unsettled.filter(
            status_changed_at__lt=now - SETTLE_GRACE,
            status_changed_at__gte=now - SETTLE_LOOKBACK,
        )
        .exclude(Exists(late_payments))
        .exclude(Exists(events.filter(type="payment_intent_cancelled")))
        # Only a payment still processing waits out the backoff; a cancel that failed on
        # Stripe's side is tried again now, under the next key (BEAV-109 review r2).
        .exclude(
            Exists(
                events.filter(
                    type=CANCEL_DEFERRED,
                    created_at__gte=now - CANCEL_RETRY_AFTER,
                ).exclude(data__reason=DEFERRED_STRIPE_ERROR)
            )
        )
        .order_by("status_changed_at")
        .values_list("pk", flat=True)[: limit - len(refunds)]
    )
    # Failed checkouts that never stored a PaymentIntent ID: Stripe may hold one anyway.
    orphans = list(
        Booking.objects.filter(
            status=S.PAYMENT_FAILED,
            payment_intent_id="",
            late_payment_refund_id="",
            status_changed_at__lt=now - SETTLE_GRACE,
            status_changed_at__gte=now - SETTLE_LOOKBACK,
        )
        .exclude(Exists(events.filter(type__in=[INTENT_NOT_FOUND, "payment_intent_cancelled"])))
        .order_by("status_changed_at")
        .values_list("pk", flat=True)[: max(limit - len(refunds) - len(cancels), 0)]
    )
    if refunds:
        logger.warning(
            "bookings: late-payment refunds still owed after the grace period",
            extra={"booking.count": len(refunds)},
        )
    _give_up_past_lookback(unsettled, late_payments, events, now, limit)
    pending = refunds + cancels + orphans
    for booking_id in pending:
        settle_failed_checkout_task.delay(str(booking_id))
    return len(pending)


def _give_up_past_lookback(
    unsettled: QuerySet[Booking],
    late_payments: QuerySet[BookingEvent],
    events: QuerySet[BookingEvent],
    now: dt.datetime,
    limit: int,
) -> None:
    """Failed checkouts still owing a refund or a cancel past `SETTLE_LOOKBACK`: the sweep
    stops there, so support is alerted, once per booking (BEAV-109 review r2)."""
    cutoff = now - SETTLE_LOOKBACK
    flagged = events.filter(type=state_machine.SUPPORT_FLAG, data__reason=SETTLE_GIVEN_UP)
    stale = (
        unsettled.exclude(Exists(flagged))
        .filter(
            Exists(late_payments.filter(created_at__lt=cutoff))
            | (
                Q(status_changed_at__lt=cutoff)
                & ~Exists(late_payments)
                & ~Exists(events.filter(type="payment_intent_cancelled"))
            )
        )
        .order_by("status_changed_at")[:limit]
    )
    for booking in stale:
        with transaction.atomic():
            state_machine.flag_for_support(
                booking, SETTLE_GIVEN_UP, {"payment_intent": booking.payment_intent_id}
            )


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}


# The 30-minute checkout (plan: "Ana leaves checkout for 30 minutes").


def expire_abandoned_checkouts(now: dt.datetime | None = None, limit: int = 100) -> int:
    """Queue `expire_checkout` for checkouts open longer than CHECKOUT_ABANDON_AFTER_MINUTES.
    Returns how many were queued. Beat task `bookings.checkout.expire_abandoned`."""
    # Imported here: bookings.tasks imports this module.
    from bookings.tasks import expire_checkout_task

    now = now or timezone.now()
    events = BookingEvent.objects.filter(booking=OuterRef("pk"))
    cutoff = _abandon_cutoff(now)
    last_attempt = events.filter(type__in=ATTEMPTS).order_by("-created_at").values("created_at")
    due = list(
        Booking.objects.filter(status=S.AWAITING_PAYMENT)
        .annotate(last_attempt=Subquery(last_attempt[:1]))
        .filter(
            Q(status_changed_at__lt=_cap_cutoff(now))
            | Q(status_changed_at__lt=cutoff)
            & (Q(last_attempt__isnull=True) | Q(last_attempt__lt=cutoff))
        )
        .exclude(Exists(events.filter(type=CANCEL_REFUSED)))
        .exclude(
            Exists(
                events.filter(
                    type=CANCEL_DEFERRED,
                    data__reason=DEFERRED_PROCESSING,
                    created_at__gte=now - CANCEL_RETRY_AFTER,
                )
            )
        )
        .order_by("status_changed_at")
        .values_list("pk", flat=True)[:limit]
    )
    for booking_id in due:
        expire_checkout_task.delay(str(booking_id))
    return len(due)


def _abandon_cutoff(now: dt.datetime) -> dt.datetime:
    return now - dt.timedelta(minutes=settings.CHECKOUT_ABANDON_AFTER_MINUTES)


def _cap_cutoff(now: dt.datetime) -> dt.datetime:
    return now - dt.timedelta(minutes=settings.CHECKOUT_MAX_MINUTES)


def checkout_expires_at(booking: Booking) -> dt.datetime:
    """When the open checkout of `booking` ends: 30 minutes after its last card attempt
    (acceptance counts as the first), never later than 60 minutes after acceptance."""
    attempt = latest_attempt(booking)
    latest = booking.status_changed_at
    if attempt is not None:
        latest = max(latest, attempt.created_at)
    return min(
        latest + dt.timedelta(minutes=settings.CHECKOUT_ABANDON_AFTER_MINUTES),
        booking.status_changed_at + dt.timedelta(minutes=settings.CHECKOUT_MAX_MINUTES),
    )


def latest_attempt(booking: Booking) -> BookingEvent | None:
    """The last card attempt of the checkout: a decline or the start of a 3D Secure check."""
    return (
        BookingEvent.objects.filter(booking=booking, type__in=ATTEMPTS)
        .order_by("-created_at", "-pk")
        .first()
    )


def _is_abandoned(booking: Booking, now: dt.datetime) -> bool:
    """No card attempt for 30 minutes (acceptance counts as the first), or 60 minutes
    since acceptance whatever happened."""
    return checkout_expires_at(booking) < now


# Stripe has the payment: it succeeded or is still being confirmed (a bank transfer, say),
# and the webhook will say which. The poster waits instead of paying again.
_PAYMENT_WITH_STRIPE = frozenset({"succeeded", "processing"})


@dataclass(frozen=True)
class ReopenedCheckout:
    booking: Booking
    # For Stripe's payment sheet only; never logged or stored.
    client_secret: str
    payment_pending: bool


def reopen_checkout(
    poster: User, booking_id: str, now: dt.datetime | None = None
) -> ReopenedCheckout:
    """The open checkout of `poster`'s booking again, for a poster who left the payment
    sheet (BEAV-111). Only the poster, only while the booking is AWAITING_PAYMENT and the
    checkout hasn't expired (CHECKOUT_CLOSED). A read: Stripe returns the PaymentIntent's
    client secret, so the poster retries on the same PaymentIntent, never a new one."""
    require_signed_in(poster)
    now = now or timezone.now()
    try:
        pk = uuid.UUID(str(booking_id))
    except ValueError:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    booking = Booking.objects.filter(pk=pk, poster=poster).first()
    if booking is None:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
    if booking.status != S.AWAITING_PAYMENT or _is_abandoned(booking, now):
        raise ApiError(ErrorCode.CHECKOUT_CLOSED, "The checkout is no longer open.")
    if not booking.payment_intent_id:
        # acceptOffer lost Stripe's answer; the expiry finds and cancels the PaymentIntent.
        raise ApiError(ErrorCode.PAYMENT_UNAVAILABLE, "The checkout has no payment.")
    try:
        intent = get_gateway().retrieve_payment_intent(
            booking_id=str(booking.pk), payment_intent=booking.payment_intent_id
        )
    except StripeRequestError:
        raise ApiError(ErrorCode.PAYMENT_UNAVAILABLE, "Stripe didn't answer.") from None
    status = getattr(intent, "status", "")
    secret = getattr(intent, "client_secret", None)
    if status == "canceled":
        raise ApiError(ErrorCode.CHECKOUT_CLOSED, "The checkout is no longer open.")
    if not isinstance(secret, str) or not secret:
        raise ApiError(ErrorCode.PAYMENT_UNAVAILABLE, "Stripe returned no client secret.")
    return ReopenedCheckout(
        booking=booking, client_secret=secret, payment_pending=status in _PAYMENT_WITH_STRIPE
    )


def failure_reason(booking: Booking) -> str | None:
    """Why a failed checkout ended, as an enum key, for the poster's screen."""
    if booking.status != S.PAYMENT_FAILED:
        return None
    event = (
        BookingEvent.objects.filter(booking=booking, type="payment_failed")
        .order_by("-created_at", "-pk")
        .first()
    )
    reason = (event.data or {}).get("reason") if event else None
    if reason == ABANDONED_REASON:
        return "CHECKOUT_EXPIRED"
    if reason == state_machine.WITHDRAWN_REASON:
        return "OFFER_WITHDRAWN"
    return "PAYMENT_NOT_STARTED"


def expire_checkout(booking_id: str, now: dt.datetime | None = None) -> Booking | None:
    """End a checkout nobody paid in time: cancel its PaymentIntent, then PAYMENT_FAILED.

    Cancelling first means a payment confirmed at the last moment is kept (Stripe refuses
    the cancel, the webhook confirms it) instead of being charged and refunded. The Stripe
    calls happen outside every lock; the booking is moved under its lock afterwards, only
    if it is still in checkout. Safe to run any number of times. Returns the booking if
    this call ended its checkout.
    """
    now = now or timezone.now()
    booking = Booking.objects.filter(pk=booking_id, status=S.AWAITING_PAYMENT).first()
    if booking is None or not _is_abandoned(booking, now):
        return None
    intent = booking.payment_intent_id or _live_intent(booking)
    if intent and not _cancel_abandoned(booking, intent):
        return None
    with transaction.atomic():
        locked = Booking.objects.select_for_update().filter(pk=booking.pk).first()
        if locked is None or locked.status != S.AWAITING_PAYMENT:
            # The webhook confirmed the payment meanwhile.
            return None
        if intent and not locked.payment_intent_id:
            locked.payment_intent_id = intent
            locked.save(update_fields=["payment_intent_id", "updated_at"])
        ended = state_machine.transition(
            locked.pk,
            S.PAYMENT_FAILED,
            actor_kind=ActorKind.SYSTEM,
            data={"reason": ABANDONED_REASON},
            now=now,
        )
        BookingEvent.objects.create(
            booking=ended,
            type="payment_intent_cancelled" if intent else INTENT_NOT_FOUND,
            actor_kind=ActorKind.SYSTEM,
            data={"payment_intent": intent} if intent else {},
        )
    return ended


def _cancel_abandoned(booking: Booking, intent: str) -> bool:
    """Cancel the abandoned checkout's PaymentIntent. True when it can't be paid any more;
    False when the checkout must stay as it is for now (paid, processing, or Stripe failed
    or refused; the last two are alerted)."""
    deferred = BookingEvent.objects.filter(booking=booking, type=CANCEL_DEFERRED).count()
    try:
        get_gateway().cancel_payment_intent(
            booking_id=str(booking.pk),
            payment_intent=intent,
            idempotency_key=idempotency_key(
                "booking", str(booking.pk), "abandon", version=deferred + 1
            ),
        )
    except StripeRequestError as exc:
        status = exc.payment_intent_status
        if status == "succeeded":
            # payment_intent.succeeded is on its way and holds the funds. Seen again on a
            # later run, that webhook isn't landing: a person must look.
            seen = BookingEvent.objects.filter(booking=booking, type=ABANDON_FOUND_PAID)
            if seen.exists():
                _alert_once(
                    booking,
                    "abandoned_checkout_paid",
                    "bookings: a checkout Stripe says is paid never held its funds",
                )
            else:
                BookingEvent.objects.create(
                    booking=booking,
                    type=ABANDON_FOUND_PAID,
                    actor_kind=ActorKind.SYSTEM,
                    data={"payment_intent": intent},
                )
            return False
        if status == "processing":
            _record_cancel_deferred(_with_intent(booking, intent), DEFERRED_PROCESSING)
            return False
        if status == "canceled":
            return True
        if _is_transient(exc):
            # Retried on the next run, under the next key.
            _record_cancel_deferred(_with_intent(booking, intent), DEFERRED_STRIPE_ERROR)
            return False
        _record_refused(_with_intent(booking, intent), CANCEL_REFUSED, exc)
        return False
    return True


def _with_intent(booking: Booking, intent: str) -> Booking:
    """`booking` carrying the PaymentIntent found at Stripe, for the event data."""
    booking.payment_intent_id = booking.payment_intent_id or intent
    return booking


def _alert_once(booking: Booking, reason: str, message: str) -> None:
    """A money alert the Beat jobs would otherwise repeat every run: sent once per booking
    and reason, decided under the booking's lock so overlapping runs can't both skip it."""
    with transaction.atomic():
        Booking.objects.select_for_update().filter(pk=booking.pk).first()
        sent = BookingEvent.objects.filter(booking=booking, type=ALERT_SENT, data__reason=reason)
        if sent.exists():
            return
        BookingEvent.objects.create(
            booking=booking, type=ALERT_SENT, actor_kind=ActorKind.SYSTEM, data={"reason": reason}
        )
        money_alert(
            message,
            booking_id=booking.pk,
            reason=reason,
            stripe_object_id=booking.payment_intent_id,
        )
