"""Outbox handlers of the bookings app (imported by BookingsConfig.ready).

`booking.updated`: a live event to the booking's poster and helper, the only users who
may see it. The subscription that delivers it, and the push and email for each status,
come with the booking screens (BEAV-111, BEAV-115). The row carries IDs only; the
receiver reloads the booking.

`booking.offer_declined` and `booking.offer_reopened`: a live event to the helper whose
offer the poster's acceptance declined, or a failed checkout reopened. No push yet: a
failed checkout reopens the offer, so the kind push waits for FUNDS_HELD.

`booking.funds_held`: the payment is held (the webhook, hard rule a). The accepted
helper gets a push and email that their offer was accepted; every helper whose offer the
acceptance declined (`Booking.declined_offer_ids`) gets the kind push ("helpers notified
kindly", plan); the poster gets the contract confirmation email (bookings/contract_email.py).
Service messages, no names, prices or text in the pushes.

`booking.payment_attempt_failed`: a card was declined or 3D Secure failed. The poster's
app gets a live event and offers another card; nothing else, since the poster is in the
payment sheet. `booking.payment_action_required` (a 3D Secure or bank-app confirmation
started) is handled the same way.

`booking.payout_failed`: the helper's bank returned a payout (bookings/payouts.py). The
helper gets a live event, and a push and email to update their bank details in Stripe.

`booking.access_details_updated`: the poster changed the access details during an
active booking. The accepted helper gets a live event and a push that says only that
they changed; the app loads the details. Nothing goes out once the booking is no longer
active, since the helper can't see the details any more.

`booking.late_payment`: a payment succeeded on a checkout that had already failed. The
handler queues the full refund (`bookings.checkout.settle_failed`), a Celery task, so the
Stripe call holds no row lock. It runs again on every redelivery; the task is idempotent.

`booking.settlement_opened`: a cancel or withdrawal opened its settlement
(bookings/settlement.py). The handler queues `bookings.cancellation.settle`, a Celery task,
so the Stripe calls hold no row lock; it is idempotent.

`booking.cancelled_by_poster`: the poster cancelled or withdrew (bookings/cancel.py). The
helper gets a live event and the `booking.cancelled` push and email, so they don't go.

`booking.withdrawal_received`: the poster's withdrawal statement is in. They get the
confirmation email on a durable medium (bookings/withdrawal_email.py).

`booking.cancellation_settled`: Stripe confirmed the poster's refund (REFUNDED or
PARTIALLY_SETTLED). The poster gets a push and email that the refund is on its way. BEAV-117
hooks the payout of the helper's compensation to this topic.

`booking.cancelled_by_helper`: the helper cancelled, or support rejected their no-fault
reason (bookings/helper_cancel.py). The poster gets a live event and the
`booking.cancelled` push and email; the app offers to re-open the task.

`booking.no_fault_requested`: one side cited a no-fault reason (bookings/no_fault.py). The
other side gets a live event and the `booking.cancellation_requested` push and email, to
agree or disagree within 48 hours.

`booking.no_fault_for_support`: the request waits for support (a disagreement, or the
helper's request left unanswered). Whoever asked gets the `booking.cancellation_under_review`
push and email; both get a live event.

`booking.no_fault_decided`: support decided a no-fault request. Both sides get a live event
and the `booking.no_fault_upheld` or `booking.no_fault_rejected` push and email (the outcome,
and that the reason was or wasn't accepted); the amounts follow in the app.

`booking.no_fault_stood`: a no-fault request stood without support (the other side agreed,
or the poster's went unanswered for 48 hours). Both sides get a live event and the
`booking.no_fault_cancelled` push and email.

`booking.task_reopened`: the poster re-opened the task after a helper or no-fault cancel.
Each helper whose declined offer came back (`offer_ids`) gets the `offer.task_reopened`
push; the offers' own live events went out with OFFER_REOPENED_TOPIC.

`booking.arrival`: a step of the day of the job (bookings/arrival.py): a helper's tap, a
poster's answer, or a reminder, prompt or question of the timeline. Both sides get a live
event; the step's push (and email, by the kind's rule) goes to whoever must hear or act
(`ARRIVAL_PUSHES`). The payload's `step` is an enum key; no ETA or time in the push.

`booking.late_payment_refunded`: that refund was made. The poster gets a push and an
email, in their language, saying their payment was refunded in full.
"""

from bookings import arrival, contract_email, settlement, withdrawal_email
from bookings.cancel import CANCELLED_TOPIC, WITHDRAWAL_RECEIVED_TOPIC
from bookings.checkout import (
    ACTION_REQUIRED_TOPIC,
    FUNDS_HELD_TOPIC,
    LATE_PAYMENT_REFUNDED_TOPIC,
    LATE_PAYMENT_TOPIC,
    PAYMENT_ATTEMPT_FAILED_TOPIC,
    settle_after_commit,
)
from bookings.helper_cancel import CANCELLED_BY_HELPER_TOPIC, TASK_REOPENED_TOPIC
from bookings.models import Booking
from bookings.no_fault import DECIDED_TOPIC as NO_FAULT_DECIDED_TOPIC
from bookings.no_fault import REQUESTED_TOPIC as NO_FAULT_REQUESTED_TOPIC
from bookings.no_fault import STOOD_TOPIC as NO_FAULT_STOOD_TOPIC
from bookings.no_fault import SUPPORT_TOPIC as NO_FAULT_SUPPORT_TOPIC
from bookings.payouts import PAYOUT_FAILED_TOPIC
from bookings.state_machine import (
    ACCESS_DETAILS_TOPIC,
    OFFER_DECLINED_TOPIC,
    OFFER_REOPENED_TOPIC,
    UPDATED_TOPIC,
)
from core.models import Outbox
from core.outbox import register_handler
from core.realtime import send_to_users
from notifications.dispatch import notify
from offers.models import Offer


@register_handler(UPDATED_TOPIC)
def send_live_event(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.poster_id, booking.helper_id])


@register_handler(OFFER_DECLINED_TOPIC)
@register_handler(OFFER_REOPENED_TOPIC)
def tell_the_helper(row: Outbox) -> None:
    offer = Offer.objects.filter(pk=row.aggregate_id).first()
    if offer is None:
        return
    send_to_users(row, [offer.helper_id])


@register_handler(ACCESS_DETAILS_TOPIC)
def tell_the_helper_about_access(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None or not booking.is_active:
        return
    send_to_users(row, [booking.helper_id])
    notify(row, "booking.access_details_updated", [booking.helper_id], actor_id=booking.poster_id)


@register_handler(LATE_PAYMENT_TOPIC)
def refund_the_late_payment(row: Outbox) -> None:
    settle_after_commit(row.aggregate_id)


@register_handler(LATE_PAYMENT_REFUNDED_TOPIC)
def tell_the_poster_about_the_refund(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None or not booking.late_payment_refund_id:
        return
    notify(row, "booking.late_payment_refunded", [booking.poster_id])


@register_handler(FUNDS_HELD_TOPIC)
def confirm_the_booking(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    notify(row, "booking.offer_accepted", [booking.helper_id], actor_id=booking.poster_id)
    declined = Offer.objects.filter(
        pk__in=booking.declined_offer_ids, status=Offer.Status.DECLINED
    ).values_list("helper_id", flat=True)
    notify(row, "booking.offer_declined", declined, actor_id=booking.poster_id)
    notify(row, contract_email.KIND, [booking.poster_id])


@register_handler(PAYMENT_ATTEMPT_FAILED_TOPIC)
@register_handler(ACTION_REQUIRED_TOPIC)
def tell_the_poster_how_the_card_attempt_went(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.poster_id])


@register_handler(PAYOUT_FAILED_TOPIC)
def tell_the_helper_about_the_payout(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.helper_id])
    notify(row, "payouts.failed", [booking.helper_id])


@register_handler(settlement.OPENED_TOPIC)
def run_the_settlement(row: Outbox) -> None:
    settlement.settle_after_commit(row.aggregate_id)


@register_handler(CANCELLED_TOPIC)
def tell_the_helper_about_the_cancel(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.helper_id])
    notify(row, "booking.cancelled", [booking.helper_id], actor_id=booking.poster_id)


@register_handler(WITHDRAWAL_RECEIVED_TOPIC)
def confirm_the_withdrawal(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    notify(row, withdrawal_email.KIND, [booking.poster_id])


@register_handler(settlement.SETTLED_TOPIC)
def tell_the_poster_about_the_refund_of_the_cancel(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    notify(row, "booking.cancellation_refunded", [booking.poster_id])


@register_handler(CANCELLED_BY_HELPER_TOPIC)
def tell_the_poster_about_the_cancel(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.poster_id])
    if row.payload.get("decided"):
        return  # support's decision already told the poster (booking.no_fault_rejected)
    notify(row, "booking.cancelled", [booking.poster_id], actor_id=booking.helper_id)


@register_handler(NO_FAULT_REQUESTED_TOPIC)
def ask_the_other_side(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    asked_by_poster = row.payload.get("requested_by") == "POSTER"
    other, asker = (
        (booking.helper_id, booking.poster_id)
        if asked_by_poster
        else (booking.poster_id, booking.helper_id)
    )
    send_to_users(row, [other])
    notify(row, "booking.cancellation_requested", [other], actor_id=asker)


@register_handler(NO_FAULT_SUPPORT_TOPIC)
def tell_the_asker_it_is_under_review(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    asker = booking.poster_id if row.payload.get("requested_by") == "POSTER" else booking.helper_id
    send_to_users(row, [booking.poster_id, booking.helper_id])
    notify(row, "booking.cancellation_under_review", [asker])


@register_handler(NO_FAULT_DECIDED_TOPIC)
def tell_both_sides_the_decision(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    upheld = row.payload.get("decision") == "UPHELD"
    sides = [booking.poster_id, booking.helper_id]
    send_to_users(row, sides)
    notify(row, "booking.no_fault_upheld" if upheld else "booking.no_fault_rejected", sides)


@register_handler(NO_FAULT_STOOD_TOPIC)
def tell_both_sides_it_is_cancelled(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    sides = [booking.poster_id, booking.helper_id]
    send_to_users(row, sides)
    notify(row, "booking.no_fault_cancelled", sides)


@register_handler(TASK_REOPENED_TOPIC)
def tell_the_helpers_the_task_is_open(row: Outbox) -> None:
    offer_ids = row.payload.get("offer_ids") or []
    helpers = Offer.objects.filter(pk__in=offer_ids, status__in=Offer.LIVE).values_list(
        "helper_id", flat=True
    )
    notify(row, "offer.task_reopened", list(helpers))


# step -> the pushes it sends: (notification kind, who gets it: poster, helper or both). A
# step not listed sends only the live event.
ARRIVAL_PUSHES: dict[str, tuple[tuple[str, str], ...]] = {
    arrival.Step.ON_MY_WAY: (("booking.helper_on_my_way", "poster"),),
    arrival.Step.RUNNING_LATE: (("booking.helper_running_late", "poster"),),
    arrival.Step.ARRIVED: (("booking.helper_arrived", "poster"),),
    arrival.Step.LATE_ACCEPTED: (("booking.late_arrival_accepted", "helper"),),
    arrival.Step.NO_SHOW_REPORTED: (("booking.no_show_reported", "helper"),),
    arrival.Step.NO_SHOW_CONTESTED: (("booking.no_show_contested", "poster"),),
    arrival.Step.NO_SHOW_CONFIRMED: (
        ("booking.no_show_confirmed", "helper"),
        # The poster may not be in the app when the 24 h end (review R1-4).
        ("booking.no_show_refund", "poster"),
    ),
    arrival.Step.NO_SHOW_UPHELD: (("booking.no_show_upheld", "both"),),
    arrival.Step.NO_SHOW_REJECTED: (("booking.no_show_rejected", "both"),),
    arrival.Step.REMINDER_2H: (("booking.reminder_2h", "helper"),),
    arrival.Step.REMINDER_30M: (("booking.reminder_30m", "helper"),),
    arrival.Step.ON_THE_WAY_PROMPT: (("booking.on_the_way_prompt", "helper"),),
    arrival.Step.ASK_POSTER: (("booking.did_helper_come", "poster"),),
    arrival.Step.EARLY_START_REQUESTED: (("booking.early_start_requested", "poster"),),
    arrival.Step.EARLY_START_CONFIRMED: (("booking.early_start_confirmed", "helper"),),
    arrival.Step.EARLY_START_DECLINED: (("booking.early_start_declined", "helper"),),
}


@register_handler(arrival.ARRIVAL_TOPIC)
def tell_both_about_the_arrival(row: Outbox) -> None:
    booking = Booking.objects.filter(pk=row.aggregate_id).first()
    if booking is None:
        return
    send_to_users(row, [booking.poster_id, booking.helper_id])
    for kind, side in ARRIVAL_PUSHES.get(row.payload.get("step", ""), ()):
        users = {
            "poster": [booking.poster_id],
            "helper": [booking.helper_id],
            "both": [booking.poster_id, booking.helper_id],
        }[side]
        notify(row, kind, users)
