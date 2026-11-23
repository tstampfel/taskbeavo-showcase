"""Outbox handlers of the chat app (imported by ChatConfig.ready).

`message.added`: a participant sent a message. It goes live to both participants of the
conversation, never anyone else: the sender's own other devices show it too, and the
`messageAdded` subscription (chat/schema.py) re-checks for every event that the viewer
still takes part in the conversation. The other participant also gets a push (the plan's
"New chat message" row: push, no email). The push says only that a message arrived, never
its text or the sender's name (notifications/kinds.py); the app suppresses the banner
while that conversation is on screen. A message deleted since the row was written (its
conversation went with a deleted task or helper) sends nothing.
"""

from chat.models import Message
from chat.services import ADDED_TOPIC, other_participant
from core.models import Outbox
from core.outbox import register_handler
from core.realtime import send_to_users
from notifications.dispatch import notify


@register_handler(ADDED_TOPIC)
def deliver_message(row: Outbox) -> None:
    message = (
        Message.objects.select_related("conversation__task").filter(pk=row.aggregate_id).first()
    )
    if message is None:
        return
    conversation = message.conversation
    recipient = other_participant(conversation, message.sender_id)
    send_to_users(row, [conversation.task.poster_id, conversation.helper_id])
    notify(row, "message.added", [recipient], actor_id=message.sender_id)
