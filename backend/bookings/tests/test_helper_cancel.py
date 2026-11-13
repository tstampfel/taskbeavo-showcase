"""The helper's cancel, no-fault cancellation and re-opening the task (BEAV-123; hard rules
a, c and g): the helper never pays, the EUR 5 credit is TaskBeavo's, two helper-fault
cancels within 30 days open a review item, the 48-hour reply runs the user's split
(decision 2026-11-07), and the poster re-opens the task to the other offers."""

import datetime as dt
import threading
import time

import pytest
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import business
from accounts.models import User
from accounts.tests.factories import access_token
from accounts.tests.test_schema import error_codes, gql
from bookings import helper_cancel, no_fault, reliability, settlement, state_machine
from bookings.cancel import PosterCancelReason, cancel_booking
from bookings.cancellation import NoFaultReason
from bookings.helper_cancel import HelperCancelReason
from bookings.models import (
    Booking,
    BookingEvent,
    CancellationSettlement,
    HelperFault,
    HelperReviewItem,
    NoFaultRequest,
    PosterCredit,
)
from bookings.queries import active_booking_of_helper, task_is_booked
from bookings.tests.factories import stripe_event
from bookings.tests.fakes import FakeGateway, FakeSettlementStripe
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    accept,
    legal_documents,
    post_event,
    publish,
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
from core import outbox
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from notifications.models import Delivery
from offers import services as offers
from offers.models import Offer
from offers.tests.factories import COMPANY_OIB, make_helper
from staff.roles import Role
from staff.tests.factories import make_staff
from tasks.models import Task

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)
D = dt.timedelta(days=1)
ILL = HelperCancelReason.ILL


@pytest.fixture
def stripe() -> FakeSettlementStripe:
    return FakeSettlementStripe()


def settlement_of(booking: Booking) -> CancellationSettlement:
    return CancellationSettlement.objects.get(booking=booking)


def refund_confirmed(client: Client, booking: Booking, stripe: FakeSettlementStripe) -> Booking:
    """Run the settlement at the fake Stripe, then let charge.refunded confirm it."""
    settlement.settle(booking.pk, gateway=stripe)  # pyright: ignore[reportArgumentType]
    booking.refresh_from_db()
    charge = booking.amount_cents + booking.fee_cents
    assert post_event(client, refunded_event(booking, charge, f"evt_r_{booking.pk.hex}")) == 200
    booking.refresh_from_db()
    return booking


# --- The helper cancels. ---


def test_a_helper_cancel_well_ahead_is_a_full_refund_without_credit(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client), 72 * H)

    cancelled = helper_cancel.cancel_as_helper(
        helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H
    )

    assert cancelled.status == S.CANCEL_REQUESTED
    assert cancelled.cancel_requested_by == ActorKind.HELPER
    assert cancelled.cancel_reason == "ILL"
    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.helper_compensation_cents, s.kept_fee_cents) == (
        "HELPER_CANCEL",
        3_360,
        0,
        0,
    )
    assert s.poster_credit_cents == 0


def test_a_late_helper_cancel_adds_the_5_euro_credit(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == ("HELPER_CANCEL", 3_360, 500)


def test_exactly_24_h_before_the_start_earns_no_credit(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 26 * H)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H)

    assert settlement_of(booking).poster_credit_cents == 0


def test_a_no_show_after_the_start_is_still_a_helper_cancel_with_credit(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 5 * H)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.slot_start + H)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == ("HELPER_CANCEL", 3_360, 500)


def test_the_helper_never_pays_and_the_credit_is_taskbeavos(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client), 20 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H)

    done = refund_confirmed(client, booking, stripe)

    # The helper's account ends at zero, never below: the poster's refund is the charge
    # coming back, and the EUR 5 isn't taken from anyone's balance at Stripe.
    assert (stripe.helper, stripe.taskbeavo, stripe.refunded) == (0, 0, 3_360)
    assert done.status == S.REFUNDED
    credit = PosterCredit.objects.get(source_booking=booking)
    assert (credit.poster_id, credit.amount_cents) == (booking.poster_id, 500)
    assert not credit.uses.exists()


def test_the_credit_is_granted_once_even_if_stripe_repeats_itself(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = starting(paid(client), 20 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H)
    refund_confirmed(client, booking, stripe)

    assert post_event(client, refunded_event(booking, 3_360, "evt_test_again")) == 200

    assert PosterCredit.objects.filter(source_booking=booking).count() == 1


def test_no_credit_without_a_confirmed_refund(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL, now=booking.created_at + 2 * H)

    assert not PosterCredit.objects.exists()


def test_after_a_payment_request_the_helper_cant_cancel(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    Booking.objects.filter(pk=booking.pk).update(status=S.PAYMENT_REQUESTED)

    with pytest.raises(ApiError) as refused:
        helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def test_only_the_helper_can_cancel_as_helper(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)

    with pytest.raises(ApiError) as refused:
        helper_cancel.cancel_as_helper(poster(booking), booking.pk, ILL)

    assert code(refused) == "BOOKING_NOT_FOUND"


def test_a_helper_cancel_cant_be_taken_back(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)

    with pytest.raises(ApiError) as refused:
        state_machine.transition(
            booking.pk, S.FUNDS_HELD, actor_kind=ActorKind.HELPER, actor=helper(booking)
        )

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_the_poster_is_told_their_helper_cancelled(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)

    publish(helper_cancel.CANCELLED_BY_HELPER_TOPIC, booking)

    assert Delivery.objects.filter(user_id=booking.poster_id, kind="booking.cancelled").exists()
    assert not Delivery.objects.filter(user_id=booking.helper_id).exists()


# --- Reliability: a fault, never a penalty. ---


def test_a_helper_cancel_counts_against_reliability(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    me = helper(booking)
    assert reliability.reliability_score(me, timezone.now()) == 100

    helper_cancel.cancel_as_helper(me, booking.pk, ILL)

    fault = HelperFault.objects.get(booking=booking)
    assert (fault.helper_id, fault.kind) == (me.pk, HelperFault.Kind.CANCELLED)
    assert reliability.reliability_score(me, timezone.now()) == 0


def booking_for(client: Client, me: User, names: tuple[str, str]) -> Booking:
    """Another paid booking of helper `me`, starting in three days."""
    booking = starting(paid(client, helpers=names), 72 * H)
    Booking.objects.filter(pk=booking.pk).update(helper=me)
    booking.refresh_from_db()
    return booking


def test_one_helper_cancel_opens_no_review(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)

    assert not HelperReviewItem.objects.exists()


def test_two_helper_cancels_in_30_days_open_a_review_item(
    client: Client, gateway: FakeGateway
) -> None:
    first = starting(paid(client), 72 * H)
    me = helper(first)
    second = booking_for(client, me, ("luka", "ante"))
    now = timezone.now()

    helper_cancel.cancel_as_helper(me, first.pk, ILL, now=now)
    HelperFault.objects.filter(booking=first).update(created_at=now - 29 * D)
    helper_cancel.cancel_as_helper(me, second.pk, ILL, now=now)

    item = HelperReviewItem.objects.get(helper=me)
    assert item.reason == HelperReviewItem.Reason.REPEATED_HELPER_CANCELS
    assert set(item.faults.values_list("booking_id", flat=True)) == {first.pk, second.pk}
    assert item.reviewed_at is None
    # A person decides; nothing is blocked automatically and nobody pays.
    me.refresh_from_db()
    assert me.is_active
    assert settlement_of(second).helper_compensation_cents == 0


def test_two_cancels_more_than_30_days_apart_open_no_review(
    client: Client, gateway: FakeGateway
) -> None:
    first = starting(paid(client), 72 * H)
    me = helper(first)
    second = booking_for(client, me, ("luka", "ante"))
    now = timezone.now()

    helper_cancel.cancel_as_helper(me, first.pk, ILL, now=now)
    HelperFault.objects.filter(booking=first).update(created_at=now - 31 * D)
    helper_cancel.cancel_as_helper(me, second.pk, ILL, now=now)

    assert not HelperReviewItem.objects.exists()


def test_a_third_cancel_joins_the_open_review_item(client: Client, gateway: FakeGateway) -> None:
    first = starting(paid(client), 72 * H)
    me = helper(first)
    others = [
        booking_for(client, me, ("luka", "ante")),
        booking_for(client, me, ("petar", "josip")),
    ]

    for booking in (first, *others):
        helper_cancel.cancel_as_helper(me, booking.pk, ILL)

    item = HelperReviewItem.objects.get(helper=me)
    assert item.faults.count() == 3


def test_support_lists_and_reviews_review_items(client: Client, gateway: FakeGateway) -> None:
    first = starting(paid(client), 72 * H)
    me = helper(first)
    second = booking_for(client, me, ("luka", "ante"))
    helper_cancel.cancel_as_helper(me, first.pk, ILL)
    helper_cancel.cancel_as_helper(me, second.pk, ILL)
    item = HelperReviewItem.objects.get(helper=me)
    staff = Client()
    staff.force_login(make_staff(Role.SUPPORT))

    page = staff.get(reverse("admin:bookings_helperreviewitem_changelist"))
    done = staff.post(
        reverse("admin:bookings_helperreviewitem_changelist"),
        {"action": "mark_reviewed", "_selected_action": [item.pk]},
    )

    assert page.status_code == 200
    assert f">{item.pk}<" in page.content.decode() or str(item.pk) in page.content.decode()
    assert done.status_code == 302
    item.refresh_from_db()
    assert item.reviewed_at is not None


# --- No-fault cancellation and the 48-hour reply. ---


def requested(
    booking: Booking, by: User, reason: NoFaultReason = NoFaultReason.WEATHER, **kw: object
) -> Booking:
    return no_fault.request_no_fault(by, booking.pk, reason, **kw)  # pyright: ignore[reportArgumentType]


def test_a_no_fault_request_waits_48_h_for_the_other_side(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    now = booking.created_at + 2 * H

    waiting = requested(booking, poster(booking), now=now)

    assert waiting.status == S.CANCEL_REQUESTED
    assert waiting.cancel_reason == "NO_FAULT"
    request = NoFaultRequest.objects.get(booking=booking)
    assert (request.reason, request.requested_by, request.respond_by) == (
        "WEATHER",
        ActorKind.POSTER,
        now + 48 * H,
    )
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    publish(no_fault.REQUESTED_TOPIC, booking)
    assert Delivery.objects.filter(
        user_id=booking.helper_id, kind="booking.cancellation_requested"
    ).exists()
    assert not Delivery.objects.filter(user_id=booking.poster_id).exists()


def test_the_other_side_agrees_and_the_poster_gets_everything_back(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking), NoFaultReason.HOSPITALISATION)

    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=True)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.helper_compensation_cents, s.poster_credit_cents) == (
        "NO_FAULT",
        3_360,
        0,
        0,
    )
    assert NoFaultRequest.objects.get(booking=booking).closed_at is not None
    assert not HelperFault.objects.exists()


def test_a_helpers_no_fault_reason_the_poster_accepts_is_no_fault_of_theirs(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, helper(booking), NoFaultReason.EMERGENCY)

    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == ("NO_FAULT", 3_360, 0)
    assert not HelperFault.objects.exists()


def test_a_no_fault_reason_still_counts_after_the_start(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 5 * H)
    requested(booking, poster(booking), now=booking.slot_start + H)

    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=True)

    assert settlement_of(booking).case == "NO_FAULT"


def test_the_requester_cant_answer_their_own_request(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking))

    with pytest.raises(ApiError) as refused:
        no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True)

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_no_request_after_a_payment_request(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    Booking.objects.filter(pk=booking.pk).update(status=S.PAYMENT_REQUESTED)

    with pytest.raises(ApiError) as refused:
        requested(booking, poster(booking))

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_strangers_cant_request_or_answer(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    stranger = make_helper("stranger")

    with pytest.raises(ApiError) as refused:
        requested(booking, stranger)
    assert code(refused) == "BOOKING_NOT_FOUND"
    requested(booking, poster(booking))
    with pytest.raises(ApiError) as refused:
        no_fault.respond_to_no_fault(stranger, booking.pk, agree=True)
    assert code(refused) == "BOOKING_NOT_FOUND"


def test_a_disagreement_goes_to_support_and_nothing_moves(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking))

    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)

    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.DISAGREED
    assert request.support_needed_at is not None and request.closed_at is None
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert no_fault.awaiting_support(booking)
    publish(no_fault.SUPPORT_TOPIC, booking)
    assert Delivery.objects.filter(
        user_id=booking.poster_id, kind="booking.cancellation_under_review"
    ).exists()


def test_the_posters_request_stands_when_the_helper_doesnt_reply(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    now = timezone.now()
    requested(booking, poster(booking), now=now)

    assert no_fault.settle_unanswered(now + 47 * H) == 0
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert no_fault.settle_unanswered(now + 48 * H) == 1

    s = settlement_of(booking)
    assert (s.case, s.refund_cents) == ("NO_FAULT", 3_360)
    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.NO_REPLY
    assert request.closed_at is not None
    assert no_fault.settle_unanswered(now + 49 * H) == 0


def test_the_helpers_request_goes_to_support_when_the_poster_doesnt_reply(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    now = timezone.now()
    requested(booking, helper(booking), now=now)

    assert no_fault.settle_unanswered(now + 48 * H) == 1

    # No automatic outcome: no refund, no reliability fault, no credit decision.
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert not HelperFault.objects.exists()
    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.NO_REPLY
    assert request.support_needed_at is not None and request.closed_at is None
    assert no_fault.settle_unanswered(now + 72 * H) == 0
    publish(no_fault.SUPPORT_TOPIC, booking)
    assert Delivery.objects.filter(
        user_id=booking.helper_id, kind="booking.cancellation_under_review"
    ).exists()


def test_a_helpers_answer_after_the_48_h_is_refused_and_the_request_stands(
    client: Client, gateway: FakeGateway
) -> None:
    """The deadline decides, not the sweep: a disagreement after 48 h, before the sweep
    ran, can't send the poster's request to support."""
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)

    with pytest.raises(ApiError) as refused:
        no_fault.respond_to_no_fault(
            helper(booking), booking.pk, agree=False, now=asked + 48 * H + dt.timedelta(minutes=5)
        )

    assert code(refused) == "NO_FAULT_REPLY_CLOSED"
    request = NoFaultRequest.objects.get(booking=booking)
    assert (request.response, request.support_needed_at) == ("", None)
    assert no_fault.settle_unanswered(asked + 48 * H + dt.timedelta(minutes=15)) == 1
    s = settlement_of(booking)
    assert (s.case, s.refund_cents) == ("NO_FAULT", 3_360)
    request.refresh_from_db()
    assert request.response == NoFaultRequest.Response.NO_REPLY


def test_an_answer_exactly_at_the_deadline_is_refused(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)

    with pytest.raises(ApiError) as refused:
        no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=True, now=asked + 48 * H)

    assert code(refused) == "NO_FAULT_REPLY_CLOSED"


def test_an_answer_just_before_the_deadline_still_counts(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)

    no_fault.respond_to_no_fault(
        helper(booking), booking.pk, agree=False, now=asked + 48 * H - dt.timedelta(seconds=1)
    )

    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.DISAGREED
    assert no_fault.awaiting_support(booking)
    assert no_fault.settle_unanswered(asked + 48 * H) == 0
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def helpers_request_at_support(client: Client) -> tuple[Booking, dt.datetime]:
    """A helper's request the poster left unanswered: the sweep sent it to support."""
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, helper(booking), now=asked)
    assert no_fault.settle_unanswered(asked + 48 * H) == 1
    return booking, asked


def test_a_poster_agreeing_late_before_support_decides_gets_the_full_refund(
    client: Client, gateway: FakeGateway
) -> None:
    """Only the helper's answer closes at the deadline (coordinator decision 2026-11-07)."""
    booking, asked = helpers_request_at_support(client)

    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True, now=asked + 60 * H)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents) == ("NO_FAULT", 3_360)
    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.AGREED
    assert request.closed_at is not None
    assert not no_fault.awaiting_support(booking)
    assert not HelperFault.objects.exists()


def test_a_poster_agreeing_late_before_the_sweep_gets_the_full_refund(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, helper(booking), now=asked)

    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True, now=asked + 49 * H)

    assert settlement_of(booking).case == "NO_FAULT"
    assert no_fault.settle_unanswered(asked + 49 * H) == 0


def test_a_posters_late_disagreement_stays_with_support(
    client: Client, gateway: FakeGateway
) -> None:
    booking, asked = helpers_request_at_support(client)
    support_needed_at = NoFaultRequest.objects.get(booking=booking).support_needed_at
    told = Outbox.objects.filter(topic=no_fault.SUPPORT_TOPIC).count()

    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=False, now=asked + 60 * H)

    request = NoFaultRequest.objects.get(booking=booking)
    assert request.response == NoFaultRequest.Response.DISAGREED  # recorded for support
    assert request.responded_at == asked + 60 * H
    assert (request.support_needed_at, request.closed_at) == (support_needed_at, None)
    assert no_fault.awaiting_support(booking)
    assert Outbox.objects.filter(topic=no_fault.SUPPORT_TOPIC).count() == told
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    # Answered once: no second answer.
    with pytest.raises(ApiError) as again:
        no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True, now=asked + 61 * H)
    assert code(again) == "BOOKING_ACTION_NOT_ALLOWED"


@pytest.mark.parametrize("uphold", [True, False])
def test_a_posters_answer_after_support_decided_is_refused(
    client: Client, gateway: FakeGateway, uphold: bool
) -> None:
    booking, asked = helpers_request_at_support(client)
    no_fault.decide_no_fault(finance(), booking.pk, uphold=uphold)

    with pytest.raises(ApiError) as refused:
        no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True, now=asked + 60 * H)

    assert code(refused) == "NO_FAULT_REPLY_CLOSED"


def test_the_poster_may_still_answer_at_support_over_the_api(
    client: Client, gateway: FakeGateway
) -> None:
    booking, _ = helpers_request_at_support(client)

    seen = gql(client, BOOKING, {"id": str(booking.pk)}, token=access_token(poster(booking)))

    assert seen["data"]["booking"]["noFaultRequest"]["canRespond"] is True


def test_a_late_answer_through_the_api_gets_the_error_code(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    requested(booking, poster(booking), now=timezone.now() - 49 * H)
    variables = {"id": str(booking.pk)}

    seen = gql(client, BOOKING, variables, token=access_token(helper(booking)))
    response = gql(
        client,
        "mutation($id: ID!) { respondToNoFaultCancellation(id: $id, agree: false) { id } }",
        variables,
        token=access_token(helper(booking)),
    )

    # The apps stop offering the buttons once the 48 hours are up, even before the sweep.
    assert seen["data"]["booking"]["noFaultRequest"]["canRespond"] is False
    assert error_codes(response) == ["NO_FAULT_REPLY_CLOSED"]


def finance() -> User:
    return make_staff(Role.FINANCE)


def both_told(booking: Booking, kind: str) -> bool:
    """Support's decision reaches both sides, with what it means (user decision 2026-11-07)."""
    publish(no_fault.DECIDED_TOPIC, booking)
    told = Delivery.objects.filter(kind=kind).values_list("user_id", flat=True)
    return {booking.poster_id, booking.helper_id} <= set(told)


def test_support_upholds_a_disputed_reason(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, helper(booking))
    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=False)

    no_fault.decide_no_fault(make_staff(Role.SUPPORT), booking.pk, uphold=True)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == ("NO_FAULT", 3_360, 0)
    assert NoFaultRequest.objects.get(booking=booking).decision == "UPHELD"
    assert not HelperFault.objects.exists()
    assert both_told(booking, "booking.no_fault_upheld")


def test_support_rejecting_a_helpers_reason_makes_it_a_helper_cancel(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    asked = booking.created_at + 2 * H
    requested(booking, helper(booking), now=asked)
    no_fault.settle_unanswered(asked + 48 * H)

    # Decided after the start: the credit follows when the helper asked (under 24 h).
    no_fault.decide_no_fault(finance(), booking.pk, uphold=False, now=booking.slot_start + D)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == ("HELPER_CANCEL", 3_360, 500)
    fault = HelperFault.objects.get(booking=booking)
    assert fault.kind == HelperFault.Kind.NO_FAULT_REJECTED
    assert both_told(booking, "booking.no_fault_rejected")
    # The poster hears it once, from support's decision, not also as a helper cancel (R1-9).
    publish(helper_cancel.CANCELLED_BY_HELPER_TOPIC, booking)
    assert not Delivery.objects.filter(user_id=booking.poster_id, kind="booking.cancelled").exists()


def test_support_rejecting_a_posters_reason_applies_the_policy_as_of_the_request(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 20 * H)
    asked = booking.created_at + 2 * H
    requested(booking, poster(booking), now=asked)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False, now=asked + H)

    no_fault.decide_no_fault(finance(), booking.pk, uphold=False, now=booking.slot_start + D)

    s = settlement_of(booking)
    assert (s.case, s.refund_cents, s.helper_compensation_cents, s.kept_fee_cents) == (
        "LATE",
        2_520,
        750,
        90,
    )
    assert both_told(booking, "booking.no_fault_rejected")


def test_rejecting_a_posters_reason_given_after_the_start_holds_the_booking_again(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 5 * H)
    requested(booking, poster(booking), now=booking.slot_start + H)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)

    no_fault.decide_no_fault(finance(), booking.pk, uphold=False)

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert booking.cancel_requested_by == "" and booking.cancel_reason == ""
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert NoFaultRequest.objects.get(booking=booking).closed_at is not None
    assert both_told(booking, "booking.no_fault_rejected")


def test_support_decides_only_requests_waiting_for_it(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking))

    with pytest.raises(ApiError) as refused:
        no_fault.decide_no_fault(finance(), booking.pk, uphold=True)

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_trust_cant_decide_a_request(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking))
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)

    with pytest.raises(ApiError) as refused:
        no_fault.decide_no_fault(make_staff(Role.TRUST), booking.pk, uphold=True)

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_support_decides_from_the_booking_page(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 20 * H)
    requested(booking, poster(booking))
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)
    staff = Client()
    staff.force_login(make_staff(Role.SUPPORT))
    url = reverse("admin:bookings_booking_no_fault_decision", args=[booking.pk])

    queue = staff.get(reverse("admin:bookings_booking_changelist") + "?cancellation=no_fault")
    page = staff.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    response = staff.post(url, {"decision": "uphold"})

    assert str(booking.pk) in queue.content.decode()
    assert url in page.content.decode()
    assert response.status_code == 302
    assert settlement_of(booking).case == "NO_FAULT"


# --- Re-opening the task. ---


def helper_cancelled(client: Client) -> Booking:
    booking = starting(paid(client), 72 * H)
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)
    booking.refresh_from_db()
    return booking


def test_the_poster_reopens_the_task_and_the_other_helpers_hear_of_it(
    client: Client, gateway: FakeGateway
) -> None:
    booking = helper_cancelled(client)
    task = Task.objects.get(pk=booking.task_id)
    declined = Offer.objects.get(pk=booking.declined_offer_ids[0])
    assert task_is_booked(task)
    assert helper_cancel.can_reopen(booking)

    reopened = helper_cancel.reopen_task(poster(booking), booking.pk)

    assert reopened.task_reopened_at is not None
    declined.refresh_from_db()
    assert declined.status == Offer.Status.SENT
    assert Offer.objects.get(pk=booking.offer_id).status == Offer.Status.ACCEPTED
    assert not task_is_booked(task)
    assert not helper_cancel.can_reopen(reopened)
    publish(helper_cancel.TASK_REOPENED_TOPIC, booking)
    assert Delivery.objects.filter(user_id=declined.helper_id, kind="offer.task_reopened").exists()
    assert not Delivery.objects.filter(
        user_id=booking.helper_id, kind="offer.task_reopened"
    ).exists()
    assert BookingEvent.objects.filter(booking=booking, type=helper_cancel.REOPENED).exists()


def test_reopening_a_business_posters_task_withdraws_private_offers_instead(
    client: Client, gateway: FakeGateway
) -> None:
    """BEAV-195 review R2-1: the poster booked a registered helper, then became a business,
    then that helper cancelled. Re-opening the task doesn't bring a private helper's declined
    offer back live: it is withdrawn with the neutral reason, and that helper hears why, not
    that the task is open again."""
    booking = helper_cancelled_after_switch(client)
    declined = Offer.objects.get(pk=booking.declined_offer_ids[0])

    helper_cancel.reopen_task(poster(booking), booking.pk)

    declined.refresh_from_db()
    reason = Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY
    assert (declined.status, declined.withdrawn_reason) == (Offer.Status.WITHDRAWN, reason)
    event = BookingEvent.objects.get(booking=booking, type=helper_cancel.REOPENED)
    assert event.data["offers"] == []
    publish(helper_cancel.TASK_REOPENED_TOPIC, booking)
    for row in Outbox.objects.filter(
        topic=offers.WITHDRAWN_REGISTERED_ONLY_TOPIC, aggregate_id=str(declined.pk)
    ):
        outbox.publish_row(row.pk)
    kinds = set(Delivery.objects.filter(user_id=declined.helper_id).values_list("kind", flat=True))
    assert "offer.withdrawn_registered_only" in kinds
    assert "offer.task_reopened" not in kinds


def helper_cancelled_after_switch(client: Client) -> Booking:
    booking = starting(paid(client, trader=True), 72 * H)
    business.set_poster_business(
        poster(booking), kind="COMPANY", name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )
    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)
    booking.refresh_from_db()
    return booking


def test_after_reopening_another_offer_can_be_booked(client: Client, gateway: FakeGateway) -> None:
    booking = helper_cancelled(client)
    helper_cancel.reopen_task(poster(booking), booking.pk)
    declined = Offer.objects.get(pk=booking.declined_offer_ids[0])

    session = accept(declined)

    assert session.booking.status == S.AWAITING_PAYMENT
    assert session.booking.task_id == booking.task_id


# --- Hard rule e: access to the exact place ends when a cancel is recorded (user
# decision 2026-11-07). Only a pending no-fault request keeps it. ---

EXACT_TASK = "query($id: ID!) { task(id: $id) { id address accessDetails onSiteContact { id } } }"


def helper_sees_the_place(client: Client, booking: Booking) -> bool:
    """Checks every route at once: the model, the query other apps use, and the API."""
    booking.refresh_from_db()
    task = Task.objects.get(pk=booking.task_id)
    body = gql(client, EXACT_TASK, {"id": str(booking.task_id)}, access_token(helper(booking)))
    assert "errors" not in body, error_codes(body)
    seen = body["data"]["task"]
    routes = {
        booking.is_active,
        active_booking_of_helper(task, helper(booking)) is not None,
        seen["address"] is not None and seen["accessDetails"] is not None,
    }
    assert len(routes) == 1, "the model, the query and the API must agree"
    return routes.pop()


def test_the_helper_loses_the_place_the_moment_they_cancel(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    assert helper_sees_the_place(client, booking)

    helper_cancel.cancel_as_helper(helper(booking), booking.pk, ILL)

    assert not helper_sees_the_place(client, booking)
    assert booking.status == S.CANCEL_REQUESTED  # the refund isn't confirmed yet


def test_the_helper_loses_the_place_when_the_poster_cancels_late(
    client: Client, gateway: FakeGateway
) -> None:
    booking = accepted_hours_ago(paid(client), 2, starts_in=10 * H)

    cancel_booking(poster(booking), booking.pk, PosterCancelReason.NO_LONGER_NEEDED)

    assert not helper_sees_the_place(client, booking)


def test_a_pending_no_fault_request_keeps_the_place_until_it_is_agreed(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    requested(booking, poster(booking))
    assert helper_sees_the_place(client, booking)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)
    assert helper_sees_the_place(client, booking)  # waiting for support

    no_fault.decide_no_fault(make_staff(Role.SUPPORT), booking.pk, uphold=True)

    assert not helper_sees_the_place(client, booking)


def test_an_agreed_no_fault_request_ends_access(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    requested(booking, helper(booking))

    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True)

    assert not helper_sees_the_place(client, booking)


def test_an_unanswered_posters_request_that_stands_ends_access(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)

    no_fault.settle_unanswered(asked + 48 * H)

    assert not helper_sees_the_place(client, booking)


def test_the_place_comes_back_when_a_rejected_request_returns_the_funds_to_held(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), -H)  # a poster's request after the start
    requested(booking, poster(booking), NoFaultReason.HOSPITALISATION)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)

    no_fault.decide_no_fault(make_staff(Role.SUPPORT), booking.pk, uphold=False)

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert helper_sees_the_place(client, booking)


def test_the_cancelled_helper_stays_out_once_reopened(client: Client, gateway: FakeGateway) -> None:
    booking = helper_cancelled(client)

    helper_cancel.reopen_task(poster(booking), booking.pk)

    assert not helper_sees_the_place(client, booking)


def test_a_no_fault_cancel_can_be_reopened_too(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    requested(booking, helper(booking))
    no_fault.respond_to_no_fault(poster(booking), booking.pk, agree=True)

    helper_cancel.reopen_task(poster(booking), booking.pk)

    booking.refresh_from_db()
    assert booking.task_reopened_at is not None


def test_a_posters_own_cancel_cant_be_reopened(client: Client, gateway: FakeGateway) -> None:
    booking = starting(paid(client), 72 * H)
    cancel_booking(poster(booking), booking.pk, PosterCancelReason.NO_LONGER_NEEDED)

    with pytest.raises(ApiError) as refused:
        helper_cancel.reopen_task(poster(booking), booking.pk)

    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"
    assert not helper_cancel.can_reopen(booking)


def test_only_the_poster_reopens_and_only_once(client: Client, gateway: FakeGateway) -> None:
    booking = helper_cancelled(client)

    with pytest.raises(ApiError) as refused:
        helper_cancel.reopen_task(helper(booking), booking.pk)
    assert code(refused) == "BOOKING_NOT_FOUND"
    helper_cancel.reopen_task(poster(booking), booking.pk)
    with pytest.raises(ApiError) as refused:
        helper_cancel.reopen_task(poster(booking), booking.pk)
    assert code(refused) == "BOOKING_ACTION_NOT_ALLOWED"


def test_a_task_whose_dates_passed_cant_be_reopened(client: Client, gateway: FakeGateway) -> None:
    booking = helper_cancelled(client)
    task = Task.objects.get(pk=booking.task_id)

    with pytest.raises(ApiError) as refused:
        helper_cancel.reopen_task(
            poster(booking), booking.pk, now=(task.window_end or timezone.now()) + H
        )

    assert code(refused) == ErrorCode.TASK_NOT_OPEN_FOR_OFFERS.value


def test_reopening_doesnt_stop_the_refund(
    client: Client, gateway: FakeGateway, stripe: FakeSettlementStripe
) -> None:
    booking = helper_cancelled(client)
    helper_cancel.reopen_task(poster(booking), booking.pk)

    done = refund_confirmed(client, booking, stripe)

    assert done.status == S.REFUNDED
    assert Outbox.objects.filter(topic=settlement.SETTLED_TOPIC).exists()


def test_reopened_offers_past_their_expiry_stay_expired(
    client: Client, gateway: FakeGateway
) -> None:
    booking = helper_cancelled(client)
    Offer.objects.filter(pk__in=booking.declined_offer_ids).update(expires_at=timezone.now() - H)

    helper_cancel.reopen_task(poster(booking), booking.pk)

    assert Offer.objects.get(pk=booking.declined_offer_ids[0]).status == Offer.Status.EXPIRED


def test_offers_send_offer_works_again_on_a_reopened_task(
    client: Client, gateway: FakeGateway
) -> None:
    booking = helper_cancelled(client)
    newcomer = make_helper("newcomer")
    with pytest.raises(ApiError):
        offers.send_offer(newcomer, str(booking.task_id), 3_000)

    helper_cancel.reopen_task(poster(booking), booking.pk)

    assert offers.send_offer(newcomer, str(booking.task_id), 3_000).status == Offer.Status.SENT


# --- The API. ---

FIELDS = """
  id status canRequestNoFault canReopenTask taskReopened
  helperCancelPreview { id refundCents posterCreditCents refusedWith }
  noFaultRequest {
    id reason label requestedBy requestedByYou respondBy response awaitingSupport canRespond
    open
  }
  cancellation { id case refundCents requestedBy posterCreditCents confirmed }
"""
BOOKING = "query($id: ID!) { booking(id: $id) { " + FIELDS + " } }"
CANCEL_AS_HELPER = (
    "mutation($id: ID!, $reason: HelperCancelReason!) { cancelAsHelper(id: $id, reason: $reason)"
    " { " + FIELDS + " } }"
)
REQUEST = (
    "mutation($id: ID!, $reason: NoFaultReason!) {"
    " requestNoFaultCancellation(id: $id, reason: $reason) { " + FIELDS + " } }"
)
RESPOND = (
    "mutation($id: ID!, $agree: Boolean!) {"
    " respondToNoFaultCancellation(id: $id, agree: $agree) { " + FIELDS + " } }"
)
REOPEN = "mutation($id: ID!) { reopenTask(bookingId: $id) { " + FIELDS + " } }"


def api_token(user: User) -> str:
    return token(user)


def test_the_helper_sees_their_cancel_preview_and_the_poster_doesnt(
    client: Client, gateway: FakeGateway
) -> None:

    booking = accepted_hours_ago(paid(client), 2, starts_in=10 * H)

    mine = gql(client, BOOKING, {"id": str(booking.pk)}, token=api_token(helper(booking)))
    theirs = gql(client, BOOKING, {"id": str(booking.pk)}, token=api_token(poster(booking)))

    assert mine["data"]["booking"]["helperCancelPreview"] == {
        "id": str(booking.pk),
        "refundCents": 3_360,
        "posterCreditCents": 500,
        "refusedWith": None,
    }
    assert theirs["data"]["booking"]["helperCancelPreview"] is None
    assert mine["data"]["booking"]["canRequestNoFault"] is True


def test_cancel_as_helper_over_the_api(client: Client, gateway: FakeGateway) -> None:

    booking = starting(paid(client), 72 * H)
    variables = {"id": str(booking.pk), "reason": "DOUBLE_BOOKED"}

    assert error_codes(gql(client, CANCEL_AS_HELPER, variables)) == ["UNAUTHENTICATED"]
    assert error_codes(
        gql(client, CANCEL_AS_HELPER, variables, token=api_token(poster(booking)))
    ) == ["BOOKING_NOT_FOUND"]
    body = gql(client, CANCEL_AS_HELPER, variables, token=api_token(helper(booking)))

    result = body["data"]["cancelAsHelper"]
    assert result["status"] == "CANCEL_REQUESTED"
    assert result["cancellation"] == {
        "id": str(booking.pk),
        "case": "HELPER_CANCEL",
        "refundCents": None,  # the poster's refund isn't the helper's to see (R1-7)
        "requestedBy": "HELPER",
        "posterCreditCents": 0,
        "confirmed": False,
    }
    seen = gql(client, BOOKING, {"id": str(booking.pk)}, token=api_token(poster(booking)))
    assert seen["data"]["booking"]["canReopenTask"] is True


def test_the_no_fault_round_trip_over_the_api(client: Client, gateway: FakeGateway) -> None:

    booking = starting(paid(client), 72 * H)
    asked = gql(
        client,
        REQUEST,
        {"id": str(booking.pk), "reason": "HOSPITALISATION"},
        token=api_token(poster(booking)),
    )["data"]["requestNoFaultCancellation"]
    assert asked["status"] == "CANCEL_REQUESTED"
    assert asked["noFaultRequest"]["requestedByYou"] is True
    assert asked["noFaultRequest"]["canRespond"] is False
    assert (asked["noFaultRequest"]["reason"], asked["noFaultRequest"]["label"]) == (
        "HOSPITALISATION",
        "PERSONAL_EMERGENCY",
    )

    variables = {"id": str(booking.pk), "agree": True}
    own = gql(client, RESPOND, variables, token=api_token(poster(booking)))
    assert error_codes(own) == ["BOOKING_ACTION_NOT_ALLOWED"]
    seen = gql(client, BOOKING, {"id": str(booking.pk)}, token=api_token(helper(booking)))
    request = seen["data"]["booking"]["noFaultRequest"]
    # Decision 4 (GDPR Art. 9): the other side sees only the generic label, and the
    # specific reason is in no event row or outbox payload.
    assert (request["reason"], request["label"], request["requestedBy"]) == (
        None,
        "PERSONAL_EMERGENCY",
        "POSTER",
    )
    assert request["canRespond"] is True
    for data in BookingEvent.objects.filter(booking=booking).values_list("data", flat=True):
        assert "HOSPITALISATION" not in str(data)
    for payload in Outbox.objects.filter(aggregate_id=booking.pk).values_list("payload", flat=True):
        assert "HOSPITALISATION" not in str(payload)
    agreed = gql(
        client, RESPOND, {"id": str(booking.pk), "agree": True}, token=api_token(helper(booking))
    )["data"]["respondToNoFaultCancellation"]

    assert agreed["noFaultRequest"]["response"] == "AGREED"
    assert agreed["noFaultRequest"]["open"] is False
    assert agreed["cancellation"]["case"] == "NO_FAULT"


def test_reopen_task_over_the_api(client: Client, gateway: FakeGateway) -> None:

    booking = helper_cancelled(client)
    variables = {"id": str(booking.pk)}

    assert error_codes(gql(client, REOPEN, variables, token=api_token(helper(booking)))) == [
        "BOOKING_NOT_FOUND"
    ]
    result = gql(client, REOPEN, variables, token=api_token(poster(booking)))["data"]["reopenTask"]

    assert (result["taskReopened"], result["canReopenTask"]) == (True, False)


def test_the_sweep_closes_a_request_whose_booking_moved_on(
    client: Client, gateway: FakeGateway
) -> None:
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + 2 * H
    requested(booking, poster(booking), now=asked)
    Booking.objects.filter(pk=booking.pk).update(status=S.DISPUTED)  # a chargeback

    no_fault.settle_unanswered(asked + 49 * H)

    request = NoFaultRequest.objects.get(booking=booking)
    assert request.closed_at is not None and request.response == ""
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def test_a_side_cites_a_no_fault_reason_once_per_booking(
    client: Client, gateway: FakeGateway
) -> None:
    """R1-4: after support rejects it and the booking is held again, the same side can't
    ask again; the other side still may."""
    booking = starting(paid(client), -H)
    requested(booking, poster(booking), NoFaultReason.WEATHER)
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)
    no_fault.decide_no_fault(make_staff(Role.SUPPORT), booking.pk, uphold=False)
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD

    with pytest.raises(ApiError) as refused:
        requested(booking, poster(booking), NoFaultReason.EMERGENCY)

    assert refused.value.extensions["code"] == "BOOKING_ACTION_NOT_ALLOWED"
    assert no_fault.can_request(booking, "HELPER")


def test_a_request_waiting_for_support_closes_when_a_chargeback_moves_the_booking(
    client: Client, gateway: FakeGateway
) -> None:
    """R1-5: a request already with support isn't picked up by the sweep; leaving
    CANCEL_REQUESTED closes it, whatever moved the booking."""
    booking = starting(paid(client), 72 * H)
    requested(booking, poster(booking))
    no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=False)

    with transaction.atomic():
        state_machine.apply_stripe_transition(
            booking.pk, S.DISPUTED, stripe_event_id=stripe_event()
        )

    request = NoFaultRequest.objects.get(booking=booking)
    assert request.closed_at is not None
    assert not no_fault.awaiting_support(booking)


@pytest.mark.parametrize("by_agreement", [True, False])
def test_both_sides_are_told_when_a_request_stands(
    client: Client, gateway: FakeGateway, by_agreement: bool
) -> None:
    """R1-6: agreed, or the poster's left unanswered: both get the push and email."""
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)
    if by_agreement:
        no_fault.respond_to_no_fault(helper(booking), booking.pk, agree=True, now=asked + H)
    else:
        no_fault.settle_unanswered(asked + 48 * H)

    publish(no_fault.STOOD_TOPIC, booking)

    told = set(
        Delivery.objects.filter(kind="booking.no_fault_cancelled").values_list("user_id", flat=True)
    )
    assert told == {booking.poster_id, booking.helper_id}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("sweep_first", [True, False])
def test_the_sweep_and_a_late_reply_never_both_count(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch, sweep_first: bool
) -> None:
    """R1-12: the 48 h sweep and the helper's last-second reply (sent just before the
    deadline) race on the booking lock. Exactly one wins; the other changes nothing."""
    # Commits run their on_commit work here: settle against the fake, never Stripe.
    monkeypatch.setattr(settlement, "get_gateway", FakeSettlementStripe)
    booking = starting(paid(client), 72 * H)
    asked = booking.created_at + H
    requested(booking, poster(booking), now=asked)
    due = asked + 48 * H
    first_locked = threading.Event()
    original = no_fault._lock
    results: list[str] = []

    def slow_lock(booking_id: object) -> Booking:
        locked = original(booking_id)
        if not first_locked.is_set():
            first_locked.set()
            time.sleep(0.3)
        return locked

    def sweep() -> None:
        try:
            results.append(f"swept:{no_fault.settle_unanswered(due)}")
        finally:
            connection.close()

    def reply() -> None:
        try:
            no_fault.respond_to_no_fault(
                helper(booking), booking.pk, agree=False, now=due - dt.timedelta(seconds=1)
            )
            results.append("replied")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    no_fault._lock = slow_lock  # type: ignore[assignment]
    try:
        order = [sweep, reply] if sweep_first else [reply, sweep]
        first = threading.Thread(target=order[0])
        first.start()
        assert first_locked.wait(5)
        second = threading.Thread(target=order[1])
        second.start()
        first.join()
        second.join()
    finally:
        no_fault._lock = original  # type: ignore[assignment]

    request = NoFaultRequest.objects.get(booking=booking)
    settled = CancellationSettlement.objects.filter(booking=booking).exists()
    if sweep_first:
        assert sorted(results) == ["NO_FAULT_REPLY_CLOSED", "swept:1"]
        assert (request.response, settled) == ("NO_REPLY", True)
    else:
        assert sorted(results) == ["replied", "swept:0"]
        assert (request.response, settled) == ("DISAGREED", False)
