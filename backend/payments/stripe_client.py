"""The only module that talks to Stripe (hard rules a and b).

`StripeGateway` wraps the SDK's `StripeClient` and offers one method per money action
TaskBeavo takes, each shaped so the wrong pattern can't be written:

- Every method that creates something takes keyword-only idempotency keys (one per
  Stripe request), checked to come from `payments.idempotency.idempotency_key()`
  (`booking:42:release:v1`). A missing key is a TypeError and a random one a ValueError,
  both before any request is sent.
- `create_destination_charge` always charges on behalf of the helper's connected
  account, with the fee as `application_fee_amount`, so TaskBeavo is never the merchant
  of record. It never sends `payment_method_types` (this account's API version rejects
  it); payment methods come from the Dashboard via `automatic_payment_methods`.
- `cancel_payment_intent` cancels a checkout's PaymentIntent before it is paid (the offer
  was withdrawn, or the checkout ended), so the poster can't pay it any more.
- `create_full_refund` always reverses the transfer and refunds the fee, so a refund is
  never paid from TaskBeavo's balance.
- A cancellation's settlement (bookings/settlement.py) moves exact amounts from
  `bookings/cancellation.py` in three steps, each needing the previous step's ID:
  `refund_application_fee` (TaskBeavo → the helper's account), `reverse_transfer`
  (helper → TaskBeavo) and `refund_poster` (TaskBeavo → the poster, with Stripe's own
  proportional reversal and fee refund off). `SettlementSplit` refuses amounts that
  would misroute money. Each step sets `metadata.booking_id` and `metadata.action`, and
  `find_fee_refund`, `find_transfer_reversal` and `find_poster_refund` look a step up by
  them before it is sent again under a new key (.reviews/BEAV-106-partial-refund-handoff.md,
  R3-1). `retrieve_charge_parts` reads the charge's transfer and application fee IDs.
- `create_payout` pays out from the helper's own account (manual payouts).
- `create_express_account` opens a helper's Express account with manual payouts, and
  `create_onboarding_link` sends them to Stripe's hosted onboarding (identity, IBAN).
  `retrieve_account` reads an account back, a GET that takes no idempotency key.
- `find_booking_payment_intents` searches Stripe for a booking's PaymentIntents by
  metadata, for one whose ID never reached us (the 30-minute checkout expiry).

Amounts are integer cents in EUR. Callers compute them (`bookings/cancellation.py` for
refunds); the gateway only refuses impossible values. Calls happen outside database
transactions: make the call, then store the returned ID.

A new Stripe call is a new method here that goes through `_post`.
tests/test_stripe_client.py then requires a sample call for it, which checks it sends its
idempotency key. Nothing else in the backend imports `stripe`; a test enforces that too.

A request Stripe refuses raises `StripeRequestError`, carrying Stripe's error code and
parameter (never its message, which can quote what the customer typed), and for a
PaymentIntent in the wrong state its current status. Callers branch on those, so nothing
outside this module needs the SDK's exception classes.

Each call is a span named `stripe.<resource>.<action>` carrying IDs and amounts only.
"""

import functools
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

import sentry_sdk
import stripe
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from opentelemetry import trace

from payments.idempotency import check_idempotency_key

tracer = trace.get_tracer(__name__)

CURRENCY = "eur"
# Safe to retry: every request carries an idempotency key.
MAX_NETWORK_RETRIES = 2
# Stripe's default replay window for webhook signatures, in seconds.
WEBHOOK_TOLERANCE_SECONDS = 300

_CONNECTED_ACCOUNT = re.compile(r"acct_[A-Za-z0-9]+")
_PAYMENT_INTENT = re.compile(r"pi_[A-Za-z0-9]+")
_APPLICATION_FEE = re.compile(r"fee_[A-Za-z0-9]+")
_TRANSFER = re.compile(r"tr_[A-Za-z0-9]+")
_FEE_REFUND = re.compile(r"fr_[A-Za-z0-9]+")
_TRANSFER_REVERSAL = re.compile(r"trr_[A-Za-z0-9]+")

# `metadata.action` of each settlement step, which the lookups match on.
FEE_REFUND_ACTION = "fee_refund"
TRANSFER_REVERSAL_ACTION = "transfer_reversal"
POSTER_REFUND_ACTION = "partial_refund"

T = TypeVar("T", bound=stripe.StripeObject)


class InvalidWebhookSignatureError(Exception):
    """The webhook body is not signed by any configured endpoint secret."""


# Stripe error codes and parameters that mean the helper's connected account can't take
# a destination charge (capabilities inactive, requirements due, account closed): our
# stored ACTIVE status was out of date (BEAV-95 hand-off).
_ACCOUNT_ERROR_CODES = frozenset(
    {
        "account_invalid",
        "account_closed",
        "insufficient_capabilities_for_transfer",
        "transfers_not_allowed",
    }
)
_ACCOUNT_ERROR_PARAMS = frozenset({"on_behalf_of", "transfer_data[destination]"})


class StripeRequestError(Exception):
    """Stripe refused a request, or it couldn't be sent.

    `code` and `param` are Stripe's (empty for a network error); `payment_intent_status`
    is the PaymentIntent's status when Stripe returned it with the error (a cancel of a
    PaymentIntent that already succeeded). The message is the exception class name only.
    """

    def __init__(
        self,
        operation: str,
        *,
        code: str = "",
        param: str = "",
        http_status: int | None = None,
        payment_intent_status: str = "",
    ) -> None:
        super().__init__(f"Stripe refused {operation} ({code or 'no code'}).")
        self.code = code
        self.param = param
        self.http_status = http_status
        self.payment_intent_status = payment_intent_status

    @property
    def is_connected_account_problem(self) -> bool:
        """The destination account can't take the charge; reconcile it from Stripe."""
        return self.code in _ACCOUNT_ERROR_CODES or self.param in _ACCOUNT_ERROR_PARAMS


@dataclass(frozen=True, kw_only=True)
class SettlementSplit:
    """Where a cancelled destination charge's money goes, from `bookings/cancellation.py`.
    The gateway checks these amounts; it never computes them.

    The poster paid A = P + F; the helper's account holds P and TaskBeavo F. For the
    helper's compensation C and the fee kept K (12% of C):

        refund_cents            R = A - C - K            (back to the poster)
        transfer_reversal_cents X = R + max(0, K - F)    (from the helper)
        fee_refund_cents        Y = max(0, F - K)        (fee TaskBeavo gives up, to the helper)

    so the helper ends with C and TaskBeavo with K. When a TaskBeavo credit lowered F
    below K (BEAV-123), the reversal also brings back the part of K the fee didn't cover.
    A full refund is C = K = 0.
    """

    charge_cents: int
    application_fee_cents: int
    helper_compensation_cents: int
    fee_kept_cents: int
    refund_cents: int
    transfer_reversal_cents: int
    fee_refund_cents: int

    def __post_init__(self) -> None:
        for name in ("charge_cents", "transfer_reversal_cents"):
            _check_cents(getattr(self, name), name)
        # The fee and its refund may be 0 when the poster's TaskBeavo credit covered the
        # fee (bookings/credit.py, BEAV-123): there is then no fee refund step. The refund
        # may be 0 when K takes all that is left of the charge: no refund step, and
        # transfer.reversed confirms the settlement (user decision 2026-11-07).
        for name in (
            "refund_cents",
            "application_fee_cents",
            "fee_refund_cents",
            "helper_compensation_cents",
            "fee_kept_cents",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer number of cents.")
        if self.application_fee_cents >= self.charge_cents:
            raise ValueError("The application fee must be less than the charge.")
        if self.helper_compensation_cents > self.charge_cents - self.application_fee_cents:
            raise ValueError("The compensation can't be more than the task price.")
        # K itself comes from bookings/cancellation.py (`Outcome` checks it); here only
        # the sums that keep the money where hard rule b wants it.
        if self.refund_cents + self.helper_compensation_cents + self.fee_kept_cents != (
            self.charge_cents
        ):
            raise ValueError("Refund, compensation and kept fee must add up to the charge.")
        beyond_fee = max(0, self.fee_kept_cents - self.application_fee_cents)
        if self.transfer_reversal_cents != self.refund_cents + beyond_fee:
            # Less would pay part of the refund from TaskBeavo's balance (hard rule b);
            # more would move the helper's compensation to TaskBeavo.
            raise ValueError("The transfer reversal must be the refund plus K beyond the fee.")
        if self.fee_refund_cents != max(0, self.application_fee_cents - self.fee_kept_cents):
            raise ValueError("The fee refund must be the fee charged minus the fee kept.")


@dataclass(frozen=True)
class ChargeParts:
    """The IDs a settlement needs from a destination charge."""

    charge: str
    transfer: str
    # "" when the charge had no application fee (a credit covered the fee, BEAV-123).
    application_fee: str


class StripeGateway:
    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    def create_destination_charge(
        self,
        *,
        booking_id: int | str,
        amount_cents: int,
        application_fee_cents: int,
        destination_account: str,
        idempotency_key: str,
    ) -> stripe.PaymentIntent:
        """A PaymentIntent for the booking: task price plus fee, charged on behalf of
        the helper, who receives everything but the fee. A fee of 0 (the poster's
        TaskBeavo credit covered it, BEAV-123) sends no `application_fee_amount`."""
        _check_cents(amount_cents, "amount_cents")
        if (
            not isinstance(application_fee_cents, int)
            or isinstance(application_fee_cents, bool)
            or application_fee_cents < 0
        ):
            raise ValueError("application_fee_cents must be a non-negative number of cents.")
        if application_fee_cents >= amount_cents:
            raise ValueError("The application fee must be less than the amount charged.")
        _check_account(destination_account)
        params: dict[str, Any] = {
            "amount": amount_cents,
            "currency": CURRENCY,
            "on_behalf_of": destination_account,
            "transfer_data": {"destination": destination_account},
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"booking_id": str(booking_id)},
        }
        if application_fee_cents:
            params["application_fee_amount"] = application_fee_cents
        intent = self._post(
            "payment_intents.create",
            self._client.v1.payment_intents.create,
            params,
            idempotency_key=idempotency_key,
            span_attributes={"booking.id": str(booking_id), "amount.cents": amount_cents},
        )
        _warn_if_link_offered(booking_id, intent)
        return intent

    def cancel_payment_intent(
        self, *, booking_id: int | str, payment_intent: str, idempotency_key: str
    ) -> stripe.PaymentIntent:
        """Cancel the booking's PaymentIntent so it can't be paid any more. Stripe refuses
        it once the payment succeeded or is processing: the error carries that status."""
        _check_payment_intent(payment_intent)
        return self._post(
            "payment_intents.cancel",
            functools.partial(self._client.v1.payment_intents.cancel, payment_intent),
            {"cancellation_reason": "abandoned"},
            idempotency_key=idempotency_key,
            span_attributes={"booking.id": str(booking_id), "stripe.object_id": payment_intent},
        )

    def retrieve_payment_intent(
        self, *, booking_id: int | str, payment_intent: str
    ) -> stripe.PaymentIntent:
        """The booking's PaymentIntent as Stripe has it now, with its client secret, for a
        poster who comes back to an open checkout. A read, so no idempotency key."""
        _check_payment_intent(payment_intent)
        with tracer.start_as_current_span("stripe.payment_intents.retrieve") as span:
            span.set_attributes({"booking.id": str(booking_id), "stripe.object_id": payment_intent})
            try:
                return self._client.v1.payment_intents.retrieve(payment_intent)
            except stripe.StripeError as exc:
                error = _request_error("payment_intents.retrieve", exc)
                span.set_attribute("stripe.error_code", error.code)
                raise error from None

    def find_booking_payment_intents(self, *, booking_id: int | str) -> list[dict[str, str]]:
        """The PaymentIntents Stripe holds for the booking (`metadata.booking_id`), as
        `{"id", "status"}`. Finds one whose ID never reached us: acceptOffer lost Stripe's
        answer after Stripe created it. A read (Stripe's search API, which can lag behind
        by a minute), so no idempotency key."""
        query = f"metadata['booking_id']:'{uuid.UUID(str(booking_id))}'"
        with tracer.start_as_current_span("stripe.payment_intents.search") as span:
            span.set_attribute("booking.id", str(booking_id))
            try:
                found = self._client.v1.payment_intents.search({"query": query})
            except stripe.StripeError as exc:
                raise _request_error("payment_intents.search", exc) from None
            return [{"id": intent.id, "status": intent.status} for intent in found.data]

    def create_full_refund(
        self, *, booking_id: int | str, payment_intent: str, idempotency_key: str
    ) -> stripe.Refund:
        """Refund everything left on the booking's charge, fee included.

        Stripe reverses the whole transfer and refunds the whole application fee, so the
        helper and TaskBeavo both end at zero. It is exact only for a full refund; partial
        refunds come with the cancellation ticket.
        """
        return self._post(
            "refunds.create",
            self._client.v1.refunds.create,
            {
                "payment_intent": payment_intent,
                "reverse_transfer": True,
                "refund_application_fee": True,
                "metadata": {"booking_id": str(booking_id)},
            },
            idempotency_key=idempotency_key,
            span_attributes={"booking.id": str(booking_id)},
        )

    def retrieve_charge_parts(
        self, *, booking_id: int | str, payment_intent: str, expect_application_fee: bool = True
    ) -> ChargeParts:
        """The charge of the booking's PaymentIntent, with its transfer to the helper
        (`tr_`) and its application fee (`fee_`). A read, so no idempotency key.

        `expect_application_fee` is False only for a booking that charged no fee because a
        TaskBeavo credit covered it (BEAV-123). Otherwise a missing fee is refused like a
        missing transfer: Stripe fills both a few seconds after the payment, so the caller
        retries and then hands the settlement to support."""
        _check_payment_intent(payment_intent)
        with tracer.start_as_current_span("stripe.payment_intents.retrieve") as span:
            span.set_attributes({"booking.id": str(booking_id), "stripe.object_id": payment_intent})
            try:
                intent = self._client.v1.payment_intents.retrieve(
                    payment_intent, {"expand": ["latest_charge"]}
                )
            except stripe.StripeError as exc:
                raise _request_error("payment_intents.retrieve", exc) from None
            charge = intent.latest_charge
            if charge is None or isinstance(charge, str):
                raise StripeRequestError("payment_intents.retrieve", code="no_charge")
            parts = charge.to_dict()
            transfer = _id_of(parts.get("transfer"))
            fee = _id_of(parts.get("application_fee")) or ""
            fee_ok = _APPLICATION_FEE.fullmatch(fee) if fee else not expect_application_fee
            if not (transfer and _TRANSFER.fullmatch(transfer)) or not fee_ok:
                raise StripeRequestError(
                    "payment_intents.retrieve", code="not_a_destination_charge"
                )
            return ChargeParts(charge=charge.id, transfer=transfer, application_fee=fee)

    def refund_application_fee(
        self,
        *,
        booking_id: int | str,
        application_fee: str,
        split: SettlementSplit,
        idempotency_key: str,
    ) -> stripe.ApplicationFeeRefund:
        """Settlement step 1: give the fee TaskBeavo doesn't keep back to the helper's
        account. First, so TaskBeavo never pays out before the money is back and step 2
        always fits the helper's balance: they then hold P + (F - K), and the reversal of
        P + F - C - K leaves them C."""
        _check_id(application_fee, _APPLICATION_FEE, "application fee ID (fee_...)")
        return self._post(
            "application_fees.refunds.create",
            functools.partial(self._client.v1.application_fees.refunds.create, application_fee),
            {
                "amount": split.fee_refund_cents,
                "metadata": _metadata(booking_id, FEE_REFUND_ACTION),
            },
            idempotency_key=idempotency_key,
            span_attributes={"booking.id": str(booking_id), "amount.cents": split.fee_refund_cents},
        )

    def reverse_transfer(
        self,
        *,
        booking_id: int | str,
        transfer: str,
        split: SettlementSplit,
        fee_refund_id: str,
        idempotency_key: str,
    ) -> stripe.TransferReversal:
        """Settlement step 2: take the poster's refund back from the helper's transfer.
        Needs step 1's `fr_` ID, unless there was no fee to refund (BEAV-123)."""
        _check_id(transfer, _TRANSFER, "transfer ID (tr_...)")
        if split.fee_refund_cents:
            _check_id(fee_refund_id, _FEE_REFUND, "fee refund ID from step 1 (fr_...)")
        return self._post(
            "transfers.reversals.create",
            functools.partial(self._client.v1.transfers.reversals.create, transfer),
            {
                "amount": split.transfer_reversal_cents,
                "metadata": _metadata(booking_id, TRANSFER_REVERSAL_ACTION),
            },
            idempotency_key=idempotency_key,
            span_attributes={
                "booking.id": str(booking_id),
                "amount.cents": split.transfer_reversal_cents,
            },
        )

    def refund_poster(
        self,
        *,
        booking_id: int | str,
        payment_intent: str,
        split: SettlementSplit,
        transfer_reversal_id: str,
        idempotency_key: str,
    ) -> stripe.Refund:
        """Settlement step 3: pay the poster back from the money step 2 returned to
        TaskBeavo. Stripe's proportional reversal and fee refund are off: steps 1 and 2
        moved exact amounts. Needs step 2's `trr_` ID."""
        _check_payment_intent(payment_intent)
        _check_cents(split.refund_cents, "refund_cents")
        _check_id(
            transfer_reversal_id, _TRANSFER_REVERSAL, "transfer reversal ID from step 2 (trr_...)"
        )
        return self._post(
            "refunds.create",
            self._client.v1.refunds.create,
            {
                "payment_intent": payment_intent,
                "amount": split.refund_cents,
                "reverse_transfer": False,
                "refund_application_fee": False,
                "metadata": _metadata(booking_id, POSTER_REFUND_ACTION),
            },
            idempotency_key=idempotency_key,
            span_attributes={"booking.id": str(booking_id), "amount.cents": split.refund_cents},
        )

    def find_fee_refund(self, *, booking_id: int | str, application_fee: str) -> str | None:
        """Step 1 of the booking's settlement if Stripe has it: a read, no key."""
        _check_id(application_fee, _APPLICATION_FEE, "application fee ID (fee_...)")
        return self._find(
            "application_fees.refunds.list",
            lambda: self._client.v1.application_fees.refunds.list(
                application_fee, {"limit": 100}
            ).auto_paging_iter(),
            booking_id,
            FEE_REFUND_ACTION,
        )

    def find_transfer_reversal(self, *, booking_id: int | str, transfer: str) -> str | None:
        """Step 2 of the booking's settlement if Stripe has it: a read, no key."""
        _check_id(transfer, _TRANSFER, "transfer ID (tr_...)")
        return self._find(
            "transfers.reversals.list",
            lambda: self._client.v1.transfers.reversals.list(
                transfer, {"limit": 100}
            ).auto_paging_iter(),
            booking_id,
            TRANSFER_REVERSAL_ACTION,
        )

    def find_poster_refund(self, *, booking_id: int | str, payment_intent: str) -> str | None:
        """Step 3 of the booking's settlement if Stripe has it: a read, no key."""
        _check_payment_intent(payment_intent)
        return self._find(
            "refunds.list",
            lambda: self._client.v1.refunds.list(
                {"payment_intent": payment_intent, "limit": 100}
            ).auto_paging_iter(),
            booking_id,
            POSTER_REFUND_ACTION,
        )

    def create_payout(
        self,
        *,
        booking_id: int,
        connected_account: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> stripe.Payout:
        """Pay out from the helper's Stripe balance to their bank ("Release payment")."""
        _check_cents(amount_cents, "amount_cents")
        _check_account(connected_account)
        return self._post(
            "payouts.create",
            self._client.v1.payouts.create,
            {
                "amount": amount_cents,
                "currency": CURRENCY,
                "metadata": {"booking_id": str(booking_id)},
            },
            idempotency_key=idempotency_key,
            stripe_account=connected_account,
            span_attributes={"booking.id": str(booking_id), "amount.cents": amount_cents},
        )

    def create_express_account(
        self, *, helper_profile_id: int, idempotency_key: str
    ) -> stripe.Account:
        """A Croatian Express account for the helper, able to take destination charges
        (`card_payments` and `transfers`), with manual payouts: money leaves the helper's
        balance only on "Release payment". Stripe's onboarding collects identity and IBAN;
        nothing personal is sent from here."""
        return self._post(
            "accounts.create",
            self._client.v1.accounts.create,
            {
                "type": "express",
                "country": "HR",
                "capabilities": {
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                "settings": {"payouts": {"schedule": {"interval": "manual"}}},
                "metadata": {"helper_profile_id": str(helper_profile_id)},
            },
            idempotency_key=idempotency_key,
            span_attributes={"helper_profile.id": str(helper_profile_id)},
        )

    def create_onboarding_link(
        self,
        *,
        connected_account: str,
        return_url: str,
        refresh_url: str,
        idempotency_key: str,
    ) -> stripe.AccountLink:
        """A single-use link to Stripe's hosted Express onboarding. Also used when Stripe
        later asks for more information. Stripe sends the helper to `refresh_url` when the
        link has expired or was used, and to `return_url` when they leave the flow."""
        _check_account(connected_account)
        return self._post(
            "account_links.create",
            self._client.v1.account_links.create,
            {
                "account": connected_account,
                "type": "account_onboarding",
                "return_url": return_url,
                "refresh_url": refresh_url,
            },
            idempotency_key=idempotency_key,
            span_attributes={"stripe.account_id": connected_account},
        )

    def retrieve_account(self, *, connected_account: str) -> dict[str, Any]:
        """The connected account's current state, as a plain dict like a webhook's
        `data.object`. A read: Stripe takes idempotency keys on POST requests only, and a
        repeated read changes nothing."""
        _check_account(connected_account)
        with tracer.start_as_current_span("stripe.accounts.retrieve") as span:
            span.set_attribute("stripe.account_id", connected_account)
            return self._client.v1.accounts.retrieve(connected_account).to_dict()

    def _find(
        self,
        operation: str,
        listing: Callable[[], Iterable[Any]],
        booking_id: int | str,
        action: str,
    ) -> str | None:
        with tracer.start_as_current_span(f"stripe.{operation}") as span:
            span.set_attribute("booking.id", str(booking_id))
            try:
                for item in listing():
                    metadata = item.to_dict().get("metadata") or {}
                    if (
                        metadata.get("booking_id") == str(booking_id)
                        and metadata.get("action") == action
                    ):
                        span.set_attribute("stripe.object_id", item.id)
                        return item.id
            except stripe.StripeError as exc:
                raise _request_error(operation, exc) from None
            return None

    def _post(
        self,
        operation: str,
        create: Callable[..., T],
        params: dict[str, Any],
        *,
        idempotency_key: str,
        span_attributes: dict[str, str | int],
        stripe_account: str | None = None,
    ) -> T:
        options: dict[str, str] = {"idempotency_key": check_idempotency_key(idempotency_key)}
        if stripe_account is not None:
            options["stripe_account"] = stripe_account
        with tracer.start_as_current_span(f"stripe.{operation}") as span:
            span.set_attributes(span_attributes)
            if "currency" in params:
                span.set_attribute("currency", params["currency"])
            try:
                result = create(params, options)
            except stripe.StripeError as exc:
                error = _request_error(operation, exc)
                span.set_attribute("stripe.error_code", error.code)
                raise error from None
            object_id = getattr(result, "id", None)
            if isinstance(object_id, str):
                span.set_attribute("stripe.object_id", object_id)
            return result


@functools.cache
def get_gateway() -> StripeGateway:
    """The process-wide gateway, built from settings on first use. With DEMO_PAYMENTS on,
    its requests go to the demo stand-in (payments/demo_stripe.py), never to Stripe."""
    if settings.DEMO_PAYMENTS:
        from payments.demo_stripe import demo_client

        return StripeGateway(demo_client())
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("Set STRIPE_SECRET_KEY to call Stripe.")
    client = stripe.StripeClient(
        settings.STRIPE_SECRET_KEY, max_network_retries=MAX_NETWORK_RETRIES
    )
    return StripeGateway(client)


def verify_webhook_signature(payload: bytes, header: str | None, secrets: list[str]) -> None:
    """Raise `InvalidWebhookSignatureError` unless `header` signs `payload`, within the
    replay window, with one of `secrets` (the platform and Connect endpoints each have
    their own, and a rotated secret overlaps its successor)."""
    try:
        # The SDK decodes before checking anything; a non-UTF-8 body is simply unsigned.
        payload.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidWebhookSignatureError("The payload is not UTF-8.") from None
    for secret in secrets:
        try:
            stripe.WebhookSignature.verify_header(
                payload, header, secret, tolerance=WEBHOOK_TOLERANCE_SECONDS
            )
        except stripe.SignatureVerificationError:
            continue
        return
    raise InvalidWebhookSignatureError("No configured webhook secret signs this payload.")


def _request_error(operation: str, exc: stripe.StripeError) -> StripeRequestError:
    error = exc.error
    intent = getattr(error, "payment_intent", None)
    return StripeRequestError(
        operation,
        code=getattr(error, "code", None) or exc.code or "",
        param=getattr(error, "param", None) or "",
        http_status=exc.http_status,
        payment_intent_status=getattr(intent, "status", None) or "",
    )


def _metadata(booking_id: int | str, action: str) -> dict[str, str]:
    return {"booking_id": str(booking_id), "action": action}


def _id_of(value: Any) -> str | None:
    """A Stripe field holding an ID, or the object it expands to (as a dict)."""
    if isinstance(value, str):
        return value
    return value.get("id") if isinstance(value, dict) else None


class StripeIdError(ValueError):
    """A Stripe ID failed its shape check: a stored ID is wrong, so a retry can't help."""


def _check_id(value: str, pattern: re.Pattern[str], what: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StripeIdError(f"Expected a {what}.")


def _check_payment_intent(payment_intent: str) -> None:
    if not isinstance(payment_intent, str) or not _PAYMENT_INTENT.fullmatch(payment_intent):
        raise StripeIdError("Expected a PaymentIntent ID (pi_...).")


def _warn_if_link_offered(booking_id: int | str, intent: stripe.PaymentIntent) -> None:
    """Stripe Link keeps a Stripe login on the payer's device, which needs consent outside
    checkout (BEAV-190). It is off in the Stripe Dashboard's payment method configurations;
    for these `on_behalf_of` charges the one that counts is Connect > Payment methods for
    connected accounts (docs/storage-inventory.md). Stripe refuses `link` in
    `excluded_payment_method_types`. Should the setting be switched back on, checkout keeps
    working (the clients hide Link) and Sentry gets a warning with the booking and
    PaymentIntent IDs only (user decision 2026-11-07, review R1-2)."""
    if "link" not in (getattr(intent, "payment_method_types", None) or []):
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("booking.id", str(booking_id))
        scope.set_tag("stripe.object_id", str(getattr(intent, "id", "")))
        scope.set_tag("support.reason", "STRIPE_LINK_ENABLED")
        sentry_sdk.capture_message(
            "Stripe Link is on in the payment method configuration", level="warning"
        )


def _check_cents(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer number of cents.")


def _check_account(account: str) -> None:
    if not _CONNECTED_ACCOUNT.fullmatch(account):
        raise ValueError("Expected a connected account ID (acct_...).")
