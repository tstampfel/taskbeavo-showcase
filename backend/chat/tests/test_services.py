"""Chat rules: who may open and write, the contact-detail filter before acceptance only,
and the outbox row each message writes (hard rule c pattern)."""

import pytest
from django.db.models import F

from accounts.models import User
from bookings.models import Booking
from bookings.tests.factories import book, make_booking
from chat import services
from chat.models import Conversation, Message
from chat.policy import Stage, state_of
from core.errors import ApiError, ErrorCode
from core.models import Outbox
from offers import services as offers
from offers.models import Offer
from offers.tests.factories import make_helper, open_task
from tasks.tests.factories import make_poster

pytestmark = pytest.mark.django_db

PHONE = "Call me on 091 234 5678 tomorrow"


def offer_on_flexible_task(helper: User | None = None) -> Offer:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    return offers.send_offer(helper or make_helper(), str(task.pk), 3_000)


def offer_conversation() -> tuple[Offer, Conversation]:
    offer = offer_on_flexible_task()
    return offer, services.open_conversation(offer.helper, str(offer.pk))


def booked_conversation(status: Booking.Status) -> tuple[Booking, Conversation]:
    """The pair's conversation, opened during checkout, with the booking now in `status`."""
    booking = make_booking()
    conversation = services.open_conversation(booking.helper, str(booking.offer_id))
    Booking.objects.filter(pk=booking.pk).update(status=status)
    booking.refresh_from_db()
    return booking, conversation


def codes(error: pytest.ExceptionInfo[ApiError]) -> ErrorCode:
    return error.value.code


# Opening


def test_either_side_of_an_offer_opens_the_same_conversation() -> None:
    offer = offer_on_flexible_task()

    by_helper = services.open_conversation(offer.helper, str(offer.pk))
    by_poster = services.open_conversation(offer.task.poster, str(offer.pk))

    assert by_helper.pk == by_poster.pk
    assert (by_helper.task_id, by_helper.helper_id) == (offer.task_id, offer.helper_id)
    assert Conversation.objects.count() == 1


def test_nobody_else_can_open_a_conversation_on_an_offer() -> None:
    offer = offer_on_flexible_task()

    for stranger in (make_helper("ivan"), make_poster("eve")):
        with pytest.raises(ApiError) as error:
            services.open_conversation(stranger, str(offer.pk))
        assert codes(error) == ErrorCode.CONVERSATION_NOT_FOUND


def test_a_withdrawn_offer_opens_no_new_conversation_but_an_existing_one_stays_readable() -> None:
    offer = offer_on_flexible_task()
    offers.withdraw_offer(offer.helper, str(offer.pk))

    with pytest.raises(ApiError) as error:
        services.open_conversation(offer.task.poster, str(offer.pk))
    assert codes(error) == ErrorCode.CONVERSATION_CLOSED

    other = offer_on_flexible_task(make_helper("ivan"))
    conversation = services.open_conversation(other.helper, str(other.pk))
    offers.withdraw_offer(other.helper, str(other.pk))
    assert services.open_conversation(other.task.poster, str(other.pk)).pk == conversation.pk


def test_the_booked_pair_opens_their_conversation_from_the_accepted_offer() -> None:
    booking, conversation = booked_conversation(Booking.Status.FUNDS_HELD)

    assert services.open_conversation(booking.poster, str(booking.offer_id)).pk == conversation.pk
    state = state_of(conversation)
    assert (state.stage, state.can_send, state.booking) == (Stage.BOOKED, True, booking)


def test_a_paid_booking_opens_a_first_conversation_too() -> None:
    booking = make_booking(Booking.Status.FUNDS_HELD)

    conversation = services.open_conversation(booking.poster, str(booking.offer_id))

    assert state_of(conversation).stage == Stage.BOOKED


# Masking: before acceptance only


def test_before_acceptance_the_helpers_phone_number_is_masked() -> None:
    offer, conversation = offer_conversation()

    message = services.send_message(offer.helper, str(conversation.pk), PHONE)

    assert "5678" not in message.body
    assert "•••" in message.body
    assert message.moderation_verdict == "MASKED"
    assert message.sender_role == Message.Role.HELPER
    # Only the masked text is stored.
    assert not Message.objects.filter(body__contains="234").exists()


def test_before_acceptance_the_posters_email_is_masked_too() -> None:
    offer, conversation = offer_conversation()

    message = services.send_message(
        offer.task.poster, str(conversation.pk), "Write to ana.k@example.com"
    )

    assert "example.com" not in message.body
    assert message.sender_role == Message.Role.POSTER


def test_a_clean_message_is_stored_as_written() -> None:
    offer, conversation = offer_conversation()

    message = services.send_message(offer.helper, str(conversation.pk), "  Hvala, vidimo se!  ")

    assert (message.body, message.moderation_verdict) == ("Hvala, vidimo se!", "CLEAN")


def test_a_doubtful_match_before_acceptance_is_refused() -> None:
    offer, conversation = offer_conversation()

    with pytest.raises(ApiError) as error:
        # Digit groups split by line breaks: doubtful, so held back rather than published.
        services.send_message(offer.helper, str(conversation.pk), "091\n234\n5678")

    assert codes(error) == ErrorCode.MESSAGE_CONTACT_DETAILS
    assert not Message.objects.exists()


def test_while_the_poster_is_paying_messages_are_still_masked() -> None:
    booking, conversation = booked_conversation(Booking.Status.AWAITING_PAYMENT)

    message = services.send_message(booking.helper, str(conversation.pk), PHONE)

    assert state_of(conversation).stage == Stage.OFFER
    assert "5678" not in message.body


@pytest.mark.parametrize(
    "status",
    [Booking.Status.FUNDS_HELD, Booking.Status.PAYMENT_REQUESTED, Booking.Status.DISPUTED],
)
def test_after_acceptance_messages_are_not_masked(status: Booking.Status) -> None:
    booking, conversation = booked_conversation(status)

    message = services.send_message(booking.poster, str(conversation.pk), PHONE)

    assert message.body == PHONE
    assert message.moderation_verdict == ""


def test_a_failed_checkout_brings_the_filter_back() -> None:
    booking, conversation = booked_conversation(Booking.Status.AWAITING_PAYMENT)
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.PAYMENT_FAILED)
    # The state machine reopens the offer with the failed checkout; here by hand.
    Offer.objects.filter(pk=booking.offer_id).update(status=Offer.Status.SENT)

    message = services.send_message(booking.helper, str(conversation.pk), PHONE)

    assert "5678" not in message.body


@pytest.mark.parametrize(
    "pieces",
    [
        ["Call me on 091", "234", "5678"],
        ["My number: 09", "12345", "678"],
        ["Ring 0912", "345678"],
        ["+385", "91", "234", "5678"],
        ["Write to ana.k@", "example", ".com"],
    ],
)
def test_contact_details_sent_in_pieces_are_refused(pieces: list[str]) -> None:
    """Each piece is clean alone; joined to the sender's earlier pieces it is refused as soon
    as the run looks like a phone number or an email, so the whole detail never gets through."""
    offer, conversation = offer_conversation()
    first, *rest = pieces
    services.send_message(offer.helper, str(conversation.pk), first)

    refused = []
    for piece in rest:
        try:
            services.send_message(offer.helper, str(conversation.pk), piece)
        except ApiError as error:
            refused.append(error.code)

    assert refused and set(refused) == {ErrorCode.MESSAGE_CONTACT_DETAILS}
    # Whatever was stored, the whole detail can't be read back from it.
    stored = "".join(conversation.messages.values_list("body", flat=True)).replace(" ", "")
    assert "".join(pieces).replace(" ", "")[-12:] not in stored


@pytest.mark.parametrize(
    "pieces",
    [
        ["I can do it for 30 €", "or 35 with the bags", "Saturday at 9:00?"],
        ["Bus 6 stops at 10:15", "I'll be there by 10:30", "Done in 2 hours, 45 min tops"],
        ["Kuća broj 24", "drugi kat", "oko 14 h"],
    ],
)
def test_prices_and_times_in_separate_messages_still_pass(pieces: list[str]) -> None:
    offer, conversation = offer_conversation()

    for piece in pieces:
        message = services.send_message(offer.helper, str(conversation.pk), piece)
        assert message.body == piece


@pytest.mark.parametrize(
    "numbers",
    [
        ["40", "35", "32", "30"],
        ["120", "100", "90"],
        ["10", "15", "20", "25"],
        ["30", "45", "2", "120"],
    ],
)
def test_bare_prices_and_counts_one_after_another_still_pass(numbers: list[str]) -> None:
    """Haggling or answering "price?", "minutes?", "floor?" with bare numbers makes a long
    joined run of digits, but not a phone-like one (review R2-1)."""
    offer, conversation = offer_conversation()

    for number in numbers:
        message = services.send_message(offer.helper, str(conversation.pk), number)
        assert message.body == number


def test_the_other_sides_messages_are_not_joined_to_the_senders() -> None:
    offer, conversation = offer_conversation()
    services.send_message(offer.task.poster, str(conversation.pk), "Call me on 091")

    message = services.send_message(offer.helper, str(conversation.pk), "234")

    assert message.body == "234"


def test_messages_written_after_acceptance_are_not_joined_once_the_filter_is_back() -> None:
    booking, conversation = booked_conversation(Booking.Status.FUNDS_HELD)
    services.send_message(booking.helper, str(conversation.pk), "My number is 091 234")
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.PAYMENT_FAILED)
    Offer.objects.filter(pk=booking.offer_id).update(status=Offer.Status.SENT)

    message = services.send_message(booking.helper, str(conversation.pk), "5678 thanks")

    assert message.body == "5678 thanks"


def test_a_reopened_task_brings_the_filter_back_for_the_old_pair() -> None:
    booking, conversation = booked_conversation(Booking.Status.REFUNDED)
    Booking.objects.filter(pk=booking.pk).update(task_reopened_at=F("created_at"))

    state = state_of(conversation)

    assert (state.stage, state.can_send) == (Stage.OFFER, False)


# Who may write, and when


def test_sending_needs_a_verified_phone() -> None:
    offer, conversation = offer_conversation()
    User.objects.filter(pk=offer.helper_id).update(phone_verified_at=None)
    offer.helper.refresh_from_db()

    with pytest.raises(ApiError) as error:
        services.send_message(offer.helper, str(conversation.pk), "Hi")

    assert codes(error) == ErrorCode.PHONE_NOT_VERIFIED


def test_only_participants_can_read_or_write() -> None:
    _, conversation = offer_conversation()
    ivan = make_helper("ivan")

    for call in (
        lambda: services.send_message(ivan, str(conversation.pk), "Hi"),
        lambda: services.conversation_of(ivan, str(conversation.pk)),
        lambda: services.messages_of(ivan, str(conversation.pk)),
    ):
        with pytest.raises(ApiError) as error:
            call()
        assert codes(error) == ErrorCode.CONVERSATION_NOT_FOUND


def test_a_malformed_id_is_not_found() -> None:
    offer, _ = offer_conversation()

    with pytest.raises(ApiError) as error:
        services.send_message(offer.helper, "not-a-uuid", "Hi")

    assert codes(error) == ErrorCode.CONVERSATION_NOT_FOUND


@pytest.mark.parametrize("body", ["", "   ", "x" * (services.MAX_MESSAGE + 1)])
def test_empty_and_overlong_messages_are_refused(body: str) -> None:
    offer, conversation = offer_conversation()

    with pytest.raises(ApiError) as error:
        services.send_message(offer.helper, str(conversation.pk), body)

    assert codes(error) == ErrorCode.INVALID_MESSAGE


def test_once_the_offer_is_withdrawn_the_conversation_is_read_only() -> None:
    offer, conversation = offer_conversation()
    services.send_message(offer.helper, str(conversation.pk), "Hi")
    offers.withdraw_offer(offer.helper, str(offer.pk))

    with pytest.raises(ApiError) as error:
        services.send_message(offer.task.poster, str(conversation.pk), "Still there?")

    assert codes(error) == ErrorCode.CONVERSATION_CLOSED
    assert len(services.messages_of(offer.task.poster, str(conversation.pk))) == 1


def test_once_another_helper_is_booked_the_conversation_is_read_only() -> None:
    # A dated task, which checkout needs.
    offer = offers.send_offer(make_helper(), str(open_task().pk), 3_000)
    conversation = services.open_conversation(offer.task.poster, str(offer.pk))
    other = offers.send_offer(make_helper("ivan"), str(offer.task_id), 3_500)

    book(offer.task.poster, other)

    assert state_of(conversation).can_send is False


@pytest.mark.parametrize(
    "status", [Booking.Status.PAID_OUT, Booking.Status.REFUNDED, Booking.Status.CLOSED]
)
def test_once_the_booking_is_settled_the_conversation_is_read_only(status: Booking.Status) -> None:
    booking, conversation = booked_conversation(status)

    with pytest.raises(ApiError) as error:
        services.send_message(booking.helper, str(conversation.pk), "Thanks!")

    assert codes(error) == ErrorCode.CONVERSATION_CLOSED


def test_each_message_writes_an_outbox_row_with_ids_only() -> None:
    offer, conversation = offer_conversation()

    message = services.send_message(offer.helper, str(conversation.pk), "Hi")

    row = Outbox.objects.get(topic=services.ADDED_TOPIC)
    assert row.aggregate_id == str(message.pk)
    assert row.payload == {"conversation_id": str(conversation.pk)}
    conversation.refresh_from_db()
    assert conversation.last_message_at == message.created_at


# Reading


def test_messages_come_oldest_first_in_pages_from_the_latest() -> None:
    offer, conversation = offer_conversation()
    sent = [
        services.send_message(offer.helper, str(conversation.pk), f"Message {n}") for n in range(5)
    ]

    latest = services.messages_of(offer.task.poster, str(conversation.pk), limit=2)
    older = services.messages_of(
        offer.task.poster, str(conversation.pk), before=str(latest[0].pk), limit=2
    )

    assert [m.pk for m in latest] == [sent[3].pk, sent[4].pk]
    assert [m.pk for m in older] == [sent[1].pk, sent[2].pk]
