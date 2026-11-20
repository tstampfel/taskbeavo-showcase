"""Release payment, releasing less by agreement, payouts and tips (BEAV-117; PLAN.md, "The
adapted flow keeps Airtasker's release model"). Hard rule a: no money moves from a client
mutation alone, the payout and the tip go through Stripe, and only webhooks confirm
PAID_OUT, PARTIALLY_SETTLED and a tip. Hard rule b: manual payouts from the helper's own
account, the tip a separate destination charge with no fee. Hard rule c: every move
locked, audited and in the outbox. Rule g: a used credit is spent on a full release and
comes back on a partial one."""

import copy
import datetime as dt
import threading
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from bookings import arrival, payouts, release, settlement, state_machine, tips
from bookings.models import (
    Booking,
    BookingArrival,
    BookingEvent,
    BookingPayout,
    CancellationSettlement,
    PosterCredit,
    PosterCreditUse,
    ReleaseProposal,
    Tip,
)
from bookings.tests.fakes import FakeGateway, FakePayoutStripe, FakeSettlementStripe
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    helper_account,
    legal_documents,
    post_event,
    publish,
    webhook_secret,
)
from bookings.tests.test_payment_request import request, started
from bookings.tests.test_poster_cancel import code, helper, paid, poster, refunded_event
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from offers.tests.factories import make_helper
from payments.models import StripeEvent
from payments.stripe_client import StripeRequestError
from payments.tests.stripe_fakes import load_event

pytestmark = pytest.mark.django_db

S = Booking.Status
M = dt.timedelta(minutes=1)


@pytest.fixture
def stripe(payout_stripe: FakePayoutStripe) -> FakePayoutStripe:
    """The autouse in-memory Stripe for payouts and tips (bookings/tests/conftest.py)."""
    return payout_stripe


def requested(client: Client) -> Booking:
    """A booking whose helper requested payment: EUR 30 task, EUR 3.60 fee."""
    booking = started(paid(client))
    return request(booking)


def topics(booking: Booking) -> list[str]:
    return list(
        Outbox.objects.filter(aggregate_id=str(booking.pk))
        .order_by("pk")
        .values_list("topic", flat=True)
    )


def payout_of(booking: Booking, kind: str = BookingPayout.Kind.RELEASE) -> BookingPayout:
    return BookingPayout.objects.get(booking=booking, kind=kind)


def paid_event(booking: Booking, payout_id: str, **changes: Any) -> dict:
    event = copy.deepcopy(load_event("payout.paid"))
    event["id"] = changes.pop("event_id", f"evt_test_po_{payout_id}")
    event["account"] = changes.pop("account", helper_account(booking))
    event["data"]["object"].update(
        {
            "id": payout_id,
            "amount": booking.amount_cents,
            "metadata": {"booking_id": str(booking.pk), "action": "payout_release"},
            **changes,
        }
    )
    return event


# --- Full release. ---


def test_the_poster_releases_and_the_payout_of_the_price_is_due(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)

    released = release.release_payment(poster(booking), booking.pk)

    assert released.status == S.RELEASED
    assert released.released_by == ActorKind.POSTER and released.released_at is not None
    event = BookingEvent.objects.get(booking=booking, type="released")
    assert event.actor_kind == ActorKind.POSTER
    assert event.data["from"] == S.PAYMENT_REQUESTED
    payout = payout_of(booking)
    assert payout.amount_cents == booking.amount_cents == 3_000
    assert {
        state_machine.UPDATED_TOPIC,
        release.RELEASED_TOPIC,
        payouts.OPENED_TOPIC,
    } <= set(topics(booking))
    # Hard rule a: the mutation itself called no Stripe.
    assert stripe.payouts == []


def test_the_payout_goes_from_the_helpers_account_with_a_key_from_the_action(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)

    payouts.pay_out(payout_of(booking).pk)
    payouts.pay_out(payout_of(booking).pk)

    [call] = stripe.payouts
    assert call == {
        "booking_id": str(booking.pk),
        "connected_account": helper_account(booking),
        "amount_cents": 3_000,
        "kind": "release",
        "idempotency_key": f"booking:{booking.pk}:payout:release:v1",
    }
    assert payout_of(booking).payout_id == "po_test1"
    created = BookingEvent.objects.get(booking=booking, type=payouts.CREATED)
    assert created.data == {
        "payout": "po_test1",
        "kind": "RELEASE",
        "amount_cents": 3_000,
        "looked_up": False,
    }


def test_the_outbox_handler_sends_the_payout_after_commit(
    client: Client,
    gateway: FakeGateway,
    stripe: FakePayoutStripe,
    django_capture_on_commit_callbacks: Any,
    settings: Any,
) -> None:
    settings.CELERY_TASK_ALWAYS_EAGER = True
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)

    with django_capture_on_commit_callbacks(execute=True):
        publish(payouts.OPENED_TOPIC, booking)

    assert stripe.sent() == 1


def test_a_payout_that_went_through_but_answered_500_is_looked_up_not_resent(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)
    stripe.fail_with = StripeRequestError("payouts.create", http_status=500)
    stripe.fail_after_work = True

    with pytest.raises(StripeRequestError):
        payouts.pay_out(payout_of(booking).pk)
    payouts.pay_out(payout_of(booking).pk)

    assert stripe.sent() == 1
    assert len(stripe.payouts) == 1
    payout = payout_of(booking)
    assert payout.payout_id == "po_test1" and payout.key_version == 2
    assert BookingEvent.objects.get(booking=booking, type=payouts.CREATED).data["looked_up"]


def test_money_not_yet_available_waits_for_the_retry(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)
    stripe.fail_with = StripeRequestError(
        "payouts.create", code="balance_insufficient", http_status=400
    )

    with pytest.raises(StripeRequestError):
        payouts.pay_out(payout_of(booking).pk)

    payout = payout_of(booking)
    assert payout.refused_at is None and payout.payout_id == ""
    # Stripe would replay the stored balance_insufficient under v1 for 24 hours.
    assert payout.key_version == 2
    assert payouts.unsent(now=timezone.now() + dt.timedelta(hours=1)) == [payout.pk]
    payouts.pay_out(payout.pk)
    assert payout_of(booking).payout_id == "po_test1"
    assert len(stripe.lookups) == 1
    assert [c["idempotency_key"] for c in stripe.payouts] == [
        f"booking:{booking.pk}:payout:release:v1",
        f"booking:{booking.pk}:payout:release:v2",
    ]


def test_a_payout_waiting_over_a_week_is_flagged_once_and_still_retried(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)
    BookingPayout.objects.filter(booking=booking).update(
        created_at=timezone.now() - dt.timedelta(days=8)
    )
    for _ in range(2):
        stripe.fail_with = StripeRequestError(
            "payouts.create", code="balance_insufficient", http_status=400
        )
        with pytest.raises(StripeRequestError):
            payouts.pay_out(payout_of(booking).pk)

    assert payout_of(booking).refused_at is None
    flags = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert [f.data["reason"] for f in flags] == [payouts.REFUSED]


def test_a_refused_payout_goes_to_support(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)
    stripe.fail_with = StripeRequestError("payouts.create", code="account_closed", http_status=400)

    payouts.pay_out(payout_of(booking).pk)
    payouts.pay_out(payout_of(booking).pk)

    assert payout_of(booking).refused_at is not None
    assert len(stripe.payouts) == 1
    refused = BookingEvent.objects.get(booking=booking, type=payouts.REFUSED)
    assert refused.data["stripe_code"] == "account_closed"
    assert BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=payouts.REFUSED
    ).exists()
    assert payouts.unsent(now=timezone.now() + dt.timedelta(hours=1)) == []


def test_releasing_twice_releases_once(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)

    release.release_payment(poster(booking), booking.pk)
    again = release.release_payment(poster(booking), booking.pk)

    assert again.status == S.RELEASED
    assert BookingEvent.objects.filter(booking=booking, type="released").count() == 1
    assert BookingPayout.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_parallel_releases_make_one_payout(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    who = poster(booking)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def tap() -> None:
        try:
            barrier.wait()
            release.release_payment(who, booking.pk)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert BookingEvent.objects.filter(booking=booking, type="released").count() == 1
    assert BookingPayout.objects.filter(booking=booking).count() == 1
    payouts.pay_out(payout_of(booking).pk)
    payouts.pay_out(payout_of(booking).pk)
    assert stripe.sent() == 1


def test_only_the_poster_releases(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)

    for who in (helper(booking), make_helper("stranger")):
        with pytest.raises(ApiError) as exc:
            release.release_payment(who, booking.pk)
        assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value
    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_REQUESTED
    assert not BookingPayout.objects.exists()


def test_release_waits_for_the_payment_request(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = started(paid(client))

    with pytest.raises(ApiError) as exc:
        release.release_payment(poster(booking), booking.pk)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    assert not release.can_release(booking, poster(booking))
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


def test_release_waits_while_the_poster_has_a_late_arrival_to_answer(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = paid(client)
    start = booking.slot_start
    arrival.running_late(helper(booking), booking.pk, 60, now=start + 5 * M)
    request(booking, now=start + 10 * M)

    with pytest.raises(ApiError) as exc:
        release.release_payment(poster(booking), booking.pk, now=start + 20 * M)
    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value

    arrival.respond_to_late(poster(booking), booking.pk, keep=True, now=start + 21 * M)
    released = release.release_payment(poster(booking), booking.pk, now=start + 22 * M)
    assert released.status == S.RELEASED


def test_releasing_says_the_helper_came_and_closes_the_no_show_route(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)

    release.release_payment(poster(booking), booking.pk)

    row = BookingArrival.objects.get(booking=booking)
    assert row.poster_confirmed_came_at is not None
    came = BookingEvent.objects.get(booking=booking, type="came_confirmed")
    assert came.data["by_release"] is True
    booking.refresh_from_db()
    assert not arrival.can_confirm_no_show(booking, row)


def test_a_used_credit_stays_spent_on_a_full_release(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    credit = PosterCredit.objects.create(
        poster=poster(booking),
        source_booking=paid(client, helpers=("ana", "luka")),
        amount_cents=200,
        granted_at=timezone.now(),
    )
    PosterCreditUse.objects.create(
        credit=credit, booking=booking, amount_cents=200, used_at=timezone.now()
    )

    release.release_payment(poster(booking), booking.pk)

    assert PosterCreditUse.objects.get(booking=booking).restored_at is None


# --- payout.paid. ---


def test_payout_paid_makes_a_released_booking_paid_out_once(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)
    payouts.pay_out(payout_of(booking).pk)
    event = paid_event(booking, "po_test1")

    assert post_event(client, event) == 200
    assert post_event(client, event) == 200

    booking.refresh_from_db()
    assert booking.status == S.PAID_OUT
    [moved] = BookingEvent.objects.filter(booking=booking, type="paid_out")
    assert moved.actor_kind == ActorKind.STRIPE_WEBHOOK
    assert moved.data["stripe_event"] == event["id"]
    assert payout_of(booking).paid_at is not None
    assert BookingEvent.objects.filter(booking=booking, type=payouts.PAID).count() == 1
    assert StripeEvent.objects.get(pk=event["id"]).processed_at is not None


def test_a_payout_on_another_account_is_flagged_not_applied(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)

    assert post_event(client, paid_event(booking, "po_x", account="acct_1SomeoneElse")) == 200

    booking.refresh_from_db()
    assert booking.status == S.RELEASED
    assert BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason="payout_on_another_account"
    ).exists()


def test_a_client_cant_mark_a_booking_paid_out(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = requested(client)
    release.release_payment(poster(booking), booking.pk)

    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.PAID_OUT, actor_kind=ActorKind.POSTER, actor=poster(booking)
        )


# --- Releasing less, by agreement. ---


def proposed(client: Client, released_cents: int = 2_000) -> Booking:
    booking = requested(client)
    release.propose_partial_release(poster(booking), booking.pk, released_cents)
    return booking


def test_a_proposal_moves_no_money_and_asks_the_helper(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = proposed(client)

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_REQUESTED
    proposal = ReleaseProposal.objects.get(booking=booking)
    assert (proposal.status, proposal.released_cents) == (ReleaseProposal.Status.OPEN, 2_000)
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert not BookingPayout.objects.exists()
    event = BookingEvent.objects.get(booking=booking, type=release.PROPOSED)
    assert event.actor_kind == ActorKind.POSTER
    assert event.data == {"proposal": proposal.pk, "released_cents": 2_000}
    assert release.PROPOSAL_TOPIC in topics(booking)


@pytest.mark.parametrize("cents", [0, -5, 3_000, 4_000])
def test_a_proposal_is_more_than_nothing_and_less_than_the_price(
    client: Client, gateway: FakeGateway, cents: int
) -> None:
    booking = requested(client)

    with pytest.raises(ApiError) as exc:
        release.propose_partial_release(poster(booking), booking.pk, cents)

    assert code(exc) == ErrorCode.INVALID_RELEASE_AMOUNT.value
    assert not ReleaseProposal.objects.exists()


def test_one_open_proposal_at_a_time_and_the_poster_may_withdraw_it(
    client: Client, gateway: FakeGateway
) -> None:
    booking = proposed(client)
    with pytest.raises(ApiError):
        release.propose_partial_release(poster(booking), booking.pk, 2_500)

    release.withdraw_partial_release(poster(booking), booking.pk)
    release.propose_partial_release(poster(booking), booking.pk, 2_500)

    statuses = list(
        ReleaseProposal.objects.filter(booking=booking)
        .order_by("pk")
        .values_list("status", flat=True)
    )
    assert statuses == [ReleaseProposal.Status.WITHDRAWN, ReleaseProposal.Status.OPEN]
    with pytest.raises(ApiError) as exc:
        release.respond_to_partial_release(poster(booking), booking.pk, accept=True)
    assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value


def test_the_helper_declines_and_nothing_moves(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = proposed(client)

    release.respond_to_partial_release(helper(booking), booking.pk, accept=False)

    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_REQUESTED
    assert ReleaseProposal.objects.get(booking=booking).status == ReleaseProposal.Status.DECLINED
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    # The poster may still release in full.
    assert release.release_payment(poster(booking), booking.pk).status == S.RELEASED


def test_a_full_release_withdraws_an_open_proposal(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = proposed(client)

    release.release_payment(poster(booking), booking.pk)

    assert ReleaseProposal.objects.get(booking=booking).status == ReleaseProposal.Status.WITHDRAWN
    assert payout_of(booking).amount_cents == 3_000


def test_leaving_payment_requested_otherwise_closes_the_proposal(
    client: Client, gateway: FakeGateway
) -> None:
    booking = proposed(client)

    state_machine.transition(
        booking.pk, S.DISPUTED, actor_kind=ActorKind.POSTER, actor=poster(booking)
    )

    assert ReleaseProposal.objects.get(booking=booking).status == ReleaseProposal.Status.CLOSED


def test_with_no_answer_nothing_moves(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = proposed(client)

    assert payouts.unsent(now=timezone.now() + dt.timedelta(days=60)) == []
    assert settlement.unsettled(now=timezone.now() + dt.timedelta(days=60)) == []
    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_REQUESTED


def accepted(client: Client, released_cents: int = 2_000) -> Booking:
    booking = proposed(client, released_cents)
    release.respond_to_partial_release(helper(booking), booking.pk, accept=True)
    booking.refresh_from_db()
    return booking


def test_accepting_opens_the_settlement_of_the_agreed_part(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = accepted(client)

    assert booking.status == S.PAYMENT_REQUESTED
    s = CancellationSettlement.objects.get(booking=booking)
    # EUR 30 + 3.60: the helper keeps 20.00, TaskBeavo 12% of it (2.40), 11.20 back.
    assert (s.case, s.helper_compensation_cents, s.kept_fee_cents) == (
        "PARTIAL_RELEASE",
        2_000,
        240,
    )
    assert (s.refund_cents, s.transfer_reversal_cents, s.fee_refund_cents) == (1_120, 1_120, 120)
    assert settlement.OPENED_TOPIC in topics(booking)
    # Nothing at Stripe yet: the settlement runs from the outbox, after commit.
    assert stripe.payouts == []


def test_while_it_settles_neither_side_can_move_the_booking(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = accepted(client)

    with pytest.raises(ApiError):
        release.release_payment(poster(booking), booking.pk)
    with pytest.raises(ApiError):
        release.propose_partial_release(poster(booking), booking.pk, 1_000)
    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.DISPUTED, actor_kind=ActorKind.POSTER, actor=poster(booking)
        )


def test_a_partial_release_reconciles_to_the_cent_and_pays_out_c(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = accepted(client)
    money = FakeSettlementStripe(amount_cents=3_000, fee_cents=360)

    settlement.settle(booking.pk, gateway=money)  # pyright: ignore[reportArgumentType]

    assert money.sent() == ["fee_refund", "transfer_reversal", "refund"]
    assert [kwargs["idempotency_key"] for _, kwargs in money.calls] == [
        f"booking:{booking.pk}:cancel:fee_refund:v1",
        f"booking:{booking.pk}:cancel:transfer_reversal:v1",
        f"booking:{booking.pk}:cancel:refund:v1",
    ]
    # Helper C, TaskBeavo K, the poster R: every cent of the EUR 33.60.
    assert (money.helper, money.taskbeavo, money.refunded) == (2_000, 240, 1_120)
    booking.refresh_from_db()
    assert booking.status == S.PAYMENT_REQUESTED

    assert post_event(client, refunded_event(booking, 1_120)) == 200

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    payout = payout_of(booking)
    assert payout.amount_cents == 2_000 == money.helper
    payouts.pay_out(payout.pk)
    assert stripe.payouts[0]["amount_cents"] == 2_000
    assert stripe.payouts[0]["idempotency_key"] == f"booking:{booking.pk}:payout:release:v1"


def test_a_used_credit_comes_back_in_full_on_a_partial_release(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = accepted(client)
    credit = PosterCredit.objects.create(
        poster=poster(booking),
        source_booking=paid(client, helpers=("ana", "luka")),
        amount_cents=200,
        granted_at=timezone.now(),
    )
    PosterCreditUse.objects.create(
        credit=credit, booking=booking, amount_cents=200, used_at=timezone.now()
    )
    Booking.objects.filter(pk=booking.pk).update(credit_cents=200)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe())  # pyright: ignore[reportArgumentType]

    post_event(client, refunded_event(booking, 1_120))

    assert PosterCreditUse.objects.get(booking=booking).restored_cents == 200


def test_a_cancellation_with_compensation_pays_c_out_too(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    from bookings.tests.test_poster_cancel import late_cancelled

    booking = late_cancelled(client)
    settlement.settle(booking.pk, gateway=FakeSettlementStripe())  # pyright: ignore[reportArgumentType]

    post_event(client, refunded_event(booking, 2_520))

    booking.refresh_from_db()
    assert booking.status == S.PARTIALLY_SETTLED
    assert payout_of(booking).amount_cents == 750


# --- Tips. ---


def released(client: Client) -> Booking:
    booking = requested(client)
    return release.release_payment(poster(booking), booking.pk)


def tip_event(booking: Booking, intent_id: str, amount: int, **changes: Any) -> dict:
    event = copy.deepcopy(load_event("payment_intent.succeeded"))
    event["id"] = changes.pop("event_id", f"evt_test_tip_{intent_id}")
    event["data"]["object"].update(
        {
            "id": intent_id,
            "amount": amount,
            "application_fee_amount": None,
            "metadata": {"booking_id": str(booking.pk), "action": "tip"},
            "transfer_data": {"destination": helper_account(booking)},
            "on_behalf_of": helper_account(booking),
            **changes,
        }
    )
    return event


def test_a_tip_is_its_own_fee_less_charge_and_counts_only_once_stripe_confirms(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = released(client)

    session = tips.add_tip(poster(booking), booking.pk, 500)

    [call] = stripe.tips
    assert call == {
        "booking_id": str(booking.pk),
        "amount_cents": 500,
        "destination_account": helper_account(booking),
        "idempotency_key": f"booking:{booking.pk}:tip:v1",
    }
    assert session.client_secret.endswith("_secret_tip")
    tip = Tip.objects.get(booking=booking)
    assert tip.status == Tip.Status.PENDING
    booking.refresh_from_db()
    assert booking.tip_cents == 0
    assert not BookingPayout.objects.filter(kind=BookingPayout.Kind.TIP).exists()

    event = tip_event(booking, tip.payment_intent_id, 500)
    assert post_event(client, event) == 200
    assert post_event(client, event) == 200

    tip.refresh_from_db()
    booking.refresh_from_db()
    assert tip.status == Tip.Status.SUCCEEDED
    assert booking.tip_cents == 500
    assert booking.status == S.RELEASED
    assert BookingEvent.objects.filter(booking=booking, type=tips.TIP_RECEIVED).count() == 1
    assert tips.RECEIVED_TOPIC in topics(booking)
    tip_payout = payout_of(booking, BookingPayout.Kind.TIP)
    assert tip_payout.amount_cents == 500
    payouts.pay_out(tip_payout.pk)
    assert stripe.payouts[-1]["kind"] == "tip"
    assert stripe.payouts[-1]["idempotency_key"] == f"booking:{booking.pk}:payout:tip:v1"


def test_a_tip_waits_for_the_release(client: Client, gateway: FakeGateway) -> None:
    booking = requested(client)

    with pytest.raises(ApiError) as exc:
        tips.add_tip(poster(booking), booking.pk, 500)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


@pytest.mark.parametrize("cents", [0, 99, 3_001, True])
def test_a_tip_is_one_euro_up_to_the_price(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe, cents: int
) -> None:
    booking = released(client)

    with pytest.raises(ApiError) as exc:
        tips.add_tip(poster(booking), booking.pk, cents)

    assert code(exc) == ErrorCode.INVALID_TIP_AMOUNT.value
    assert stripe.tips == []


def test_one_tip_per_booking_and_its_amount_is_fixed_once_started(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = released(client)
    first = tips.add_tip(poster(booking), booking.pk, 500)
    again = tips.add_tip(poster(booking), booking.pk, 500)

    assert again.tip.pk == first.tip.pk
    assert {call["idempotency_key"] for call in stripe.tips} == {f"booking:{booking.pk}:tip:v1"}
    with pytest.raises(ApiError) as exc:
        tips.add_tip(poster(booking), booking.pk, 700)
    assert code(exc) == ErrorCode.INVALID_TIP_AMOUNT.value

    post_event(client, tip_event(booking, first.tip.payment_intent_id, 500))
    with pytest.raises(ApiError) as exc:
        tips.add_tip(poster(booking), booking.pk, 500)
    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_only_the_poster_tips(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = released(client)

    with pytest.raises(ApiError) as exc:
        tips.add_tip(helper(booking), booking.pk, 500)

    assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value


def test_a_tip_payment_that_doesnt_match_is_flagged_not_counted(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = released(client)
    session = tips.add_tip(poster(booking), booking.pk, 500)

    post_event(client, tip_event(booking, session.tip.payment_intent_id, 900))

    session.tip.refresh_from_db()
    assert session.tip.status == Tip.Status.PENDING
    assert BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=tips.TIP_MISMATCH
    ).exists()


def test_a_tip_is_never_taken_for_the_checkout(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    booking = released(client)
    session = tips.add_tip(poster(booking), booking.pk, 500)
    failed = copy.deepcopy(tip_event(booking, session.tip.payment_intent_id, 500))
    failed["id"] = "evt_test_tip_failed"
    failed["type"] = "payment_intent.payment_failed"

    assert post_event(client, failed) == 200

    booking.refresh_from_db()
    assert booking.status == S.RELEASED
    assert not BookingEvent.objects.filter(
        booking=booking, type=state_machine.SUPPORT_FLAG
    ).exists()


# --- Over the API. ---

API_FIELDS = """
  id status canReleasePayment
  releaseProposal { id releasedCents status refundCents }
  tip { id amountCents status }
  cancellation { case }
"""
RELEASE = "mutation($id: ID!) { releasePayment(bookingId: $id) { " + API_FIELDS + " } }"
PROPOSE = (
    "mutation($id: ID!, $cents: Int!) { proposePartialRelease(bookingId: $id, "
    "releasedCents: $cents) { " + API_FIELDS + " } }"
)
WITHDRAW = "mutation($id: ID!) { withdrawPartialRelease(bookingId: $id) { " + API_FIELDS + " } }"
RESPOND = (
    "mutation($id: ID!, $accept: Boolean!) { respondToPartialRelease(bookingId: $id, "
    "accept: $accept) { " + API_FIELDS + " } }"
)
PREVIEW = (
    "query($id: ID!, $cents: Int!) { partialReleasePreview(bookingId: $id, releasedCents: "
    "$cents) { id releasedCents keptFeeCents refundCents } }"
)
ADD_TIP = (
    "mutation($id: ID!, $cents: Int!) { addTip(bookingId: $id, amountCents: $cents) { "
    "id clientSecret tip { id amountCents status } booking { " + API_FIELDS + " } } }"
)
BOOKING_API = "query($id: ID!) { booking(id: $id) { " + API_FIELDS + " } }"


def api(client: Client, query: str, user: Any = None, **variables: Any) -> dict:
    from accounts.tests.test_schema import gql
    from bookings.tests.test_poster_cancel import token

    return gql(client, query, variables, token=token(user) if user else None)


def test_release_over_the_api(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    from accounts.tests.test_schema import error_codes

    booking = requested(client)
    pk = str(booking.pk)
    assert (
        api(client, BOOKING_API, poster(booking), id=pk)["data"]["booking"]["canReleasePayment"]
        is True
    )
    assert (
        api(client, BOOKING_API, helper(booking), id=pk)["data"]["booking"]["canReleasePayment"]
        is False
    )

    assert error_codes(api(client, RELEASE, id=pk)) == ["UNAUTHENTICATED"]
    assert error_codes(api(client, RELEASE, helper(booking), id=pk)) == ["BOOKING_NOT_FOUND"]
    first = api(client, RELEASE, poster(booking), id=pk)["data"]["releasePayment"]
    again = api(client, RELEASE, poster(booking), id=pk)["data"]["releasePayment"]

    assert first["status"] == again["status"] == "RELEASED"
    assert first["canReleasePayment"] is False
    assert BookingPayout.objects.filter(booking=booking).count() == 1


def test_a_partial_release_over_the_api(
    client: Client, gateway: FakeGateway, stripe: FakePayoutStripe
) -> None:
    from accounts.tests.test_schema import error_codes

    booking = requested(client)
    pk = str(booking.pk)

    preview = api(client, PREVIEW, poster(booking), id=pk, cents=2_000)["data"][
        "partialReleasePreview"
    ]
    assert preview == {"id": pk, "releasedCents": 2_000, "keptFeeCents": 240, "refundCents": 1_120}
    assert error_codes(api(client, PREVIEW, helper(booking), id=pk, cents=2_000)) == [
        "BOOKING_NOT_FOUND"
    ]
    assert error_codes(api(client, PREVIEW, poster(booking), id=pk, cents=3_000)) == [
        "INVALID_RELEASE_AMOUNT"
    ]

    proposed = api(client, PROPOSE, poster(booking), id=pk, cents=2_000)["data"][
        "proposePartialRelease"
    ]
    assert proposed["status"] == "PAYMENT_REQUESTED"
    assert proposed["releaseProposal"]["status"] == "OPEN"
    assert proposed["releaseProposal"]["refundCents"] == 1_120
    # The helper sees the amount, not the poster's refund.
    theirs = api(client, BOOKING_API, helper(booking), id=pk)["data"]["booking"]
    assert theirs["releaseProposal"]["releasedCents"] == 2_000
    assert theirs["releaseProposal"]["refundCents"] is None

    withdrawn = api(client, WITHDRAW, poster(booking), id=pk)["data"]["withdrawPartialRelease"]
    assert withdrawn["releaseProposal"]["status"] == "WITHDRAWN"
    api(client, PROPOSE, poster(booking), id=pk, cents=2_500)

    assert error_codes(api(client, RESPOND, poster(booking), id=pk, accept=True)) == [
        "BOOKING_NOT_FOUND"
    ]
    accepted = api(client, RESPOND, helper(booking), id=pk, accept=True)["data"][
        "respondToPartialRelease"
    ]
    assert accepted["releaseProposal"]["status"] == "ACCEPTED"
    assert accepted["status"] == "PAYMENT_REQUESTED"
    # An agreed partial release isn't shown as a cancellation.
    assert accepted["cancellation"] is None
    mine = api(client, BOOKING_API, poster(booking), id=pk)["data"]["booking"]
    assert mine["canReleasePayment"] is False and mine["cancellation"] is None


def test_a_tip_over_the_api(client: Client, gateway: FakeGateway, stripe: FakePayoutStripe) -> None:
    from accounts.tests.test_schema import error_codes

    booking = released(client)
    pk = str(booking.pk)

    assert error_codes(api(client, ADD_TIP, helper(booking), id=pk, cents=500)) == [
        "BOOKING_NOT_FOUND"
    ]
    assert error_codes(api(client, ADD_TIP, poster(booking), id=pk, cents=50)) == [
        "INVALID_TIP_AMOUNT"
    ]
    started_tip = api(client, ADD_TIP, poster(booking), id=pk, cents=500)["data"]["addTip"]
    assert started_tip["clientSecret"].endswith("_secret_tip")
    assert started_tip["tip"]["status"] == "PENDING"
    assert started_tip["booking"]["tip"]["status"] == "PENDING"
    # The helper sees a tip only once Stripe confirmed it.
    assert api(client, BOOKING_API, helper(booking), id=pk)["data"]["booking"]["tip"] is None

    tip = Tip.objects.get(booking=booking)
    post_event(client, tip_event(booking, tip.payment_intent_id, 500))

    theirs = api(client, BOOKING_API, helper(booking), id=pk)["data"]["booking"]
    assert theirs["tip"] == {"id": str(tip.pk), "amountCents": 500, "status": "SUCCEEDED"}
