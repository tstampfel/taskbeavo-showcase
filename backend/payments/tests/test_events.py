import threading
import time
from unittest import mock

import pytest
from django.db import connection, transaction

from payments.events import mark_stripe_event_processed, record_stripe_event
from payments.models import StripeEvent

pytestmark = pytest.mark.django_db

EVENT = {
    "id": "evt_test_1",
    "object": "event",
    "type": "payment_intent.succeeded",
    "livemode": False,
    "data": {"object": {"id": "pi_test_1", "metadata": {"booking_id": "42"}}},
}


def test_first_insert_stores_the_event() -> None:
    assert record_stripe_event(EVENT) is True

    stored = StripeEvent.objects.get()
    assert stored.pk == "evt_test_1"
    assert stored.type == "payment_intent.succeeded"
    assert stored.livemode is False
    assert stored.payload == EVENT
    assert stored.processed_at is None


def test_duplicate_insert_is_a_no_op() -> None:
    record_stripe_event(EVENT)
    redelivered = {**EVENT, "data": {"object": {"id": "pi_changed"}}}

    assert record_stripe_event(redelivered) is False

    stored = StripeEvent.objects.get()
    assert stored.payload == EVENT


def test_duplicate_insert_uses_on_conflict_do_nothing(django_assert_num_queries) -> None:
    record_stripe_event(EVENT)

    with django_assert_num_queries(1) as queries:
        record_stripe_event(EVENT)

    assert "ON CONFLICT (id) DO NOTHING" in queries.captured_queries[0]["sql"]


def test_mark_processed_sets_the_time_once() -> None:
    record_stripe_event(EVENT)
    mark_stripe_event_processed("evt_test_1")
    first = StripeEvent.objects.get().processed_at

    mark_stripe_event_processed("evt_test_1")

    assert first is not None
    assert StripeEvent.objects.get().processed_at == first


class ProcessingError(Exception):
    pass


def test_record_refuses_to_run_outside_a_transaction() -> None:
    # The test's own transaction is not the webhook's; faking autocommit shows the misuse.
    with (
        pytest.raises(RuntimeError, match="inside the transaction"),
        mock.patch.object(connection, "in_atomic_block", False),
    ):
        record_stripe_event(EVENT)

    assert not StripeEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_redelivery_after_a_failed_run_is_processed() -> None:
    with pytest.raises(ProcessingError), transaction.atomic():
        assert record_stripe_event(EVENT) is True
        raise ProcessingError

    assert not StripeEvent.objects.exists()

    # Stripe redelivers: the event is new again, and this run commits.
    with transaction.atomic():
        assert record_stripe_event(EVENT) is True
        mark_stripe_event_processed(EVENT["id"])

    with transaction.atomic():
        assert record_stripe_event(EVENT) is False
    assert StripeEvent.objects.get().processed_at is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("first_commits", "second_processes"), [(True, False), (False, True)])
def test_concurrent_redelivery_waits_for_the_first_run(
    first_commits: bool, second_processes: bool
) -> None:
    inserted = threading.Event()
    results: dict[str, bool] = {}

    def first() -> None:
        try:
            with transaction.atomic():
                results["first"] = record_stripe_event(EVENT)
                inserted.set()
                # The redelivery arrives while this run is still processing.
                time.sleep(0.5)
                if not first_commits:
                    raise ProcessingError
        except ProcessingError:
            pass
        finally:
            connection.close()

    def second() -> None:
        try:
            with transaction.atomic():
                results["second"] = record_stripe_event(EVENT)
        finally:
            connection.close()

    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert inserted.wait(5)
    started = time.monotonic()
    second_thread = threading.Thread(target=second)
    second_thread.start()
    first_thread.join()
    second_thread.join()

    assert results == {"first": True, "second": second_processes}
    # The redelivery blocked on the first run's insert instead of racing past it.
    assert time.monotonic() - started >= 0.3
    assert StripeEvent.objects.count() == 1
