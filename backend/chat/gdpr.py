"""Chat in account deletion and "Download my data" (plan: "GDPR flows").

Deleting the `User` row cascades to the conversations they take part in (as the helper, or
as the poster through their tasks) and to every message in them. A booked pair can't be
deleted while their bookings protect the users (`Booking.poster`/`helper` PROTECT), so the
chat of a booking stays as dispute evidence until the booking's own retention runs out.

TODO(BEAV-153): include `export_messages` in "Download my data", and replace the deleted
user's name in a kept chat with "Former user" once bookings can outlive an account.
"""

from typing import Any

from django.db.models import Q

from accounts.models import User
from chat.models import Message


def export_messages(user: User) -> list[dict[str, Any]]:
    """Every message in the conversations `user` takes part in, the other side's included:
    they are part of the user's conversation (plan: "Download my data")."""
    messages = Message.objects.filter(
        Q(conversation__helper=user) | Q(conversation__task__poster=user)
    ).order_by("conversation_id", "created_at", "id")
    return [
        {
            "conversation_id": str(message.conversation_id),
            "task_id": str(message.conversation.task_id),
            "mine": message.sender_id == user.pk,
            "sender_role": message.sender_role,
            "body": message.body,
            "created_at": message.created_at.isoformat(),
        }
        for message in messages.select_related("conversation")
    ]
