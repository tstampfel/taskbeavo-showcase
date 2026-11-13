import threading
import time
from collections.abc import Iterator

import pytest
from django.apps import apps
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from opentelemetry import trace

from accounts.tests.factories import make_user
from core.models import ActorKind, AuditEvent, AuditEventError, Outbox
from core.outbox import enqueue
from core.transitions import locked_for_update

tracer = trace.get_tracer(__name__)


class OutboxAuditEvent(AuditEvent):
    """A concrete audit table, the way `booking_events` will subclass AuditEvent."""

    subject = models.ForeignKey(Outbox, on_delete=models.CASCADE, related_name="+")

    class Meta(AuditEvent.Meta):
        app_label = "core"
        db_table = "test_outbox_audit_events"


def _unregister_audit_model() -> None:
    # Registered, the model's `actor` foreign key makes every `User` delete touch its
    # table, which exists only inside `audit_table`'s tests.
    apps.all_models["core"].pop("outboxauditevent", None)
    apps.clear_cache()


# Defining the class registered it; register it only while a test uses its table.
_unregister_audit_model()


@pytest.fixture
def audit_table(db) -> Iterator[None]:
    apps.register_model("core", OutboxAuditEvent)
    # Created inside the test's transaction, so the rollback at the end drops it.
    with connection.schema_editor() as editor:
        editor.create_model(OutboxAuditEvent)
    yield
    _unregister_audit_model()


def make_row() -> Outbox:
    return Outbox.objects.create(topic="test.ok", aggregate_type="core.Test", aggregate_id="1")


@pytest.mark.django_db
def test_audit_event_saves_the_current_trace(audit_table) -> None:
    subject = make_row()

    with tracer.start_as_current_span("transition") as span:
        event = OutboxAuditEvent.objects.create(
            subject=subject, type="released", actor_kind=ActorKind.SYSTEM, data={"cents": 4500}
        )
    context = span.get_span_context()

    assert event.traceparent == (
        f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data", [{"full_name": "Ana"}, {"gate_code": "1"}, {"by": "a@example.com"}]
)
def test_audit_event_data_holds_ids_only(audit_table, data) -> None:
    with pytest.raises(ValueError):
        OutboxAuditEvent.objects.create(
            subject=make_row(), type="released", actor_kind=ActorKind.SYSTEM, data=data
        )


@pytest.mark.django_db
def test_audit_events_are_append_only(audit_table) -> None:
    event = OutboxAuditEvent.objects.create(
        subject=make_row(), type="released", actor_kind=ActorKind.SYSTEM
    )

    event.type = "rewritten"
    with pytest.raises(AuditEventError):
        event.save()
    with pytest.raises(AuditEventError):
        event.delete()
    assert OutboxAuditEvent.objects.get().type == "released"


@pytest.mark.django_db
def test_deleting_the_actor_anonymises_the_event(audit_table, django_user_model) -> None:
    user = make_user("poster")
    event = OutboxAuditEvent.objects.create(
        subject=make_row(), type="cancelled", actor_kind=ActorKind.POSTER, actor=user
    )

    user.delete()

    event.refresh_from_db()
    assert event.actor is None
    assert event.actor_kind == ActorKind.POSTER


@pytest.mark.django_db
def test_locked_for_update_takes_a_row_lock() -> None:
    row = make_row()

    with CaptureQueriesContext(connection) as queries, locked_for_update(Outbox.objects, row.pk):
        pass

    assert any("FOR UPDATE" in query["sql"] for query in queries.captured_queries)


@pytest.mark.django_db
def test_locked_for_update_raises_for_a_missing_row() -> None:
    with pytest.raises(Outbox.DoesNotExist), locked_for_update(Outbox.objects, 0):
        pass


@pytest.mark.django_db
def test_failed_transition_rolls_back_change_audit_and_outbox(
    audit_table, django_capture_on_commit_callbacks
) -> None:
    subject = make_row()

    with (
        django_capture_on_commit_callbacks(execute=False) as callbacks,
        pytest.raises(ValueError),
        locked_for_update(Outbox.objects, subject.pk) as locked,
    ):
        locked.attempts = 99
        locked.save(update_fields=["attempts"])
        OutboxAuditEvent.objects.create(subject=locked, type="changed", actor_kind=ActorKind.SYSTEM)
        enqueue("test.ok", locked)
        raise ValueError("forbidden transition")

    subject.refresh_from_db()
    assert subject.attempts == 0
    assert not OutboxAuditEvent.objects.exists()
    assert list(Outbox.objects.values_list("pk", flat=True)) == [subject.pk]
    assert callbacks == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_transitions_see_each_others_changes() -> None:
    """Two read-check-write transitions on one row: the second waits and sees the first."""
    row = make_row()
    first_locked = threading.Event()

    def bump(wait: bool) -> None:
        try:
            with locked_for_update(Outbox.objects, row.pk) as locked:
                if wait:
                    first_locked.set()
                    time.sleep(0.3)
                locked.attempts += 1
                locked.save(update_fields=["attempts"])
        finally:
            connection.close()

    first = threading.Thread(target=bump, args=(True,))
    first.start()
    assert first_locked.wait(5)
    second = threading.Thread(target=bump, args=(False,))
    second.start()
    first.join()
    second.join()

    row.refresh_from_db()
    assert row.attempts == 2
