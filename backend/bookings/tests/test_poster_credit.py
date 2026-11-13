"""Spending the poster's EUR 5 credit (BEAV-123; user decision 2026-11-07; hard rules b
and g): it comes off TaskBeavo's service fee only, the rest carries over, the helper's
amount never changes, and a failed checkout or any cancellation or split gives it back."""

import copy
import datetime as dt
import threading
import time
from typing import Any

import pytest
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.test_schema import gql
from bookings import (
    cancel,
    cancellation,
    checkout,
    credit,
    helper_cancel,
    settlement,
    state_machine,
)
from bookings.cancel import PosterCancelReason, cancel_booking
from bookings.cancellation import NoFaultReason
from bookings.helper_cancel import HelperCancelReason
from bookings.models import (
    Booking,
    BookingEvent,
    CancellationSettlement,
    PosterCredit,
    PosterCreditUse,
)
from bookings.queries import active_booking_of_helper
from bookings.tests.factories import checkout_fee_version, stripe_event
from bookings.tests.fakes import FakeGateway, FakeSettlementStripe
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    consent,
    legal_documents,
    make_trader,
    post_event,
    succeeded_event,
    webhook_secret,
)
from bookings.tests.test_poster_cancel import (
    accepted_hours_ago,
    code,
    helper,
    paid,
    poster,
    refunded_event,
    starting,
    token,
)
from core.errors import ApiError
from core.models import ActorKind, Outbox
from offers import services as offers
from offers.models import Offer
from offers.tests.factories import make_helper, open_task
from payments.stripe_client import StripeIdError, StripeRequestError
from payments.tests.stripe_fakes import load_event
from staff.roles import Role
from staff.tests.factories import make_staff
from tasks.models import Task

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)


def refund_confirmed(
    client: Client, booking: Booking, stripe: FakeSettlementStripe, event_id: str
) -> Booking:
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    booking.refresh_from_db()
    refund = CancellationSettlement.objects.get(booking=booking).refund_cents
    assert post_event(client, refunded_event(booking, refund, event_id)) == 200
    booking.refresh_from_db()
    return booking


def credited(client: Client) -> User:
    """A poster whose helper cancelled late: Stripe confirmed the refund, EUR 5 credit."""
    booking = starting(paid(client), 20 * H)
    helper_cancel.cancel_as_helper(
        helper(booking), booking.pk, HelperCancelReason.ILL, now=booking.created_at + 2 * H
    )
    refund_confirmed(client, booking, FakeSettlementStripe(), "evt_test_credit")
    assert PosterCredit.objects.get(source_booking=booking).amount_cents == 500
    return poster(booking)


_names = iter(f"credithelper{n}" for n in range(100))


def offer_for(me: User, price: int = 3_000) -> Offer:
    task = open_task(me)
    return offers.send_offer(make_helper(next(_names)), str(task.pk), price)


def accept_now(
    offer: Offer, *, total: int | None = None, variant: str = "private"
) -> checkout.CheckoutSession:
    """Accept with the total the pay button shows now, the credit included."""
    me = offer.task.poster
    fee = state_machine.service_fee_cents(offer.price_cents)
    shown = offer.price_cents + fee - credit.credit_for_fee(me, fee)
    return checkout.accept_offer(
        me,
        str(offer.pk),
        expected_price_cents=offer.price_cents,
        expected_total_cents=shown if total is None else total,
        expected_fee_schedule_version=checkout_fee_version(),
        consent=consent(variant=variant),
    )


def paid_now(client: Client, offer: Offer, *, variant: str = "private") -> Booking:
    booking = accept_now(offer, variant=variant).booking
    booking.refresh_from_db()
    assert post_event(client, succeeded_event(booking, f"evt_test_{booking.pk.hex}")) == 200
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    return booking


def test_the_credit_comes_off_the_fee_and_never_off_the_helpers_amount(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)

    booking = accept_now(offer_for(me)).booking

    assert (booking.amount_cents, booking.fee_cents, booking.credit_cents) == (3_000, 0, 360)
    charge = gateway.charges[-1]
    # The poster pays the task price; the helper still receives all of it.
    assert (charge["amount_cents"], charge["application_fee_cents"]) == (3_000, 0)
    assert credit.available_cents(me) == 140


def test_the_rest_carries_over_to_the_next_booking(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)
    first = accept_now(offer_for(me)).booking

    second = accept_now(offer_for(me)).booking

    assert (second.fee_cents, second.credit_cents) == (220, 140)
    assert gateway.charges[-1]["application_fee_cents"] == 220
    assert credit.available_cents(me) == 0
    uses = PosterCreditUse.objects.order_by("used_at", "pk")
    assert [(u.booking_id, u.amount_cents) for u in uses] == [(first.pk, 360), (second.pk, 140)]
    third = accept_now(offer_for(me)).booking
    assert (third.fee_cents, third.credit_cents) == (360, 0)


def test_a_total_without_the_credit_is_refused(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)

    with pytest.raises(ApiError) as refused:
        accept_now(offer_for(me), total=3_360)

    assert code(refused) == "FEE_CHANGED"
    assert credit.available_cents(me) == 500
    assert not PosterCreditUse.objects.exists()


def test_a_failed_checkout_gives_the_credit_back(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)
    gateway.charge_error = StripeRequestError("payment_intents.create", code="card_declined")

    with pytest.raises(ApiError):
        accept_now(offer_for(me))

    booking = Booking.objects.get(poster=me, status=S.PAYMENT_FAILED)
    assert booking.credit_cents == 360
    assert PosterCreditUse.objects.get(booking=booking).restored_at is not None
    assert credit.available_cents(me) == 500


def test_an_abandoned_checkout_gives_the_credit_back(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)
    booking = accept_now(offer_for(me)).booking

    checkout.expire_checkout(str(booking.pk), now=booking.created_at + 2 * H)

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_FAILED
    assert credit.available_cents(me) == 500


def test_a_full_refund_gives_the_credit_back(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)
    booking = starting(paid_now(client, offer_for(me)), 72 * H)
    cancel_booking(me, booking.pk, PosterCancelReason.NO_LONGER_NEEDED)
    stripe = FakeSettlementStripe(fee_cents=0)

    done = refund_confirmed(client, booking, stripe, "evt_test_full")

    # No fee was charged, so there is no fee refund step; the poster gets the charge back.
    assert stripe.sent() == ["transfer_reversal", "refund"]
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (0, 0, 3_000)
    assert done.status == S.REFUNDED
    assert credit.available_cents(me) == 500


def test_a_late_cancel_gives_the_credit_back_and_taskbeavo_keeps_the_full_rate(
    client: Client, gateway: FakeGateway
) -> None:
    """Rule g (user decisions 2026-11-07): the fee the credit paid is refunded, so the credit
    comes back in full, and TaskBeavo still keeps 12% of C: the part the €0 fee didn't
    cover comes back from the helper's account with the reversal."""
    me = credited(client)
    booking = accepted_hours_ago(paid_now(client, offer_for(me)), 2, starts_in=10 * H)
    cancel_booking(me, booking.pk, PosterCancelReason.NO_LONGER_NEEDED)
    stripe = FakeSettlementStripe(fee_cents=0)

    done = refund_confirmed(client, booking, stripe, "evt_test_late")

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.helper_compensation_cents, s.kept_fee_cents, s.refund_cents) == (750, 90, 2_160)
    assert stripe.sent() == ["transfer_reversal", "refund"]
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (750, 90, 2_160)
    assert done.status == S.PARTIALLY_SETTLED
    assert credit.available_cents(me) == 500


def test_a_helper_cancel_gives_the_spent_credit_back_too(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    booking = starting(paid_now(client, offer_for(me)), 72 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, HelperCancelReason.ILL)

    refund_confirmed(client, booking, FakeSettlementStripe(fee_cents=0), "evt_test_hc")

    assert credit.available_cents(me) == 500


def test_a_repeated_refund_webhook_gives_the_credit_back_once(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    booking = accepted_hours_ago(paid_now(client, offer_for(me)), 2, starts_in=10 * H)
    cancel_booking(me, booking.pk, PosterCancelReason.NO_LONGER_NEEDED)
    stripe = FakeSettlementStripe(fee_cents=0)
    refund_confirmed(client, booking, stripe, "evt_test_once")

    refund = CancellationSettlement.objects.get(booking=booking).refund_cents
    assert post_event(client, refunded_event(booking, refund, "evt_test_once_again")) == 200

    assert credit.available_cents(me) == 500
    assert BookingEvent.objects.filter(booking=booking, type=credit.RESTORED).count() == 1


def test_a_release_keeps_the_credit_spent(client: Client, gateway: FakeGateway) -> None:
    me = credited(client)
    booking = paid_now(client, offer_for(me))
    state_machine.transition(
        booking.pk, S.PAYMENT_REQUESTED, actor_kind=ActorKind.HELPER, actor=helper(booking)
    )

    state_machine.transition(booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=me)

    assert credit.available_cents(me) == 140
    assert PosterCreditUse.objects.get(booking=booking).restored_at is None


def test_a_partly_credited_late_cancel_keeps_the_usual_share(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    accept_now(offer_for(me))  # takes 360 of the 500
    booking = accepted_hours_ago(paid_now(client, offer_for(me)), 2, starts_in=10 * H)
    assert (booking.fee_cents, booking.credit_cents) == (220, 140)

    cancel_booking(me, booking.pk, PosterCancelReason.NO_LONGER_NEEDED)

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.fee_cents, s.helper_compensation_cents, s.kept_fee_cents, s.refund_cents) == (
        220,
        750,
        90,
        2_380,
    )


def test_a_no_fault_cancel_gives_the_credit_back(client: Client, gateway: FakeGateway) -> None:
    from bookings import no_fault

    me = credited(client)
    booking = starting(paid_now(client, offer_for(me)), 72 * H)
    no_fault.request_no_fault(me, booking.pk, NoFaultReason.WEATHER)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=True)

    refund_confirmed(client, booking, FakeSettlementStripe(fee_cents=0), "evt_test_nf")

    assert credit.available_cents(me) == 500


OFFERS = """query($id: ID!) { taskOffers(taskId: $id) {
  id serviceFeeCents serviceFeeCreditCents totalCents
} }"""
MY_OFFERS = "{ myOffers { id serviceFeeCreditCents totalCents } }"
BOOKING = "query($id: ID!) { booking(id: $id) { id feeCents creditCents totalCents } }"


def mine_or(body: dict) -> list[dict]:
    return body["data"]["myOffers"]


def test_the_poster_sees_the_credit_line_and_the_helper_doesnt(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    offer = offer_for(me)

    mine = gql(client, OFFERS, {"id": str(offer.task_id)}, token=token(me))
    theirs = gql(client, MY_OFFERS, token=token(offer.helper))

    assert mine["data"]["taskOffers"] == [
        {
            "id": str(offer.pk),
            "serviceFeeCents": 360,
            "serviceFeeCreditCents": 360,
            "totalCents": 3_000,
        }
    ]
    helper_view = next(o for o in mine_or(theirs) if o["id"] == str(offer.pk))
    assert (helper_view["serviceFeeCreditCents"], helper_view["totalCents"]) == (0, 3_360)
    booking = accept_now(offer).booking
    seen = gql(client, BOOKING, {"id": str(booking.pk)}, token=token(me))["data"]["booking"]
    assert (seen["feeCents"], seen["creditCents"], seen["totalCents"]) == (0, 360, 3_000)
    other = gql(client, BOOKING, {"id": str(booking.pk)}, token=token(offer.helper))
    # The helper sees the schedule's fee: the poster's credit isn't theirs to see.
    assert (
        other["data"]["booking"]["creditCents"],
        other["data"]["booking"]["feeCents"],
        other["data"]["booking"]["totalCents"],
    ) == (None, 360, 3_360)


class BadFeeIdStripe(FakeSettlementStripe):
    """Stripe answered with a fee ID that fails its shape check."""

    def refund_application_fee(self, **kwargs: object):  # type: ignore[override]
        raise StripeIdError("invalid application fee ID")


def test_a_stored_id_that_fails_its_check_goes_to_support_instead_of_retrying(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client), 2, starts_in=10 * H)
    cancel_booking(poster(booking), booking.pk, PosterCancelReason.NO_LONGER_NEEDED)

    settlement.settle(booking.pk, gateway=BadFeeIdStripe())  # pyright: ignore[reportArgumentType]

    s = CancellationSettlement.objects.get(booking=booking)
    assert s.refused_at is not None and s.refused_step == "fee_refund"


# --- The credit comes back in full after every cancellation and split, and is spent only
# on a release (user decision 2026-11-07, review round 2). ---


@pytest.mark.parametrize(("work_done", "refund"), [(50, 1_320), (80, 312)])
def test_a_withdrawal_split_gives_the_whole_credit_back(
    client: Client, gateway: FakeGateway, work_done: int, refund: int
) -> None:
    """User decision 2026-11-07: TaskBeavo takes its K in cash through the reversal, so
    the whole credit comes back. A fully credited EUR 30 task, 50% done: the helper keeps
    15.00, TaskBeavo 1.80, and the poster gets 13.20 back plus the whole 3.60 credit."""
    me = credited(client)
    offer = offer_for(me)
    make_trader(offer)
    booking = starting(paid_now(client, offer, variant="trader"), 5 * H)
    cancel.withdraw_from_contract(me, booking.pk, now=booking.slot_start + H)
    cancel.settle_withdrawal(make_staff(Role.SUPPORT), booking.pk, work_done)

    done = refund_confirmed(client, booking, FakeSettlementStripe(fee_cents=0), "evt_test_split")

    assert done.status == S.PARTIALLY_SETTLED
    assert CancellationSettlement.objects.get(booking=booking).refund_cents == refund
    assert credit.available_cents(me) == 500
    assert PosterCreditUse.objects.get(booking=booking).restored_cents == 360


def test_a_full_refund_after_a_dispute_gives_the_whole_credit_back(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    booking = paid_now(client, offer_for(me))
    state_machine.transition(booking.pk, S.DISPUTED, actor_kind=ActorKind.POSTER, actor=me)

    # Support decides a dispute for the poster outside the app for now; Stripe's refund
    # confirms it, and the state machine gives the credit back with the full refund.
    with transaction.atomic():
        state_machine.apply_stripe_transition(
            booking.pk, S.REFUNDED, stripe_event_id=stripe_event()
        )

    assert credit.available_cents(me) == 500


def test_every_cancellation_and_split_gives_the_whole_credit_back() -> None:
    """The credit is spent only on a release; every cancellation case, the withdrawal split
    and (when the app computes them) dispute splits give all of it back."""
    for work_done in (0, 50, 99):
        split = cancellation.pro_rata_withdrawal_outcome(
            amount_cents=3_000, fee_cents=0, fee_percent=12, work_done_percent=work_done
        )
        assert cancellation.credit_back_cents(split, 360) == 360


# --- A split that leaves the poster no refund (R = 0): transfer.reversed confirms it
# (user decision 2026-11-07, hard rule a). ---


def withdrawn_at(client: Client, work_done: int) -> tuple[User, Booking]:
    """A withdrawal after the start on a booking the poster's credit paid the fee of, with
    support's share entered."""
    me = credited(client)
    offer = offer_for(me)
    make_trader(offer)
    booking = starting(paid_now(client, offer, variant="trader"), 5 * H)
    cancel.withdraw_from_contract(me, booking.pk, now=booking.slot_start + H)
    cancel.settle_withdrawal(make_staff(Role.SUPPORT), booking.pk, work_done)
    return me, booking


def reversed_event(
    booking: Booking,
    amount: int,
    event_id: str,
    *,
    ours: bool = True,
    reversal_id: str = "trr_test1",
) -> dict[str, Any]:
    """transfer.reversed as Stripe sent it in the sandbox (fixture), for the fake's
    transfer: TaskBeavo's reversal carries the booking in its metadata and key."""
    event = copy.deepcopy(load_event("transfer.reversed"))
    event["id"] = event_id
    transfer = event["data"]["object"]
    transfer.update({"id": "tr_test1", "amount": booking.amount_cents, "amount_reversed": amount})
    transfer["reversals"]["data"][0].update(
        {
            "id": reversal_id,
            "amount": amount,
            "transfer": "tr_test1",
            "metadata": (
                {"booking_id": str(booking.pk), "action": "transfer_reversal"} if ours else {}
            ),
        }
    )
    event["request"]["idempotency_key"] = (
        f"booking:{booking.pk}:cancel:transfer_reversal:v1" if ours else None
    )
    return event


def confirmed_once(booking: Booking) -> bool:
    return (
        BookingEvent.objects.filter(booking=booking, type=settlement.CONFIRMED).count() == 1
        and Outbox.objects.filter(
            topic=settlement.SETTLED_TOPIC, aggregate_id=str(booking.pk)
        ).count()
        == 1
    )


def test_a_split_with_no_refund_is_confirmed_by_the_transfer_reversal(
    client: Client, gateway: FakeGateway
) -> None:
    """99% done on a fully credited booking: the helper keeps 2,970 and K takes the last
    30, so no refund. The reversal of 30 is the last step; its webhook settles the booking
    and gives back the whole credit."""
    me, booking = withdrawn_at(client, 99)
    stripe = FakeSettlementStripe(fee_cents=0)

    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]

    assert stripe.sent() == ["transfer_reversal"]
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (2_970, 30, 0)
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED  # not until Stripe confirms
    assert booking.pk not in settlement.unsettled(now=booking.updated_at + 10 * H)

    assert post_event(client, reversed_event(booking, 30, "evt_test_reversed")) == 200

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert CancellationSettlement.objects.get(booking=booking).confirmed_at is not None
    assert confirmed_once(booking)
    assert credit.available_cents(me) == 500
    # Nothing more to send once confirmed.
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    assert stripe.sent() == ["transfer_reversal"]


def test_a_repeated_reversal_webhook_settles_once(client: Client, gateway: FakeGateway) -> None:
    me, booking = withdrawn_at(client, 99)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]

    event = reversed_event(booking, 30, "evt_test_reversed_once")
    assert post_event(client, event) == 200
    assert post_event(client, event) == 200  # the same event again: stripe_events dedupes
    assert post_event(client, reversed_event(booking, 30, "evt_test_reversed_again")) == 200

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert confirmed_once(booking)
    assert PosterCreditUse.objects.get(booking=booking).restored_cents == 360
    assert credit.available_cents(me) == 500


def test_the_reversal_webhook_may_beat_the_record_of_the_reversal(
    client: Client, gateway: FakeGateway
) -> None:
    """Stripe made the reversal but its answer never reached `settle` (a network error):
    the webhook arrives first, records the reversal and settles; the retry sends nothing."""
    _, booking = withdrawn_at(client, 99)
    stripe = FakeSettlementStripe(
        fee_cents=0,
        fail="transfer_reversal",
        fail_with=StripeRequestError("transfer_reversal"),
        fail_after_work=True,
    )
    with pytest.raises(StripeRequestError):
        settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    assert CancellationSettlement.objects.get(booking=booking).transfer_reversal_id == ""

    assert post_event(client, reversed_event(booking, 30, "evt_test_reversed_first")) == 200

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert CancellationSettlement.objects.get(booking=booking).transfer_reversal_id == "trr_test1"
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    assert stripe.sent() == ["transfer_reversal"]
    assert stripe.helper == 2_970  # moved once


def test_a_reversal_short_of_x_waits_and_more_goes_to_support(
    client: Client, gateway: FakeGateway
) -> None:
    _, booking = withdrawn_at(client, 99)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]

    assert post_event(client, reversed_event(booking, 10, "evt_test_reversed_short")) == 200
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED

    assert post_event(client, reversed_event(booking, 31, "evt_test_reversed_more")) == 200
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED
    assert BookingEvent.objects.filter(
        booking=booking, data__reason="settlement_reversal_mismatch"
    ).exists()


def test_a_reversal_that_isnt_taskbeavos_step_confirms_nothing(
    client: Client, gateway: FakeGateway
) -> None:
    """Same transfer and amount, but neither the metadata nor the key of the settlement's
    reversal (made by hand in the dashboard, say): it doesn't settle the booking."""
    _, booking = withdrawn_at(client, 99)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]

    assert post_event(client, reversed_event(booking, 30, "evt_test_not_ours", ours=False)) == 200

    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED


def test_with_a_refund_the_reversal_webhook_changes_nothing_and_charge_refunded_settles(
    client: Client, gateway: FakeGateway
) -> None:
    """R > 0, unchanged: the refund is sent after the reversal, so charge.refunded is the
    event that completes the settlement; transfer.reversed alone doesn't."""
    me, booking = withdrawn_at(client, 50)
    stripe = FakeSettlementStripe(fee_cents=0)
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    split = CancellationSettlement.objects.get(booking=booking)
    assert stripe.sent() == ["transfer_reversal", "refund"]

    reversal = reversed_event(booking, split.transfer_reversal_cents, "evt_test_x_first")
    assert post_event(client, reversal) == 200
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED

    assert post_event(client, refunded_event(booking, split.refund_cents, "evt_test_r")) == 200
    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert confirmed_once(booking)
    assert credit.available_cents(me) == 500


@pytest.mark.parametrize("fee", [0, 50, 360])
def test_every_share_support_can_enter_reverses_something(fee: int) -> None:
    """Support enters 0-99% (`pro_rata_withdrawal_outcome`), so the helper never keeps the
    whole price: every split has a transfer reversal, whose webhook can confirm it when
    there is no refund. No split is left without an event to confirm it."""
    for work_done in range(100):
        outcome = cancellation.pro_rata_withdrawal_outcome(
            amount_cents=3_000, fee_cents=fee, fee_percent=12, work_done_percent=work_done
        )
        assert outcome.transfer_reversal_cents > 0


# --- A settlement sent to Stripe that no webhook confirms in 24 h goes to support. ---


@pytest.mark.parametrize("work_done", [50, 99])  # with a refund (R > 0) and without (R = 0)
def test_a_settlement_no_webhook_confirms_in_a_day_flags_support_once(
    client: Client,
    gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Any,
    work_done: int,
) -> None:
    _, booking = withdrawn_at(client, work_done)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]
    sent = CancellationSettlement.objects.get(booking=booking)
    last_sent = sent.refund_sent_at if sent.refund_cents else sent.transfer_reversal_sent_at
    assert last_sent is not None

    assert settlement.flag_unconfirmed(now=last_sent + 23 * H) == 0
    with django_capture_on_commit_callbacks(execute=True):  # alerts go out on commit
        assert settlement.flag_unconfirmed(now=last_sent + 25 * H) == 1
    assert settlement.flag_unconfirmed(now=last_sent + 50 * H) == 0

    flags = BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=settlement.UNCONFIRMED
    )
    step = "refund" if sent.refund_cents else "transfer_reversal"
    assert [f.data for f in flags] == [{"reason": settlement.UNCONFIRMED, "step": step}]
    sentry = [r for r in caplog.records if r.name == "bookings.support" and r.levelname == "ERROR"]
    assert [getattr(r, "support.reason") for r in sentry] == [settlement.UNCONFIRMED]
    assert getattr(sentry[0], "booking.id") == str(booking.pk)


def test_an_unsent_or_confirmed_settlement_is_not_flagged(
    client: Client, gateway: FakeGateway
) -> None:
    _, booking = withdrawn_at(client, 99)
    later = timezone.now() + 48 * H
    # Not sent yet: re-sending is the sweep's job (`unsettled`), not this alert's.
    assert settlement.flag_unconfirmed(now=later) == 0

    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]
    assert post_event(client, reversed_event(booking, 30, "evt_test_flag_confirmed")) == 200

    assert settlement.flag_unconfirmed(now=later) == 0


@pytest.mark.parametrize("moved_on", ["refused", "disputed"])
def test_a_refused_or_disputed_settlement_is_not_flagged_as_unconfirmed(
    client: Client, gateway: FakeGateway, moved_on: str
) -> None:
    """A refusal already went to support, and a chargeback took the booking out of
    settling: neither needs this alert."""
    _, booking = withdrawn_at(client, 50)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]
    if moved_on == "refused":
        CancellationSettlement.objects.filter(booking=booking).update(refused_at=timezone.now())
    else:
        with transaction.atomic():  # a chargeback (charge.dispute.created)
            state_machine.apply_stripe_transition(
                booking.pk, S.DISPUTED, stripe_event_id=stripe_event()
            )
        booking.refresh_from_db()
        assert booking.status == S.DISPUTED

    assert settlement.flag_unconfirmed(now=timezone.now() + 48 * H) == 0


def test_the_done_screen_promises_the_whole_credit_while_support_works_out_the_share(
    client: Client, gateway: FakeGateway
) -> None:
    me = credited(client)
    offer = offer_for(me)
    make_trader(offer)
    booking = starting(paid_now(client, offer, variant="trader"), 5 * H)
    cancel.withdraw_from_contract(me, booking.pk, now=booking.slot_start + H)
    query = (
        "query($id: ID!) { booking(id: $id) {"
        " id cancellation { id awaitingSupport creditBackCents } } }"
    )

    as_poster = gql(client, query, {"id": str(booking.pk)}, token=token(me))
    as_helper = gql(client, query, {"id": str(booking.pk)}, token=token(helper(booking)))

    assert as_poster["data"]["booking"]["cancellation"]["awaitingSupport"] is True
    assert as_poster["data"]["booking"]["cancellation"]["creditBackCents"] == 360
    assert as_helper["data"]["booking"]["cancellation"]["creditBackCents"] is None


def test_a_bug_in_the_amounts_is_not_mistaken_for_a_bad_stripe_id(
    client: Client, gateway: FakeGateway
) -> None:
    """R1-8: only a Stripe ID's shape check is refused to support; any other ValueError
    surfaces."""
    me = credited(client)
    booking = accepted_hours_ago(paid_now(client, offer_for(me)), 2, starts_in=10 * H)
    cancel_booking(me, booking.pk, PosterCancelReason.NO_LONGER_NEEDED)

    class BrokenStripe(FakeSettlementStripe):
        def retrieve_charge_parts(self, **kwargs: Any) -> Any:
            raise ValueError("a bug")

    with pytest.raises(ValueError, match="a bug"):
        settlement.settle(booking.pk, gateway=BrokenStripe(fee_cents=0))  # pyright: ignore[reportArgumentType]


@pytest.mark.django_db(transaction=True)
def test_two_checkouts_at_once_never_spend_more_credit_than_there_is(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-12: the poster's EUR 5 can't pay two 360 fees. The second checkout waits on the
    credit lock, finds 140 left, and its total no longer matches: refused, nothing spent."""
    # Commits run their on_commit work here: settle against the fake, never Stripe.
    monkeypatch.setattr(settlement, "get_gateway", FakeSettlementStripe)
    me = credited(client)
    offers_ = [offer_for(me), offer_for(me)]
    first_locked = threading.Event()
    original = credit.take
    results: list[str] = []

    def slow_take(poster: User, fee_cents: int) -> credit.Allocation:
        allocation = original(poster, fee_cents)
        if not first_locked.is_set():
            first_locked.set()
            time.sleep(0.3)
        return allocation

    def checkout_(offer: Offer) -> None:
        try:
            accept_now(offer, total=3_000)  # the total with the full credit taken off
            results.append("accepted")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    credit.take = slow_take  # type: ignore[assignment]
    try:
        first = threading.Thread(target=checkout_, args=(offers_[0],))
        first.start()
        assert first_locked.wait(5)
        second = threading.Thread(target=checkout_, args=(offers_[1],))
        second.start()
        first.join()
        second.join()
    finally:
        credit.take = original  # type: ignore[assignment]

    assert results.count("accepted") == 1
    assert sum(PosterCreditUse.objects.values_list("amount_cents", flat=True)) <= 500
    assert credit.available_cents(me) == 140


def test_a_withdrawal_ends_the_helpers_access_at_once(client: Client, gateway: FakeGateway) -> None:
    """Decision 1 (hard rule e): a withdrawal is a recorded cancel, so the helper loses the
    exact place before Stripe confirms the refund."""
    me = credited(client)
    offer = offer_for(me)
    make_trader(offer)
    booking = starting(paid_now(client, offer, variant="trader"), 72 * H)
    task = Task.objects.get(pk=booking.task_id)
    assert active_booking_of_helper(task, helper(booking)) is not None

    cancel.withdraw_from_contract(me, booking.pk)

    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED
    assert not booking.is_active
    assert active_booking_of_helper(task, helper(booking)) is None
