"""acceptOffer and its payment (BEAV-109): consent before anything is charged, the
destination charge, no money moving from the mutation (rule a), a double accept refused,
Stripe refusing the helper's account (BEAV-95 hand-off), a withdrawal during checkout
cancelling the PaymentIntent (BEAV-100 hand-off), and a payment that succeeds after the
checkout failed being refunded in full (BEAV-107 known gap)."""

import copy
import datetime as dt
import json
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.core.cache import caches
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import sign_in
from accounts.tests.test_schema import error_codes, gql
from areas.models import ServiceArea
from bookings import checkout
from bookings.checkout import LATE_PAYMENT_REFUNDED_TOPIC, LATE_PAYMENT_TOPIC
from bookings.models import Booking, BookingEvent, ConsentRecord
from bookings.tests.factories import checkout_fee_version, checkout_total
from bookings.tests.fakes import FakeGateway, intent_id
from core import outbox
from core.errors import ApiError, ErrorCode
from core.models import Outbox
from helpers.models import HelperProfile
from helpers.tests.business import give_business_address
from legal.models import LegalDocument, TermsAcceptance
from legal.tests.factories import publish_version
from notifications.models import Delivery
from notifications.tests.conftest import register_device
from offers import services as offers
from offers.models import Offer
from offers.outbox_handlers import WITHDRAWN_TOPIC
from offers.tests.factories import make_helper, open_task
from payments.models import ConnectedAccount, StripeEvent
from payments.stripe_client import StripeRequestError
from payments.tests.stripe_fakes import load_event, sign

pytestmark = pytest.mark.django_db

S = Booking.Status
SECRET = "whsec_test_webhook_secret_not_secret"
CANCELLATION = "2026-11-c"


@pytest.fixture
def reconciled(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    queued: list[int] = []
    monkeypatch.setattr(
        "payments.tasks.reconcile_connected_account.delay", lambda pk: queued.append(pk)
    )
    return queued


@pytest.fixture(autouse=True)
def legal_documents(request: pytest.FixtureRequest) -> None:
    if "django_db" in request.keywords:
        publish_version(LegalDocument.Kind.TERMS, "2026-11-t")
        publish_version(LegalDocument.Kind.CANCELLATION, CANCELLATION)


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


@pytest.fixture(autouse=True)
def webhook_secret() -> Iterator[None]:
    with override_settings(STRIPE_WEBHOOK_SECRETS=[SECRET]):
        yield


def consent(**changes: Any) -> checkout.CheckoutConsent:
    fields: dict[str, Any] = {
        "cancellation_policy_accepted": True,
        "early_start_requested": True,
        "cancellation_version": CANCELLATION,
        "platform": "ios",
        "language": "de",
        "variant": "private",
        **changes,
    }
    return checkout.CheckoutConsent(**fields)


def make_trader(offer: Offer) -> HelperProfile:
    """The offer's helper becomes a verified obrt with a registered business address."""
    profile = HelperProfile.objects.get(user=offer.helper)
    profile.declared_status = HelperProfile.Status.BUSINESS
    profile.business_kind = HelperProfile.BusinessKind.OBRT
    profile.business_name = "Vrtlarski obrt Horvat"
    profile.business_oib = profile.oib
    profile.save()
    give_business_address(offer.helper)
    return HelperProfile.objects.get(pk=profile.pk)


def two_offers(helpers: tuple[str, str] = ("marko", "ivan")) -> tuple[Offer, Offer]:
    task = open_task()
    chosen = offers.send_offer(make_helper(helpers[0]), str(task.pk), 3_000)
    other = offers.send_offer(make_helper(helpers[1]), str(task.pk), 2_800)
    return chosen, other


def accept(offer: Offer, poster: User | None = None, **changes: Any) -> checkout.CheckoutSession:
    """Accept with the tick-box pair the offer's helper type shows, unless `changes` says."""
    profile = HelperProfile.objects.filter(user=offer.helper).first()
    changes.setdefault("variant", "trader" if profile and profile.is_trader else "private")
    return checkout.accept_offer(
        poster or offer.task.poster,
        str(offer.pk),
        expected_price_cents=offer.price_cents,
        expected_total_cents=checkout_total(offer.price_cents),
        expected_fee_schedule_version=checkout_fee_version(),
        consent=consent(**changes),
    )


def code(exc: pytest.ExceptionInfo[ApiError]) -> str:
    return exc.value.extensions["code"]


def succeeded_event(booking: Booking, event_id: str = "evt_test_paid", **changes: Any) -> dict:
    event = copy.deepcopy(load_event("payment_intent.succeeded"))
    event["id"] = event_id
    intent = event["data"]["object"]
    intent.update(
        {
            "id": booking.payment_intent_id,
            "amount": booking.amount_cents + booking.fee_cents,
            "metadata": {"booking_id": str(booking.pk)},
            "transfer_data": {"destination": helper_account(booking)},
            "on_behalf_of": helper_account(booking),
            **changes,
        }
    )
    return event


def helper_account(booking: Booking) -> str:
    return ConnectedAccount.objects.get(helper__user_id=booking.helper_id).stripe_account_id


def post_event(client: Client, event: dict[str, Any]) -> int:
    body = json.dumps(event).encode()
    response = client.post(
        "/webhooks/stripe",
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": sign(body, SECRET)},
    )
    return response.status_code


def publish(topic: str, booking: Booking) -> None:
    for row in Outbox.objects.filter(topic=topic, aggregate_id=str(booking.pk)):
        outbox.publish_row(row.pk)


def refused(code: str = "", *, param: str = "", status: str = "") -> StripeRequestError:
    return StripeRequestError("test", code=code, param=param, payment_intent_status=status)


# Opening the checkout.


def test_accepting_opens_a_destination_charge_checkout(gateway: FakeGateway) -> None:
    chosen, other = two_offers()
    account = ConnectedAccount.objects.get(helper__user=chosen.helper)

    session = accept(chosen)

    booking = session.booking
    assert booking.status == S.AWAITING_PAYMENT
    assert session.client_secret == f"{intent_id(str(booking.pk))}_secret_test"
    assert gateway.charges == [
        {
            "booking_id": str(booking.pk),
            "amount_cents": 3_360,
            "application_fee_cents": 360,
            "destination_account": account.stripe_account_id,
            "idempotency_key": f"booking:{booking.pk}:payment_intent:v1",
        }
    ]
    booking.refresh_from_db()
    assert booking.payment_intent_id == intent_id(str(booking.pk))
    chosen.refresh_from_db()
    other.refresh_from_db()
    assert (chosen.status, other.status) == (Offer.Status.ACCEPTED, Offer.Status.DECLINED)


def test_no_money_moves_from_the_mutation(gateway: FakeGateway) -> None:
    """Rule a: only the webhook confirms the payment."""
    chosen, _ = two_offers()

    booking = accept(chosen).booking

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert not BookingEvent.objects.filter(booking=booking, type="funds_held").exists()
    assert gateway.refunds == []


def test_the_booking_records_the_versions_the_acceptance_and_the_consent(
    gateway: FakeGateway,
) -> None:
    chosen, _ = two_offers()
    poster = chosen.task.poster

    booking = accept(chosen, platform="web").booking

    booking.refresh_from_db()
    assert (booking.terms_version, booking.cancellation_version) == ("2026-11-t", CANCELLATION)
    [acceptance] = TermsAcceptance.objects.filter(booking=booking)
    assert acceptance.user_id == poster.pk
    assert acceptance.document.kind == LegalDocument.Kind.CANCELLATION
    assert acceptance.document.version == CANCELLATION
    # The language the checkout showed, not the account's.
    assert poster.locale != "de"
    assert acceptance.document.language == "de"
    assert acceptance.platform == "web"
    record = ConsentRecord.objects.get(booking=booking)
    assert record.terms_version == "2026-11-t"
    assert record.cancellation_version == CANCELLATION
    assert record.withdrawal_notice_version == CANCELLATION
    assert record.language == "de"
    assert record.helper_is_trader is False
    assert record.consent_variant == "private"
    assert record.platform == "web"
    assert record.express_request_at is not None


def test_a_consent_record_cant_be_changed() -> None:
    with pytest.raises(ValueError):
        ConsentRecord.objects.update(platform="web")


@pytest.mark.parametrize(
    "changes",
    [
        {"cancellation_policy_accepted": False},
        {"early_start_requested": False},
        {"cancellation_policy_accepted": False, "early_start_requested": False},
        {"cancellation_version": "2025-01"},
        {"cancellation_version": ""},
        {"platform": "fax"},
        {"language": "it"},
        {"variant": "both"},
    ],
    ids=[
        "no-policy",
        "no-early-start",
        "neither",
        "old-version",
        "no-version",
        "platform",
        "language",
        "variant",
    ],
)
def test_without_both_consents_nothing_is_booked_or_charged(
    gateway: FakeGateway, changes: dict[str, Any]
) -> None:
    chosen, _ = two_offers()

    with pytest.raises(ApiError) as exc:
        accept(chosen, **changes)

    assert code(exc) == ErrorCode.CONSENT_REQUIRED.name
    assert not Booking.objects.exists()
    assert not TermsAcceptance.objects.filter(booking__isnull=False).exists()
    assert gateway.charges == []


def test_a_trader_helpers_booking_records_the_trader_wording(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    make_trader(chosen)

    booking = accept(chosen, variant="trader").booking

    record = ConsentRecord.objects.get(booking=booking)
    assert (record.helper_is_trader, record.consent_variant) == (True, "trader")


@pytest.mark.parametrize(
    ("trader", "shown"),
    [(True, "private"), (False, "trader")],
    ids=["became-trader", "became-private"],
)
def test_wording_for_the_other_helper_type_books_nothing(
    gateway: FakeGateway, trader: bool, shown: str
) -> None:
    chosen, _ = two_offers()
    if trader:
        make_trader(chosen)

    with pytest.raises(ApiError) as exc:
        accept(chosen, variant=shown)

    assert code(exc) == ErrorCode.CONSENT_WORDING_CHANGED.name
    assert not Booking.objects.exists()
    assert gateway.charges == []


def test_no_cancellation_policy_in_force_means_no_checkout(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    publish_version(
        LegalDocument.Kind.CANCELLATION,
        "2099-01",
        effective_at=chosen.created_at.replace(year=2099),
    )

    with pytest.raises(ApiError) as exc:
        accept(chosen, cancellation_version="2099-01")

    assert code(exc) == ErrorCode.CONSENT_REQUIRED.name
    assert gateway.charges == []


@pytest.mark.parametrize(
    "status", [s for s in ConnectedAccount.Status if s != ConnectedAccount.Status.ACTIVE]
)
def test_a_helper_whose_account_isnt_active_cant_be_booked(
    gateway: FakeGateway, status: ConnectedAccount.Status
) -> None:
    chosen, _ = two_offers()
    ConnectedAccount.objects.filter(helper__user=chosen.helper).update(status=status)

    with pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS.name
    assert not Booking.objects.exists()
    assert gateway.charges == []


def test_someone_elses_offer_is_not_found(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    stranger = open_task().poster

    with pytest.raises(ApiError) as exc:
        accept(chosen, poster=stranger)

    assert code(exc) == ErrorCode.OFFER_NOT_FOUND.name
    assert gateway.charges == []


def test_a_task_outside_the_service_areas_is_not_booked(gateway: FakeGateway) -> None:
    """Rule j: the area is checked again when the booking is created."""
    chosen, _ = two_offers()
    ServiceArea.objects.update(active=False)

    with pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.OUT_OF_SERVICE_AREA.name
    assert not Booking.objects.exists()
    assert gateway.charges == []


def test_a_second_accept_of_a_booked_task_is_refused(gateway: FakeGateway) -> None:
    chosen, other = two_offers()
    accept(chosen)

    with pytest.raises(ApiError) as exc:
        accept(other)

    assert code(exc) == ErrorCode.TASK_ALREADY_BOOKED.name
    assert Booking.objects.count() == 1
    assert len(gateway.charges) == 1


# Stripe refusing the PaymentIntent (BEAV-95 hand-off).


@pytest.mark.parametrize(
    "error",
    [
        refused("insufficient_capabilities_for_transfer"),
        refused(param="transfer_data[destination]"),
        refused(param="on_behalf_of"),
    ],
    ids=["capabilities", "destination", "on-behalf-of"],
)
def test_stripe_refusing_the_helpers_account_fails_the_checkout(
    gateway: FakeGateway, reconciled: list[int], error: StripeRequestError
) -> None:
    """Our copy of the account said ACTIVE, Stripe disagrees: no booking payment state,
    a clear code, and the account is read back from Stripe so offers pause."""
    chosen, other = two_offers()
    account = ConnectedAccount.objects.get(helper__user=chosen.helper)
    gateway.charge_error = error

    with pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS.name
    booking = Booking.objects.get()
    assert booking.status == S.PAYMENT_FAILED
    assert booking.payment_intent_id == ""
    assert not BookingEvent.objects.filter(booking=booking, type="funds_held").exists()
    [failed] = BookingEvent.objects.filter(booking=booking, type="payment_failed")
    assert failed.data["reason"] == "helper_account"
    assert reconciled == [account.pk]
    chosen.refresh_from_db()
    other.refresh_from_db()
    assert (chosen.status, other.status) == (Offer.Status.SENT, Offer.Status.SENT)


def test_any_other_stripe_refusal_fails_the_checkout_without_a_reconcile(
    gateway: FakeGateway, reconciled: list[int]
) -> None:
    chosen, _ = two_offers()
    gateway.charge_error = refused("api_key_expired")

    with pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.PAYMENT_UNAVAILABLE.name
    assert Booking.objects.get().status == S.PAYMENT_FAILED
    assert reconciled == []
    # The offer is open again: the poster can try once more.
    gateway.charge_error = None
    assert accept(chosen).booking.status == S.AWAITING_PAYMENT


# A withdrawal during checkout (BEAV-100 hand-off).


def withdraw(offer: Offer) -> None:
    offers.withdraw_offer(User.objects.get(pk=offer.helper_id), str(offer.pk))


def test_a_withdrawal_during_checkout_cancels_the_payment_intent(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    register_device(booking.poster, "ExponentPushToken[poster]")
    withdraw(chosen)

    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)

    assert gateway.cancels == [
        {
            "booking_id": str(booking.pk),
            "payment_intent": intent_id(str(booking.pk)),
            "idempotency_key": f"offer:{chosen.pk}:withdraw:v1",
        }
    ]
    assert BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").exists()
    assert gateway.refunds == []
    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    # The poster is told the offer was withdrawn.
    assert Delivery.objects.filter(user=booking.poster, kind="offer.withdrawn").exists()


def test_the_withdrawal_handler_is_idempotent(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    [row] = Outbox.objects.filter(topic=WITHDRAWN_TOPIC, aggregate_id=str(chosen.pk))

    with django_capture_on_commit_callbacks(execute=True):
        outbox._handlers[WITHDRAWN_TOPIC](row)
        outbox._handlers[WITHDRAWN_TOPIC](row)

    keys = {call["idempotency_key"] for call in gateway.cancels}
    assert keys == {f"offer:{chosen.pk}:withdraw:v1"}
    assert (
        BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").count() == 1
    )


def test_a_withdrawal_before_acceptance_calls_stripe_for_nothing(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, _ = two_offers()
    withdraw(chosen)

    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)

    assert (gateway.cancels, gateway.refunds) == ([], [])


def test_a_withdrawal_of_a_payment_that_already_succeeded_refunds_it(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Known gap 1: the poster paid just as the helper withdrew. Stripe won't cancel, so
    the payment is refunded in full, fee included, with the shared refund key."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="succeeded")

    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)

    assert gateway.refunds == [
        {
            "booking_id": str(booking.pk),
            "payment_intent": intent_id(str(booking.pk)),
            "idempotency_key": f"booking:{booking.pk}:refund:withdrawn:v1",
        }
    ]
    booking.refresh_from_db()
    assert booking.late_payment_refund_id
    assert Outbox.objects.filter(
        topic=LATE_PAYMENT_REFUNDED_TOPIC, aggregate_id=str(booking.pk)
    ).exists()


@pytest.mark.parametrize("status", ["canceled", "processing"])
def test_a_payment_intent_already_cancelled_or_processing_isnt_refunded(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any, status: str
) -> None:
    chosen, _ = two_offers()
    accept(chosen)
    withdraw(chosen)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status=status)

    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)

    assert gateway.refunds == []


def test_a_withdrawal_while_the_payment_intent_is_created_gets_no_client_secret(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Known gap 4: the booking is checked again before the client secret goes out. The
    withdrawal came before the PaymentIntent was stored, so acceptOffer cancels it."""
    chosen, _ = two_offers()
    gateway.during_charge = lambda: withdraw(chosen)

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.OFFER_NOT_EDITABLE.name
    booking = Booking.objects.get()
    assert booking.status == S.PAYMENT_FAILED
    assert booking.payment_intent_id == intent_id(str(booking.pk))
    # acceptOffer and the offer.withdrawn handler may both cancel it: one key, one effect.
    assert {call["idempotency_key"] for call in gateway.cancels} == {
        f"offer:{chosen.pk}:withdraw:v1"
    }
    assert (
        BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").count() == 1
    )


# payment_intent.succeeded (known gaps 2, 4 and 5).


def test_the_webhook_holds_the_funds(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()

    assert post_event(client, succeeded_event(booking)) == 200

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    [held] = BookingEvent.objects.filter(booking=booking, type="funds_held")
    assert held.data["payment_intent"] == booking.payment_intent_id
    assert held.data["stripe_event"] == "evt_test_paid"


def test_a_replayed_payment_event_applies_once(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()
    event = succeeded_event(booking)

    assert post_event(client, event) == 200
    assert post_event(client, event) == 200
    # A redelivery under a new event ID finds the booking past AWAITING_PAYMENT.
    assert post_event(client, succeeded_event(booking, "evt_test_again")) == 200

    assert BookingEvent.objects.filter(booking=booking, type="funds_held").count() == 1


def test_a_payment_that_doesnt_match_the_booking_waits_for_a_human(
    client: Client, gateway: FakeGateway
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()

    assert post_event(client, succeeded_event(booking, amount=100)) == 200

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    [flag] = BookingEvent.objects.filter(booking=booking, type="support_flag")
    assert flag.data["reason"] == checkout.PAYMENT_MISMATCH
    assert flag.data["mismatch"] == "amount"


@pytest.mark.parametrize(
    "changes",
    [
        {"transfer_data": {"destination": "acct_1SomeoneElse"}},
        {"on_behalf_of": "acct_1SomeoneElse"},
        {"transfer_data": None},
    ],
    ids=["destination", "on-behalf-of", "no-transfer"],
)
def test_a_payment_to_another_account_waits_for_a_human(
    client: Client, gateway: FakeGateway, changes: dict[str, Any]
) -> None:
    """Review R1-4: the money must go to the booking's helper."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.refresh_from_db()

    assert post_event(client, succeeded_event(booking, **changes)) == 200

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    [flag] = BookingEvent.objects.filter(booking=booking, type="support_flag")
    assert flag.data["mismatch"] == "destination"


def test_a_payment_for_no_booking_is_acknowledged(client: Client) -> None:
    event = copy.deepcopy(load_event("payment_intent.succeeded"))

    assert post_event(client, event) == 200

    assert StripeEvent.objects.get(pk=event["id"]).processed_at is not None


def test_a_payment_after_the_checkout_failed_is_refunded_and_the_poster_told(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Known gap 2: the webhook answers 200 (no retry storm), the payment is refunded in
    full, fee included, and the poster hears of it in their language. Nothing fiscal is
    issued for the fee (known gap 3)."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    booking.poster.locale = "de"
    booking.poster.save(update_fields=["locale"])
    register_device(booking.poster, "ExponentPushToken[poster]")
    withdraw(chosen)
    booking.refresh_from_db()

    assert post_event(client, succeeded_event(booking)) == 200

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert BookingEvent.objects.filter(booking=booking, type="late_payment").exists()
    with django_capture_on_commit_callbacks(execute=True):
        publish(LATE_PAYMENT_TOPIC, booking)
    assert [refund["idempotency_key"] for refund in gateway.refunds] == [
        f"booking:{booking.pk}:refund:withdrawn:v1"
    ]
    booking.refresh_from_db()
    assert booking.late_payment_refund_id

    publish(LATE_PAYMENT_REFUNDED_TOPIC, booking)

    deliveries = Delivery.objects.filter(user=booking.poster, kind="booking.late_payment_refunded")
    assert {d.channel for d in deliveries} == {"push", "email"}
    assert not BookingEvent.objects.filter(booking=booking, type="funds_held").exists()


def test_a_replayed_late_payment_is_refunded_once(
    client: Client, gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    booking.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        assert post_event(client, succeeded_event(booking)) == 200
        publish(LATE_PAYMENT_TOPIC, booking)

        assert post_event(client, succeeded_event(booking)) == 200
        assert post_event(client, succeeded_event(booking, "evt_test_again")) == 200
        publish(LATE_PAYMENT_TOPIC, booking)
        checkout.settle_failed_checkout(str(booking.pk))

    assert len(gateway.refunds) == 1
    assert BookingEvent.objects.filter(booking=booking, type="late_payment").count() == 1
    assert BookingEvent.objects.filter(booking=booking, type="late_payment_refunded").count() == 1


def test_no_client_secret_fails_the_checkout_and_cancels_the_payment_intent(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    chosen, _ = two_offers()
    gateway.client_secret = None

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(ApiError) as exc:
        accept(chosen)

    assert code(exc) == ErrorCode.PAYMENT_UNAVAILABLE.name
    booking = Booking.objects.get()
    assert booking.status == S.PAYMENT_FAILED
    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"booking:{booking.pk}:checkout_cancel:v1"
    ]


def age(booking: Booking, minutes: int) -> None:
    """The checkout failed, and any late payment arrived, `minutes` ago."""
    then = timezone.now() - dt.timedelta(minutes=minutes)
    Booking.objects.filter(pk=booking.pk).update(status_changed_at=then)
    BookingEvent.objects.filter(booking=booking, type="late_payment").update(created_at=then)


def test_the_sweep_refunds_a_late_payment_whose_task_gave_up(
    client: Client, gateway: FakeGateway
) -> None:
    """Review M1: Stripe was down for longer than the settle task's retries. The poster
    is still refunded, by the Beat sweep, once."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    booking.refresh_from_db()
    gateway.refund_error = refused("api_error")
    assert post_event(client, succeeded_event(booking)) == 200
    # The settle task's last attempt.
    with pytest.raises(StripeRequestError):
        checkout.settle_failed_checkout(str(booking.pk))
    gateway.refund_error = None

    # Inside the grace period the task's own retries are still running.
    age(booking, 5)
    assert checkout.sweep_unsettled_checkouts() == 0
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 1

    booking.refresh_from_db()
    assert booking.late_payment_refund_id
    # Settled: the next sweep leaves it alone.
    assert checkout.sweep_unsettled_checkouts() == 0


def test_the_sweep_cancels_an_uncancelled_checkout_once(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    age(booking, 45)

    assert checkout.sweep_unsettled_checkouts() == 1

    assert len(gateway.cancels) == 1
    assert checkout.sweep_unsettled_checkouts() == 0


def test_a_payment_intent_stripe_already_cancelled_leaves_the_sweep(
    gateway: FakeGateway,
) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    age(booking, 45)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="canceled")

    assert checkout.sweep_unsettled_checkouts() == 1

    assert BookingEvent.objects.filter(booking=booking, type="payment_intent_cancelled").exists()
    assert checkout.sweep_unsettled_checkouts() == 0


def test_refunds_owed_come_before_cancels_in_the_sweep(
    client: Client, gateway: FakeGateway
) -> None:
    owed, _ = two_offers()
    refund_booking = accept(owed).booking
    withdraw(owed)
    refund_booking.refresh_from_db()
    assert post_event(client, succeeded_event(refund_booking)) == 200
    cancel_only, _ = two_offers(("ana", "luka"))
    cancel_booking = accept(cancel_only).booking
    withdraw(cancel_only)
    age(cancel_booking, 60)
    age(refund_booking, 45)

    assert checkout.sweep_unsettled_checkouts(limit=1) == 1

    refund_booking.refresh_from_db()
    assert refund_booking.late_payment_refund_id
    assert gateway.cancels == []


def test_a_cancel_stripe_deferred_while_processing_is_retried_with_a_new_key(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Review R1-1: Stripe replays a key's first answer, so a cancel refused while the
    payment was processing is retried under the next version, and then succeeds."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = refused("payment_intent_unexpected_state", status="processing")
    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)
    cancelled = BookingEvent.objects.filter(type="payment_intent_cancelled")
    assert not cancelled.filter(booking=booking).exists()

    # The payment failed; the intent can be cancelled now. The sweep waits out the
    # backoff first, so a long SEPA debit isn't polled every 15 minutes.
    gateway.cancel_error = None
    age(booking, 45)
    assert checkout.sweep_unsettled_checkouts() == 0
    BookingEvent.objects.filter(booking=booking, type=checkout.CANCEL_DEFERRED).update(
        created_at=timezone.now() - dt.timedelta(hours=7)
    )
    assert checkout.sweep_unsettled_checkouts() == 1

    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"offer:{chosen.pk}:withdraw:v1",
        f"offer:{chosen.pk}:withdraw:v2",
    ]
    assert cancelled.filter(booking=booking).exists()
    assert checkout.sweep_unsettled_checkouts() == 0


def test_a_cancel_stripe_failed_on_its_side_is_retried_with_a_new_key(
    gateway: FakeGateway,
) -> None:
    """Stripe saves a 500 under the key as well; the retry must not replay it."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    gateway.cancel_error = StripeRequestError("test", code="api_error", http_status=500)
    with pytest.raises(StripeRequestError):
        checkout.settle_failed_checkout(str(booking.pk))
    gateway.cancel_error = None

    checkout.settle_failed_checkout(str(booking.pk))

    assert [call["idempotency_key"] for call in gateway.cancels] == [
        f"offer:{chosen.pk}:withdraw:v1",
        f"offer:{chosen.pk}:withdraw:v2",
    ]


def test_two_failed_checkouts_of_one_offer_cancel_with_their_own_keys(
    gateway: FakeGateway, django_capture_on_commit_callbacks: Any
) -> None:
    """Review R1-1: an offer reopened by a failed checkout is accepted again and then
    withdrawn. Each checkout's PaymentIntent is cancelled under its own key."""
    chosen, _ = two_offers()
    gateway.client_secret = None
    with pytest.raises(ApiError):
        accept(chosen)
    first = Booking.objects.get()
    gateway.client_secret = "secret_test"
    second = accept(chosen).booking
    withdraw(chosen)

    with django_capture_on_commit_callbacks(execute=True):
        publish(WITHDRAWN_TOPIC, chosen)
        checkout.settle_failed_checkout(str(first.pk))

    keys = {call["payment_intent"]: call["idempotency_key"] for call in gateway.cancels}
    assert keys == {
        intent_id(str(first.pk)): f"booking:{first.pk}:checkout_cancel:v1",
        intent_id(str(second.pk)): f"offer:{chosen.pk}:withdraw:v1",
    }
    for booking in (first, second):
        assert BookingEvent.objects.filter(
            booking=booking, type="payment_intent_cancelled"
        ).exists()


def test_the_sweep_refunds_a_payment_that_came_long_after_the_failure(
    client: Client, gateway: FakeGateway
) -> None:
    """Review R1-5: the lookback counts from the late payment, not from the failure."""
    chosen, _ = two_offers()
    booking = accept(chosen).booking
    withdraw(chosen)
    booking.refresh_from_db()
    gateway.refund_error = refused("api_error")
    assert post_event(client, succeeded_event(booking)) == 200
    gateway.refund_error = None
    age(booking, 60 * 24 * 40)
    BookingEvent.objects.filter(booking=booking, type="late_payment").update(
        created_at=timezone.now() - dt.timedelta(minutes=45)
    )

    assert checkout.sweep_unsettled_checkouts() == 1

    booking.refresh_from_db()
    assert booking.late_payment_refund_id


def test_the_sweep_runs_on_beat() -> None:
    from django.conf import settings

    tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert "bookings.checkout.sweep_unsettled" in tasks


def test_settling_never_touches_a_live_checkout(gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    booking = accept(chosen).booking

    checkout.settle_failed_checkout(str(booking.pk))

    assert (gateway.cancels, gateway.refunds) == ([], [])


# Over GraphQL.

ACCEPT = """
mutation($offerId: ID!, $price: Int!, $total: Int!, $consent: CheckoutConsentInput!) {
  acceptOffer(
    offerId: $offerId, expectedPriceCents: $price, expectedTotalCents: $total,
    expectedFeeScheduleVersion: "1", consent: $consent
  ) {
    id clientSecret
    booking { id taskId offerId status amountCents feeCents totalCents slotStart
              termsVersion cancellationVersion createdAt updatedAt }
  }
}
"""


def consent_input(**changes: Any) -> dict[str, Any]:
    return {
        "cancellationPolicyAccepted": True,
        "earlyStartRequested": True,
        "cancellationVersion": CANCELLATION,
        "platform": "IOS",
        "language": "DE",
        "variant": "PRIVATE_HELPER",
        **changes,
    }


def test_accept_offer_returns_the_checkout_session(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    token = sign_in(chosen.task.poster).access_token

    body = gql(
        client,
        ACCEPT,
        {"offerId": str(chosen.pk), "price": 3_000, "total": 3_360, "consent": consent_input()},
        token=token,
    )

    session = body["data"]["acceptOffer"]
    booking = Booking.objects.get()
    assert session["id"] == str(booking.pk)
    assert session["clientSecret"] == f"{intent_id(str(booking.pk))}_secret_test"
    assert session["booking"]["status"] == "AWAITING_PAYMENT"
    assert (
        session["booking"]["amountCents"],
        session["booking"]["feeCents"],
        session["booking"]["totalCents"],
    ) == (3_000, 360, 3_360)
    assert session["booking"]["cancellationVersion"] == CANCELLATION


def test_accept_offer_without_consent_is_refused(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()
    token = sign_in(chosen.task.poster).access_token

    body = gql(
        client,
        ACCEPT,
        {
            "offerId": str(chosen.pk),
            "price": 3_000,
            "total": 3_360,
            "consent": consent_input(earlyStartRequested=False),
        },
        token=token,
    )

    assert error_codes(body) == ["CONSENT_REQUIRED"]
    assert gateway.charges == []


def test_accept_offer_needs_a_signed_in_poster(client: Client, gateway: FakeGateway) -> None:
    chosen, _ = two_offers()

    body = gql(
        client,
        ACCEPT,
        {"offerId": str(chosen.pk), "price": 3_000, "total": 3_360, "consent": consent_input()},
    )

    assert error_codes(body) == ["UNAUTHENTICATED"]


# Races (transactional: real row locks across connections).


def race(*actions: Callable[[], object]) -> list[str]:
    results: list[str] = []
    barrier = threading.Barrier(len(actions))

    def run(action: Callable[[], object]) -> None:
        try:
            barrier.wait(5)
            action()
            results.append("ok")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.django_db(transaction=True)
def test_a_double_accept_books_and_charges_once(gateway: FakeGateway) -> None:
    """A double tap, or two devices: one checkout, one PaymentIntent."""
    chosen, _ = two_offers()

    results = race(lambda: accept(chosen), lambda: accept(chosen))

    assert sorted(results) == sorted(["ok", ErrorCode.TASK_ALREADY_BOOKED.name])
    assert Booking.objects.count() == 1
    assert len(gateway.charges) == 1
    assert ConsentRecord.objects.count() == 1
    assert TermsAcceptance.objects.filter(booking__isnull=False).count() == 1


@pytest.mark.django_db(transaction=True)
def test_accepting_two_offers_at_once_books_one(gateway: FakeGateway) -> None:
    chosen, other = two_offers()

    results = race(lambda: accept(chosen), lambda: accept(other))

    assert sorted(results) == sorted(["ok", ErrorCode.TASK_ALREADY_BOOKED.name])
    assert Booking.objects.count() == 1
    assert len(gateway.charges) == 1
