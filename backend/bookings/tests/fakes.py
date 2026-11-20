"""The Stripe boundary for booking tests: no test calls Stripe."""

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from payments.stripe_client import StripeRequestError


@dataclass
class FakeGateway:
    """The Stripe boundary: records each call and answers like Stripe would. A key used
    again returns the first result, as Stripe's idempotency does."""

    charges: list[dict[str, Any]] = field(default_factory=list)
    cancels: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)
    charge_error: StripeRequestError | None = None
    cancel_error: StripeRequestError | None = None
    refund_error: StripeRequestError | None = None
    # What a metadata search finds: {"id", "status"} per PaymentIntent.
    intents: list[dict[str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    client_secret: str | None = "secret_test"
    # What retrieve_payment_intent answers: Stripe's status of the PaymentIntent.
    intent_status: str = "requires_payment_method"
    retrieve_error: StripeRequestError | None = None
    retrieves: list[str] = field(default_factory=list)
    # Runs inside create_destination_charge, after the booking committed.
    during_charge: Callable[[], None] | None = None

    def create_destination_charge(self, **kwargs: Any) -> SimpleNamespace:
        self.charges.append(kwargs)
        if self.during_charge is not None:
            self.during_charge()
        if self.charge_error is not None:
            raise self.charge_error
        return SimpleNamespace(
            id=intent_id(kwargs["booking_id"]),
            object="payment_intent",
            client_secret=self.client_secret
            and f"{intent_id(kwargs['booking_id'])}_{self.client_secret}",
        )

    def cancel_payment_intent(self, **kwargs: Any) -> SimpleNamespace:
        self.cancels.append(kwargs)
        if self.cancel_error is not None:
            raise self.cancel_error
        return SimpleNamespace(id=kwargs["payment_intent"], status="canceled")

    def retrieve_payment_intent(self, *, booking_id: str, payment_intent: str) -> SimpleNamespace:
        self.retrieves.append(payment_intent)
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return SimpleNamespace(
            id=payment_intent,
            status=self.intent_status,
            client_secret=self.client_secret and f"{payment_intent}_{self.client_secret}",
        )

    def find_booking_payment_intents(self, *, booking_id: str) -> list[dict[str, str]]:
        self.searches.append(booking_id)
        return list(self.intents)

    def create_full_refund(self, **kwargs: Any) -> SimpleNamespace:
        self.refunds.append(kwargs)
        if self.refund_error is not None:
            raise self.refund_error
        keys = list(dict.fromkeys(call["idempotency_key"] for call in self.refunds))
        return SimpleNamespace(id=f"re_test{keys.index(kwargs['idempotency_key']) + 1}")


# UUID hex is mostly digits, and a run of 9 or more reads as an OIB or phone number, which
# the scrubber filters even inside a Stripe ID. Real IDs are base62, so map digits to letters.
_HEX_DIGITS_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")


@dataclass
class FakePayoutStripe:
    """Stripe for payouts and tips (bookings/payouts.py, bookings/tips.py). A reused key
    returns the first result; `fail_with` makes the next payout raise once, after doing
    the work when `fail_after_work` (a lost answer)."""

    payouts: list[dict[str, Any]] = field(default_factory=list)
    lookups: list[dict[str, Any]] = field(default_factory=list)
    tips: list[dict[str, Any]] = field(default_factory=list)
    by_key: dict[str, str] = field(default_factory=dict)
    # (booking_id, kind) -> payout ID: what Stripe holds.
    made: dict[tuple[str, str], str] = field(default_factory=dict)
    fail_with: StripeRequestError | None = None
    fail_after_work: bool = False
    tip_error: StripeRequestError | None = None
    # Make the tip PaymentIntent, then raise `tip_error` (a lost answer).
    tip_fail_after_work: bool = False
    # booking_id -> the tip PaymentIntent Stripe holds; key -> the one it made.
    tip_made: dict[str, SimpleNamespace] = field(default_factory=dict)
    tip_by_key: dict[str, SimpleNamespace] = field(default_factory=dict)
    tip_lookups: list[str] = field(default_factory=list)

    def create_payout(self, **kwargs: Any) -> SimpleNamespace:
        self.payouts.append(kwargs)
        key = kwargs["idempotency_key"]
        if key in self.by_key:
            return SimpleNamespace(id=self.by_key[key])
        failing, self.fail_with = self.fail_with, None
        if failing is not None and not self.fail_after_work:
            raise failing
        payout_id = f"po_test{len(self.by_key) + 1}"
        self.by_key[key] = payout_id
        self.made[(kwargs["booking_id"], kwargs["kind"])] = payout_id
        if failing is not None:
            raise failing
        return SimpleNamespace(id=payout_id)

    def find_payout(self, **kwargs: Any) -> str | None:
        self.lookups.append(kwargs)
        return self.made.get((kwargs["booking_id"], kwargs["kind"]))

    def create_tip_charge(self, **kwargs: Any) -> SimpleNamespace:
        self.tips.append(kwargs)
        key = kwargs["idempotency_key"]
        if key in self.tip_by_key:
            return self.tip_by_key[key]
        failing, self.tip_error = self.tip_error, None
        if failing is not None and not self.tip_fail_after_work:
            raise failing
        pk = (
            "pi_testtip"
            + kwargs["booking_id"].replace("-", "")[:12].translate(_HEX_DIGITS_TO_LETTERS)
            + "v"
            + key.rsplit(":v", 1)[1]
        )
        intent = SimpleNamespace(id=pk, client_secret=f"{pk}_secret_tip")
        self.tip_by_key[key] = intent
        self.tip_made[kwargs["booking_id"]] = intent
        if failing is not None:
            raise failing
        return intent

    def retrieve_payment_intent(self, *, booking_id: str, payment_intent: str) -> SimpleNamespace:
        self.tip_lookups.append(payment_intent)
        [intent] = [i for i in self.tip_by_key.values() if i.id == payment_intent]
        return intent

    def find_tip_payment_intent(self, *, booking_id: str) -> dict[str, str] | None:
        self.tip_lookups.append(booking_id)
        intent = self.tip_made.get(booking_id)
        return intent and {"id": intent.id, "client_secret": intent.client_secret}

    def sent(self) -> int:
        return len(self.by_key)


def intent_id(booking_id: str) -> str:
    return "pi_test" + booking_id.replace("-", "")[:20].translate(_HEX_DIGITS_TO_LETTERS)


@dataclass
class FakeSettlementStripe:
    """Stripe for cancellation settlements (bookings/settlement.py): a destination charge
    whose money sits in the helper's account (P) and TaskBeavo's (F). Each step moves it
    as Stripe would, a reused key returns the first result, and `fail` makes a step raise
    `fail_with` once, after doing the work (`fail_after_work`) or before it."""

    amount_cents: int = 3_000
    fee_cents: int = 360
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)
    done: dict[str, str] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    fail: str | None = None
    fail_with: StripeRequestError | None = None
    fail_after_work: bool = False
    helper: int = 0
    taskbeavo: int = 0
    refunded: int = 0

    def __post_init__(self) -> None:
        self.helper, self.taskbeavo = self.amount_cents, self.fee_cents

    def retrieve_charge_parts(
        self, *, booking_id: str, payment_intent: str, expect_application_fee: bool = True
    ) -> SimpleNamespace:
        # A charge whose fee a TaskBeavo credit covered has no application fee (BEAV-123).
        assert expect_application_fee == bool(self.fee_cents)
        fee = "fee_test1" if self.fee_cents else ""
        return SimpleNamespace(charge="ch_test1", transfer="tr_test1", application_fee=fee)

    def refund_application_fee(self, **kwargs: Any) -> SimpleNamespace:
        return self._step("fee_refund", "fr_test1", kwargs, kwargs["split"].fee_refund_cents)

    def reverse_transfer(self, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["fee_refund_id"] == self.done.get("fee_refund", "")
        return self._step(
            "transfer_reversal", "trr_test1", kwargs, kwargs["split"].transfer_reversal_cents
        )

    def refund_poster(self, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["transfer_reversal_id"] == self.done.get("transfer_reversal")
        return self._step("refund", "re_test1", kwargs, kwargs["split"].refund_cents)

    def find_fee_refund(self, **kwargs: Any) -> str | None:
        self.lookups.append("fee_refund")
        return self.done.get("fee_refund")

    def find_transfer_reversal(self, **kwargs: Any) -> str | None:
        self.lookups.append("transfer_reversal")
        return self.done.get("transfer_reversal")

    def find_poster_refund(self, **kwargs: Any) -> str | None:
        self.lookups.append("refund")
        return self.done.get("refund")

    def sent(self, step: str | None = None) -> list[str]:
        return [name for name, _ in self.calls if step is None or name == step]

    def keys(self, step: str) -> list[str]:
        return [kwargs["idempotency_key"] for name, kwargs in self.calls if name == step]

    def _step(
        self, name: str, stripe_id: str, kwargs: dict[str, Any], amount: int
    ) -> SimpleNamespace:
        self.calls.append((name, kwargs))
        key = kwargs["idempotency_key"]
        if key in self.by_key:
            return SimpleNamespace(id=self.by_key[key])
        failing = self.fail == name and self.fail_with is not None
        if failing and not self.fail_after_work:
            self.fail = None
            raise self.fail_with  # type: ignore[misc]
        if name in self.done:
            # Stripe would move the money again under a new key.
            stripe_id = f"{stripe_id}_again"
        if name == "fee_refund":
            self.taskbeavo -= amount
            self.helper += amount
        elif name == "transfer_reversal":
            assert amount <= self.helper, "the reversal must fit the helper's balance"
            self.helper -= amount
            self.taskbeavo += amount
        else:
            assert amount <= self.taskbeavo, "TaskBeavo never refunds money it doesn't hold"
            self.taskbeavo -= amount
            self.refunded += amount
        self.done.setdefault(name, stripe_id)
        if failing:
            self.fail = None
            raise self.fail_with  # type: ignore[misc]
        self.by_key[key] = stripe_id
        return SimpleNamespace(id=stripe_id)
