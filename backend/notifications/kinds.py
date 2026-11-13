"""What each kind of notification says and on which channels it goes out.

The rules follow the plan's table ("GraphQL subscriptions deliver live updates, push
covers the rest"): every event pushes; money, cancellation and dispute events also
email. A kind's text is lock-screen safe: no names, addresses, amounts or chat text,
only that something happened and where to look. The app loads the details after the
tap. Text is translated with gettext in the recipient's language (core.i18n).

`route` is the app path the push opens and the email links to. It is formatted with the
outbox row's payload (IDs only) plus `id`, the row's aggregate ID.
"""

import enum
from dataclasses import dataclass
from typing import Any

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from core.models import Outbox
from notifications.models import Category


class EmailRule(enum.Enum):
    ALWAYS = "always"
    # Only when the user has no device to push to, so web-only users still hear of it.
    NO_PUSH_DEVICE = "no_push_device"
    NEVER = "never"


@dataclass(frozen=True)
class Kind:
    key: str
    category: Category
    email: EmailRule
    title: Promise
    body: Promise
    route: str
    # False for an email-only kind, such as the contract confirmation.
    push: bool = True

    def route_for(self, row: Outbox) -> str:
        fields: dict[str, Any] = {**row.payload, "id": row.aggregate_id}
        return self.route.format(**fields)


KINDS: dict[str, Kind] = {
    kind.key: kind
    for kind in (
        Kind(
            # Email: posters with no app signed in get one per offer, so web-only posters
            # hear of it; the plan's daily digest (BEAV-178) replaces that email.
            "offer.received",
            Category.OFFERS,
            EmailRule.NO_PUSH_DEVICE,
            _("New offer on your task"),
            _("A helper sent you an offer. Open TaskBeavo to compare offers."),
            "/tasks/{task_id}",
        ),
        Kind(
            # A day before an offer on the poster's task expires (offers/services.py).
            # Email: like new offers, only to posters with no app signed in.
            "offer.expiring",
            Category.OFFERS,
            EmailRule.NO_PUSH_DEVICE,
            _("An offer expires soon"),
            _("An offer on your task expires within a day. Open TaskBeavo to compare offers."),
            "/tasks/{task_id}",
        ),
        Kind(
            # The helper withdrew an offer on the poster's task (offers/services.py).
            "offer.withdrawn",
            Category.OFFERS,
            EmailRule.NEVER,
            _("An offer was withdrawn"),
            _("A helper withdrew their offer on your task. Open TaskBeavo to see your offers."),
            "/tasks/{task_id}",
        ),
        Kind(
            # TaskBeavo withdrew a private helper's offer: the task's poster books for a
            # business, so registered helpers only (accounts.business). Neutral, rule k.
            "offer.withdrawn_registered_only",
            Category.OFFERS,
            EmailRule.NO_PUSH_DEVICE,
            _("Your offer was withdrawn"),
            _(
                "This task now takes offers from registered businesses only, so your offer "
                "was withdrawn. Open TaskBeavo to find more tasks."
            ),
            "/tasks/{task_id}",
        ),
        Kind(
            "booking.offer_accepted",
            Category.BOOKING,
            EmailRule.ALWAYS,
            _("Your offer was accepted"),
            _("The poster accepted your offer. Open TaskBeavo to see the booking."),
            "/bookings/{id}",
        ),
        Kind(
            "booking.offer_declined",
            Category.BOOKING,
            EmailRule.NEVER,
            _("Your offer was declined"),
            _("The poster chose another offer. Open TaskBeavo to find more tasks."),
            "/tasks/{task_id}",
        ),
        Kind(
            # The chat text never goes in a push or email; the handler skips users who
            # are viewing the conversation.
            "message.added",
            Category.MESSAGES,
            EmailRule.NEVER,
            _("New message"),
            _("You have a new message. Open TaskBeavo to read it."),
            "/conversations/{id}",
        ),
        Kind(
            "booking.payment_requested",
            Category.PAYMENT,
            EmailRule.ALWAYS,
            _("Payment requested"),
            _("Your helper finished the task and requested payment. Open TaskBeavo to release it."),
            # Opens the "Release payment" screen; releasing still takes the poster's tap.
            "/bookings/{id}/release",
        ),
        Kind(
            "booking.payment_released",
            Category.PAYMENT,
            EmailRule.ALWAYS,
            _("Payment released"),
            _("The poster released your payment. It is on its way to your bank account."),
            "/bookings/{id}",
        ),
        Kind(
            "booking.cancellation_requested",
            Category.BOOKING,
            EmailRule.ALWAYS,
            _("Cancellation requested"),
            _("The other side asked to cancel a booking. Open TaskBeavo to respond."),
            "/bookings/{id}",
        ),
        Kind(
            "booking.cancelled",
            Category.BOOKING,
            EmailRule.ALWAYS,
            _("Booking cancelled"),
            _("A booking was cancelled. Open TaskBeavo for the details."),
            "/bookings/{id}",
        ),
        Kind(
            "booking.change_requested",
            Category.BOOKING,
            EmailRule.NO_PUSH_DEVICE,
            _("Change requested"),
            _(
                "The other side asked to change the price or time of a booking. "
                "Open TaskBeavo to respond."
            ),
            "/bookings/{id}",
        ),
        Kind(
            "booking.dispute_updated",
            Category.BOOKING,
            EmailRule.ALWAYS,
            _("Dispute update"),
            _("There is news about a dispute on your booking. Open TaskBeavo for the details."),
            "/bookings/{id}",
        ),
        Kind(
            # About the helper's own payout account: a service message (plan: "Payout
            # failures and account problems"). Also sent as the reminders.
            "payouts.info_needed",
            Category.PAYMENT,
            EmailRule.ALWAYS,
            _("Stripe needs more information"),
            _(
                "Your offers are paused until you give Stripe the missing details for your "
                "payouts. Open TaskBeavo to continue."
            ),
            "/helper/payouts",
        ),
        Kind(
            "payouts.rejected",
            Category.PAYMENT,
            EmailRule.ALWAYS,
            _("Payout account not approved"),
            _(
                "Stripe couldn't approve your payout account, so you can't send offers for "
                "now. Open TaskBeavo to see what happens next."
            ),
            "/helper/payouts",
        ),
    )
}


# Booking kinds added by BEAV-107, kept apart from the table above.
_BOOKING_DETAIL_KINDS = (
    Kind(
        # The poster changed the doorbell, floor, gate code or key note during an active
        # booking (bookings.state_machine.record_access_details_change). Never the details.
        "booking.access_details_updated",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Access details updated"),
        _("The poster updated how to get in. Open TaskBeavo before you go."),
        "/bookings/{id}",
    ),
    Kind(
        # A payment arrived after the checkout had ended (the offer was withdrawn or the
        # checkout abandoned) and was refunded in full, fee included (bookings.checkout).
        "booking.late_payment_refunded",
        Category.PAYMENT,
        EmailRule.ALWAYS,
        _("Payment refunded"),
        _(
            "Your booking didn't go ahead, so we refunded your payment in full. It can take "
            "a few days to reach your account."
        ),
        "/tasks/{task_id}",
    ),
)
KINDS.update({kind.key: kind for kind in _BOOKING_DETAIL_KINDS})


# Payment kinds added by BEAV-110.
_PAYMENT_KINDS = (
    Kind(
        # The durable-medium confirmation of the contract (Consumer Rights Directive
        # Art. 8(7)), sent to the poster when the payment is held. Its own email template
        # (bookings/contract_email.py); `title` and `body` are its fallback summary.
        "booking.contract_confirmation",
        Category.PAYMENT,
        EmailRule.ALWAYS,
        _("Your booking is confirmed"),
        _("Your payment is held and your booking is confirmed."),
        "/bookings/{id}",
        push=False,
    ),
    Kind(
        # The helper's bank returned a payout (bookings/payouts.py).
        "payouts.failed",
        Category.PAYMENT,
        EmailRule.ALWAYS,
        _("Payout failed"),
        _(
            "Your bank returned a payout. Update your bank details in Stripe; we'll send the "
            "money again once they're fixed."
        ),
        "/helper/payouts",
    ),
)
KINDS.update({kind.key: kind for kind in _PAYMENT_KINDS})


# Cancellation kinds added by BEAV-122.
_CANCELLATION_KINDS = (
    Kind(
        # The durable-medium confirmation of the poster's withdrawal statement (Directive
        # 2023/2673; bookings/withdrawal_email.py). Its own template; `title` and `body`
        # are its fallback summary.
        "booking.withdrawal_confirmation",
        Category.PAYMENT,
        EmailRule.ALWAYS,
        _("Withdrawal received"),
        _("Your withdrawal from the contract is recorded."),
        "/bookings/{id}",
        push=False,
    ),
    Kind(
        # Stripe confirmed the poster's refund of a cancellation (bookings/settlement.py).
        "booking.cancellation_refunded",
        Category.PAYMENT,
        EmailRule.ALWAYS,
        _("Refund on its way"),
        _(
            "Your cancellation is complete and your refund is on its way. It can take a few "
            "days to reach your account."
        ),
        "/bookings/{id}",
    ),
)
KINDS.update({kind.key: kind for kind in _CANCELLATION_KINDS})


# Helper and no-fault cancellation kinds added by BEAV-123.
_HELPER_CANCELLATION_KINDS = (
    Kind(
        # A no-fault cancellation request waits for support: a disagreement, or the
        # helper's request left unanswered (bookings/no_fault.py). To whoever asked.
        "booking.cancellation_under_review",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Cancellation under review"),
        _("Our team is reviewing your cancellation request. We'll let you know the outcome."),
        "/bookings/{id}",
    ),
    Kind(
        # A no-fault request stood without support: the other side agreed, or the poster's
        # went unanswered for 48 hours (bookings/no_fault.py). To both.
        "booking.no_fault_cancelled",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Booking cancelled, no one's fault"),
        _(
            "The no-fault cancellation stands. The booking is cancelled and the poster gets "
            "a full refund."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # Support upheld a no-fault cancellation request (bookings/no_fault.py): to both.
        "booking.no_fault_upheld",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("No-fault cancellation accepted"),
        _(
            "Our team accepted the no-fault reason. The booking is cancelled and the poster "
            "gets a full refund."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # Support rejected a no-fault request: the usual policy applies to whoever asked, as
        # of when they asked (rule g, user decision 2026-11-07). To both.
        "booking.no_fault_rejected",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("No-fault reason not accepted"),
        _(
            "Our team didn't accept the no-fault reason, so the usual cancellation policy "
            "applies. Open TaskBeavo to see what happens next."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # The poster re-opened a task after its helper cancelled (bookings/helper_cancel.py):
        # to each helper whose declined offer is live again.
        "offer.task_reopened",
        Category.OFFERS,
        EmailRule.NO_PUSH_DEVICE,
        _("A task is open again"),
        _("A task you offered on is open again and your offer is back. Open TaskBeavo to see it."),
        "/tasks/{task_id}",
    ),
)
KINDS.update({kind.key: kind for kind in _HELPER_CANCELLATION_KINDS})


# The day of the job, added by BEAV-124 (bookings/arrival.py). The helper's reminders and
# "Are you on your way?" prompt, and the poster's questions, email only users with no app
# signed in (often a poster abroad on the web); the plain updates push only.
_ARRIVAL_KINDS = (
    Kind(
        "booking.reminder_2h",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Your task starts in 2 hours"),
        _("A booked task starts in about 2 hours. Open TaskBeavo to see the details."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.reminder_30m",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Your task starts in 30 minutes"),
        _("A booked task starts in about 30 minutes. Tap On my way when you set off."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.on_the_way_prompt",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Are you on your way?"),
        _(
            "Your booked task started 15 minutes ago. Let the poster know if you're running "
            "late, have arrived or can't come."
        ),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.did_helper_come",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Did your helper come?"),
        _("Your helper should be there by now. Open TaskBeavo to tell us whether they came."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.helper_on_my_way",
        Category.BOOKING,
        EmailRule.NEVER,
        _("Your helper is on the way"),
        _("Your helper set off. Open TaskBeavo to see when they expect to arrive."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.helper_running_late",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Your helper is running late"),
        _(
            "Your helper sent a new arrival time. Open TaskBeavo to keep the booking or "
            "cancel for free."
        ),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.helper_arrived",
        Category.BOOKING,
        EmailRule.NEVER,
        _("Your helper has arrived"),
        _("Your helper says they have arrived."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.late_arrival_accepted",
        Category.BOOKING,
        EmailRule.NEVER,
        _("Your new time is fine"),
        _("The poster is fine with your new arrival time."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        # The poster said the helper didn't come; the helper has 24 h to contest. Email too:
        # it is time-limited and decides whether the helper is paid.
        "booking.no_show_reported",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Were you there?"),
        _(
            "The poster says you didn't come. If you were there, tap I was there within 24 "
            "hours and our team will review it. Otherwise the poster gets a full refund."
        ),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.no_show_contested",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Your helper says they came"),
        _(
            "Your helper says they were there. Our team will review it and let you both "
            "know the outcome."
        ),
        "/bookings/{id}/arrival",
    ),
    Kind(
        # The helper didn't contest within 24 h: to the poster (review R1-4). Money moves.
        "booking.no_show_refund",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Your refund is on its way"),
        _(
            "Your helper didn't answer within 24 hours, so the booking is cancelled and you "
            "get a full refund."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # Support upheld a contested no-show (bookings/arrival.py): to both. Money moves.
        "booking.no_show_upheld",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("No-show confirmed"),
        _(
            "Our team reviewed it and found the helper didn't come, so the booking is "
            "cancelled and the poster gets a full refund. The helper pays nothing."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # Support found the helper was there (bookings/arrival.py): to both.
        "booking.no_show_rejected",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("The booking continues"),
        _(
            "Our team reviewed it and found the helper was there, so the booking continues. "
            "Open TaskBeavo to see what happens next."
        ),
        "/bookings/{id}",
    ),
    Kind(
        # Money: a full refund to the poster, so it emails too.
        "booking.no_show_confirmed",
        Category.BOOKING,
        EmailRule.ALWAYS,
        _("Booking cancelled"),
        _(
            "The poster said you didn't come, so the booking is cancelled and the poster gets "
            "a full refund. You pay nothing. Open TaskBeavo to see the booking."
        ),
        "/bookings/{id}",
    ),
    Kind(
        "booking.early_start_requested",
        Category.BOOKING,
        EmailRule.NO_PUSH_DEVICE,
        _("Your helper can start now"),
        _(
            "Your helper is there early and asks to start now. Open TaskBeavo to confirm or "
            "keep the booked time."
        ),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.early_start_confirmed",
        Category.BOOKING,
        EmailRule.NEVER,
        _("You can start now"),
        _("The poster confirmed an early start. The job starts now."),
        "/bookings/{id}/arrival",
    ),
    Kind(
        "booking.early_start_declined",
        Category.BOOKING,
        EmailRule.NEVER,
        _("Start at the booked time"),
        _("The poster would like to keep the booked start time."),
        "/bookings/{id}/arrival",
    ),
)
KINDS.update({kind.key: kind for kind in _ARRIVAL_KINDS})
