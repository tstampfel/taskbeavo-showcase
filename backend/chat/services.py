"""Opening conversations, sending and reading messages (docs/PLAN.md, "Contacting people on
the day"; trust-safety skill section 2). The stage rules are in chat/policy.py.

- Who: a conversation's participants are the task's poster and its helper; anyone else
  gets CONVERSATION_NOT_FOUND, so IDs reveal nothing.
- Opening: either side of an offer opens the pair's one conversation
  (`open_conversation`). A new one needs the offer to be open or booked
  (CONVERSATION_CLOSED otherwise); an existing one always opens, to read.
- Sending (`send_message`) needs a verified mobile number (PHONE_NOT_VERIFIED, plan:
  "Sign-up and login"), but not an account out of limited mode: a suspended user can still
  finish open bookings, and talking is part of that (accounts.permissions). Before
  acceptance the text passes the contact-detail filter: clear matches are masked and the
  masked text is stored; a doubtful match is refused with MESSAGE_CONTACT_DETAILS, as for
  offer messages, because chat has no review queue. After acceptance the text is stored
  as written.
- Notifying: each message writes the outbox row `message.added` in the same transaction;
  chat/outbox_handlers.py sends it live to both participants and by push to the other.

Never log message text: only verdicts and kinds of contact detail.
"""

import logging
import re
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from accounts.models import User
from accounts.permissions import require_signed_in, require_verified_phone
from chat.models import Conversation, Message
from chat.policy import ConversationState, state_of
from core.errors import ApiError, ErrorCode
from core.outbox import enqueue
from moderation.contact_filter import Verdict, filter_contact_details
from offers.models import Offer
from offers.services import visible_to_poster

logger = logging.getLogger(__name__)

MAX_MESSAGE = 2_000
PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
ADDED_TOPIC = "message.added"
# Before acceptance a new message is also screened together with the sender's previous few,
# so a phone number or email sent in pieces ("091", "234 5678") is caught too.
JOINED_SCREEN_PREVIOUS = 3
# A joined run of digits counts as a phone number the way the filter's own rule for split
# digit groups does: 9 or more digits starting with 0, 00 or +. Bare prices and counts sent
# one after another ("40", "35", "30") make a long run too, but don't start that way.
_PHONE_LIKE = re.compile(r"(?<![\d+])(?:\+|0)\d{8,}")


def open_conversation(viewer: User, offer_id: str) -> Conversation:
    """The conversation between the poster and the helper of `offer_id`, created if needed."""
    viewer = require_signed_in(viewer)
    offer = _offer_of_participant(viewer, offer_id)
    existing = _pair(offer).first()
    if existing is not None:
        return existing
    candidate = Conversation(task=offer.task, helper_id=offer.helper_id)
    if not state_of(candidate).can_send:
        raise ApiError(ErrorCode.CONVERSATION_CLOSED, "The offer is no longer open.")
    try:
        with transaction.atomic():
            candidate.save()
    except IntegrityError:
        # The other side opened it at the same moment (conversation_one_per_pair).
        return _pair(offer).get()
    return candidate


def send_message(sender: User, conversation_id: str, body: str) -> Message:
    """Add `sender`'s message to a conversation they take part in."""
    sender = require_verified_phone(sender)
    text = body.strip()
    if not text or len(text) > MAX_MESSAGE:
        raise ApiError(
            ErrorCode.INVALID_MESSAGE, f"The message must be 1 to {MAX_MESSAGE} characters."
        )
    with transaction.atomic():
        # Locked so messages of one conversation are written one after another.
        conversation = _of_participant(
            Conversation.objects.select_for_update(of=("self",)), sender, conversation_id
        )
        state = state_of(conversation)
        if not state.can_send:
            raise ApiError(ErrorCode.CONVERSATION_CLOSED, "The conversation is closed.")
        stored, verdict = _screen(sender, conversation, text, state)
        message = Message.objects.create(
            conversation=conversation,
            sender=sender,
            sender_role=role_of(conversation, sender),
            body=stored,
            moderation_verdict=verdict,
        )
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_at"])
        enqueue(
            ADDED_TOPIC,
            aggregate=message,
            payload={"conversation_id": str(conversation.pk)},
        )
    return message


def conversation_of(viewer: User, conversation_id: str) -> Conversation:
    """A conversation `viewer` takes part in; CONVERSATION_NOT_FOUND otherwise."""
    return _of_participant(Conversation.objects.all(), require_signed_in(viewer), conversation_id)


def messages_of(
    viewer: User, conversation_id: str, *, before: str | None = None, limit: int = PAGE_SIZE
) -> list[Message]:
    """The latest `limit` messages (before the message `before`, for older pages), oldest
    first."""
    conversation = conversation_of(viewer, conversation_id)
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    messages = conversation.messages.all()
    if before is not None:
        cursor = messages.filter(pk=_uuid(before)).first()
        if cursor is None:
            return []
        messages = messages.filter(
            Q(created_at__lt=cursor.created_at) | Q(created_at=cursor.created_at, id__lt=cursor.id)
        )
    page = list(messages.order_by("-created_at", "-id")[:limit])
    page.reverse()
    return page


def is_participant(user_id: int, conversation: Conversation) -> bool:
    return user_id in (conversation.task.poster_id, conversation.helper_id)


def role_of(conversation: Conversation, user: User) -> Message.Role:
    return Message.Role.HELPER if user.pk == conversation.helper_id else Message.Role.POSTER


def other_participant(conversation: Conversation, user_id: int) -> int:
    poster_id = conversation.task.poster_id
    return conversation.helper_id if user_id == poster_id else poster_id


def _screen(
    sender: User, conversation: Conversation, text: str, state: ConversationState
) -> tuple[str, str]:
    """The text as stored, and the filter's verdict (blank when not filtered)."""
    if not state.filtered:
        return text, ""
    result = filter_contact_details(text, sender.locale)
    if result.verdict == Verdict.NEEDS_REVIEW:
        raise ApiError(ErrorCode.MESSAGE_CONTACT_DETAILS, "The message may hold contact details.")
    if _pieces_hold_contact_details(sender, conversation, result.text):
        logger.info("Chat message refused: contact details split across messages")
        raise ApiError(ErrorCode.MESSAGE_CONTACT_DETAILS, "The message may hold contact details.")
    if result.verdict != Verdict.CLEAN:
        logger.info(
            "Chat message screened",
            extra={"verdict": result.verdict.value, "kinds": sorted(k.value for k in result.kinds)},
        )
    return result.text, result.verdict.value


def _pieces_hold_contact_details(sender: User, conversation: Conversation, stored: str) -> bool:
    """Whether the new message (as it would be stored, already masked) joined to the sender's
    previous few filtered messages holds contact details that none of them holds alone.

    Only messages written before acceptance count (a non-blank verdict): after a failed
    checkout the pair's earlier, unfiltered messages may hold a phone number on purpose.
    The pieces are joined once with spaces and once with nothing between them, so both
    "091" + "234 5678" and "ana.k" + "@example.com" read as one detail. A joined match counts
    when the filter masks it, or holds it for review and its digits are phone-like (9 or
    more starting with 0, 00 or +). Stored bodies are already masked, so a detail masked in a
    single message doesn't match again.
    """
    previous = list(
        conversation.messages.filter(sender=sender)
        .exclude(moderation_verdict="")
        .order_by("-created_at", "-id")
        .values_list("body", flat=True)[:JOINED_SCREEN_PREVIOUS]
    )
    if not previous:
        return False
    pieces = [body.strip() for body in reversed(previous)] + [stored.strip()]
    for separator in (" ", ""):
        joined = separator.join(pieces)
        verdict = filter_contact_details(joined, sender.locale).verdict
        # A clear match counts; a doubtful one only when its digits look like a phone number,
        # so a run of bare prices or counts isn't refused (review R2-1).
        if verdict == Verdict.MASKED or (
            verdict == Verdict.NEEDS_REVIEW and _PHONE_LIKE.search("".join(joined.split()))
        ):
            return True
    return False


def _of_participant(
    conversations: QuerySet[Conversation], viewer: User, conversation_id: str
) -> Conversation:
    conversation = (
        conversations.select_related("task")
        .filter(Q(helper=viewer) | Q(task__poster=viewer), pk=_uuid(conversation_id))
        .first()
    )
    if conversation is None:
        raise ApiError(ErrorCode.CONVERSATION_NOT_FOUND, "No such conversation.")
    return conversation


def _offer_of_participant(viewer: User, offer_id: str) -> Offer:
    """The offer, if `viewer` is its helper or the poster who may see it."""
    offers = Offer.objects.select_related("task").filter(pk=_uuid(offer_id))
    offer = offers.filter(helper=viewer).first()
    if offer is None:
        offer = visible_to_poster(offers.filter(task__poster=viewer), viewer).first()
    if offer is None:
        raise ApiError(ErrorCode.CONVERSATION_NOT_FOUND, "No such offer.")
    return offer


def _pair(offer: Offer) -> QuerySet[Conversation]:
    return Conversation.objects.select_related("task").filter(
        task_id=offer.task_id, helper_id=offer.helper_id
    )


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise ApiError(ErrorCode.CONVERSATION_NOT_FOUND, "No such conversation.") from None
