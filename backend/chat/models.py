"""Private chat between a task's poster and one helper (docs/PLAN.md, "Contacting people on
the day: chat and push at launch, masked calling later").

A `Conversation` belongs to one task and one helper; the other participant is always the
task's poster. There is at most one per pair: the same thread carries the talk about the
helper's offer and, once the poster accepts it and pays, the booked pair's coordination
(chat/policy.py decides which stage a conversation is in).

A `Message` is stored as shown. Before acceptance that is the contact-detail filter's
output (masked where it matched); the original text is never kept. After acceptance it is
the text as written.

Personal data: `body` is chat text (CLAUDE.md lists it as PII): never log it or put it in
a span, Sentry event or push. Both participants and staff (during a dispute) read it.
Deleting the task (and so its poster) or the helper deletes their conversations and
messages (CASCADE); a booked pair can't be deleted while bookings protect the users.
`chat.gdpr` holds the export.
"""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models

from tasks.models import Task


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="conversations")
    helper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="helper_conversations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_conversations"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["task", "helper"], name="conversation_one_per_pair"),
        ]

    def __str__(self) -> str:
        return f"Conversation {self.pk}"


class Message(models.Model):
    class Role(models.TextChoices):
        POSTER = "POSTER"
        HELPER = "HELPER"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_messages"
    )
    # The sender's role in the conversation when they wrote it.
    sender_role = models.CharField(max_length=10, choices=Role.choices)
    # As stored and shown: masked before acceptance where the filter matched.
    body = models.TextField()
    # The contact-detail filter's verdict (moderation.contact_filter.Verdict); blank when
    # the message was written after acceptance and not filtered.
    moderation_verdict = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_messages"
        ordering: ClassVar = ["created_at", "id"]
        indexes: ClassVar = [
            models.Index(fields=["conversation", "created_at"], name="message_conversation_time"),
        ]

    def __str__(self) -> str:
        # No body: __str__ ends up in logs and admin history.
        return f"Message {self.pk}"
