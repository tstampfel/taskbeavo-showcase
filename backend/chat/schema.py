"""GraphQL API of chat. Rules: chat/services.py and chat/policy.py.

A conversation and its messages are visible to its two participants only: the task's
poster and the helper. Everyone else gets CONVERSATION_NOT_FOUND, and `messageAdded`
re-checks every event against the current conversation (hard rule d). The counterpart is
shown by first name and the initial of the last name, as on the mockup; neither side sees
the other's phone number, email or address through the API (hard rule e).
"""

import datetime as dt
from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any
from uuid import UUID

import strawberry
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractBaseUser
from strawberry.types import Info

from accounts.models import User
from accounts.permissions import PhoneVerified, require_signed_in
from chat import services
from chat.models import Conversation, Message
from chat.policy import Stage, state_of
from core.realtime import LiveEvent, viewer_events
from moderation.contact_filter import Verdict


@strawberry.enum(name="ChatRole", description="A participant's side of a conversation.")
class ChatRoleEnum(Enum):
    POSTER = Message.Role.POSTER.value
    HELPER = Message.Role.HELPER.value


@strawberry.enum(name="ConversationStage")
class ConversationStageEnum(Enum):
    OFFER = strawberry.enum_value(
        Stage.OFFER.value,
        description=(
            "Before acceptance: contact details in new messages are masked, and a message "
            "that may hold one is refused with MESSAGE_CONTACT_DETAILS."
        ),
    )
    BOOKED = strawberry.enum_value(
        Stage.BOOKED.value,
        description="The pair's booking is paid: messages are shown as written.",
    )


@strawberry.type(name="Message", description="A chat message between a poster and a helper.")
class MessageType:
    id: strawberry.ID
    conversation_id: strawberry.ID
    body: str = strawberry.field(description="Before acceptance, contact details are masked.")
    sender_role: ChatRoleEnum
    is_mine: bool
    masked: bool = strawberry.field(description="Contact details were hidden in the body.")
    created_at: dt.datetime

    @classmethod
    def of(cls, message: Message, viewer: User) -> "MessageType":
        return cls(
            id=strawberry.ID(str(message.pk)),
            conversation_id=strawberry.ID(str(message.conversation_id)),
            body=message.body,
            sender_role=ChatRoleEnum(message.sender_role),
            is_mine=message.sender_id == viewer.pk,
            masked=message.moderation_verdict == Verdict.MASKED.value,
            created_at=message.created_at,
        )


@strawberry.type(
    name="Conversation", description="The private chat of a task's poster and one helper."
)
class ConversationType:
    id: strawberry.ID
    task_id: strawberry.ID
    task_title: str
    my_role: ChatRoleEnum
    counterpart_name: str = strawberry.field(
        description="The other participant's first name and last initial, e.g. 'Marko P.'."
    )
    stage: ConversationStageEnum
    can_send: bool = strawberry.field(
        description=(
            "Whether it takes new messages now: before acceptance while the helper's offer "
            "is open, after acceptance until the booking is settled."
        )
    )
    booking_id: strawberry.ID | None = strawberry.field(
        description="The pair's booking, once the poster accepted the helper's offer."
    )
    last_message_at: dt.datetime | None

    @classmethod
    def of(cls, conversation: Conversation, viewer: User) -> "ConversationType":
        state = state_of(conversation)
        role = services.role_of(conversation, viewer)
        other = User.objects.only("first_name", "last_name").get(
            pk=services.other_participant(conversation, viewer.pk)
        )
        return cls(
            id=strawberry.ID(str(conversation.pk)),
            task_id=strawberry.ID(str(conversation.task_id)),
            task_title=conversation.task.title,
            my_role=ChatRoleEnum(role),
            counterpart_name=_short_name(other),
            stage=ConversationStageEnum(state.stage.value),
            can_send=state.can_send,
            booking_id=strawberry.ID(str(state.booking.pk)) if state.booking else None,
            last_message_at=conversation.last_message_at,
        )


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "A conversation you take part in. Errors: UNAUTHENTICATED, CONVERSATION_NOT_FOUND."
        )
    )
    async def conversation(self, info: Info, id: strawberry.ID) -> ConversationType:
        user = _user(info)
        return await sync_to_async(_conversation)(user, str(id))

    @strawberry.field(
        description=(
            "The latest messages of a conversation you take part in, oldest first; pass the "
            "first message's ID as `before` for older ones. `limit` is 1 to 100. Errors: "
            "UNAUTHENTICATED, CONVERSATION_NOT_FOUND."
        )
    )
    async def conversation_messages(
        self,
        info: Info,
        conversation_id: strawberry.ID,
        before: strawberry.ID | None = None,
        limit: int = services.PAGE_SIZE,
    ) -> list[MessageType]:
        user = _user(info)
        return await sync_to_async(_messages)(
            user, str(conversation_id), None if before is None else str(before), limit
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Open the conversation between the poster and the helper of an offer, as either "
            "of them; the same one every time. Errors: UNAUTHENTICATED, "
            "CONVERSATION_NOT_FOUND, CONVERSATION_CLOSED."
        )
    )
    async def open_conversation(self, info: Info, offer_id: strawberry.ID) -> ConversationType:
        user = _user(info)
        return await sync_to_async(_open)(user, str(offer_id))

    @strawberry.mutation(
        permission_classes=[PhoneVerified],
        description=(
            "Send a message (1 to 2000 characters). Before acceptance contact details are "
            "masked. Errors: UNAUTHENTICATED, PHONE_NOT_VERIFIED, CONVERSATION_NOT_FOUND, "
            "CONVERSATION_CLOSED, INVALID_MESSAGE, MESSAGE_CONTACT_DETAILS, RATE_LIMITED."
        ),
    )
    async def send_message(
        self, info: Info, conversation_id: strawberry.ID, body: str
    ) -> MessageType:
        user = _user(info)
        message = await sync_to_async(services.send_message)(user, str(conversation_id), body)
        return MessageType.of(message, user)


@strawberry.type
class Subscription:
    @strawberry.subscription(
        description=(
            "New messages in a conversation you take part in, yours from other devices "
            "included. Errors: UNAUTHENTICATED, CONVERSATION_NOT_FOUND."
        )
    )
    async def message_added(
        self, info: Info, conversation_id: strawberry.ID
    ) -> AsyncGenerator[MessageType, None]:
        wanted = str(conversation_id)
        user = getattr(info.context, "user", None)
        # The connect-time check (hard rule d): CONVERSATION_NOT_FOUND unless the viewer takes
        # part in it now. viewer_events refuses a signed-out socket.
        if isinstance(user, User) and user.is_authenticated:
            await sync_to_async(services.conversation_of)(user, wanted)

        async def load(viewer: AbstractBaseUser, event: LiveEvent) -> MessageType | None:
            return await sync_to_async(_message_for)(viewer, event.aggregate_id, wanted)

        async for message in viewer_events(info, {services.ADDED_TOPIC}, load):
            yield message


def _message_for(
    viewer: AbstractBaseUser, message_id: str, conversation_id: str
) -> MessageType | None:
    """The message, if it is in `conversation_id` and the viewer takes part in that
    conversation now (the per-event check). A malformed ID simply matches nothing."""
    if not isinstance(viewer, User):
        return None
    try:
        message_pk, conversation_pk = UUID(message_id), UUID(conversation_id)
    except ValueError:
        return None
    message = (
        Message.objects.select_related("conversation__task")
        .filter(pk=message_pk, conversation_id=conversation_pk)
        .first()
    )
    if message is None or not services.is_participant(viewer.pk, message.conversation):
        return None
    return MessageType.of(message, viewer)


def _conversation(user: User, conversation_id: str) -> ConversationType:
    return ConversationType.of(services.conversation_of(user, conversation_id), user)


def _open(user: User, offer_id: str) -> ConversationType:
    return ConversationType.of(services.open_conversation(user, offer_id), user)


def _messages(
    user: User, conversation_id: str, before: str | None, limit: int
) -> list[MessageType]:
    messages = services.messages_of(user, conversation_id, before=before, limit=limit)
    return [MessageType.of(message, user) for message in messages]


def _short_name(user: User) -> str:
    initial = user.last_name.strip()[:1]
    return f"{user.first_name.strip()} {initial}." if initial else user.first_name.strip()


def _user(info: Info[Any, Any]) -> User:
    return require_signed_in(info.context.user)
