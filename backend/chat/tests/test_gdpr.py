"""Chat in "Download my data" (chat/gdpr.py; the wiring comes with BEAV-153)."""

import pytest

from chat import services
from chat.gdpr import export_messages
from chat.tests.test_services import offer_on_flexible_task
from offers.tests.factories import make_helper
from tasks.models import Task

pytestmark = pytest.mark.django_db


def test_the_export_holds_both_sides_of_every_conversation_the_user_is_in() -> None:
    # Marko is the helper in one conversation and the poster of the task in another.
    as_helper = offer_on_flexible_task(make_helper("marko"))
    marko = as_helper.helper
    as_poster = offer_on_flexible_task(make_helper("ivan"))
    Task.objects.filter(pk=as_poster.task_id).update(poster=marko)
    as_poster.refresh_from_db()
    # A conversation Marko takes no part in.
    elsewhere = offer_on_flexible_task(make_helper("luka"))

    first = services.open_conversation(marko, str(as_helper.pk))
    services.send_message(marko, str(first.pk), "Hello, I can come on Saturday")
    services.send_message(as_helper.task.poster, str(first.pk), "Great, thank you")
    second = services.open_conversation(marko, str(as_poster.pk))
    services.send_message(as_poster.helper, str(second.pk), "Is the garden gate open?")
    other = services.open_conversation(elsewhere.helper, str(elsewhere.pk))
    services.send_message(elsewhere.helper, str(other.pk), "Not Marko's business")

    exported = export_messages(marko)

    by_conversation = {
        conversation_id: [
            (row["mine"], row["sender_role"], row["body"])
            for row in exported
            if row["conversation_id"] == conversation_id
        ]
        for conversation_id in {row["conversation_id"] for row in exported}
    }
    assert by_conversation == {
        str(first.pk): [
            (True, "HELPER", "Hello, I can come on Saturday"),
            (False, "POSTER", "Great, thank you"),
        ],
        str(second.pk): [(False, "HELPER", "Is the garden gate open?")],
    }
    assert {row["task_id"] for row in exported} == {
        str(as_helper.task_id),
        str(as_poster.task_id),
    }
    assert all(row["created_at"] for row in exported)
