import json
from datetime import timedelta
from unittest import mock

import pytest
from django.db import transaction
from django.utils import timezone

from payments import tasks
from payments.events import record_stripe_event, trim_old_payloads, trimmed_payload
from payments.models import StripeEvent

pytestmark = pytest.mark.django_db

PERSONAL = {
    "name": "Ana Horvat",
    "email": "ana@example.com",
    "phone": "+385910000000",
    "address": {"line1": "Ulica 1", "city": "Dubrovnik", "postal_code": "20000"},
}


def charge_event(event_id: str) -> dict:
    return {
        "id": event_id,
        "object": "event",
        "type": "charge.succeeded",
        "created": 1793000000,
        "livemode": False,
        "api_version": "2026-09-30.basil",
        "request": {"id": "req_1", "idempotency_key": "booking:42:pay:v1"},
        "data": {
            "object": {
                "id": "ch_1",
                "object": "charge",
                "amount": 5000,
                "amount_captured": 5000,
                "amount_refunded": 0,
                "application_fee_amount": 600,
                "amount_details": {"tip": {}},
                "currency": "eur",
                "billing_details": PERSONAL,
                "receipt_email": PERSONAL["email"],
                "shipping": {"name": PERSONAL["name"], "phone": PERSONAL["phone"]},
                "description": "Shopping for Mama Ana",
                "metadata": {"booking_id": "42", "note": "gate code 1234"},
            }
        },
    }


def store(event: dict, days_ago: int) -> None:
    with transaction.atomic():
        record_stripe_event(event)
    StripeEvent.objects.filter(pk=event["id"]).update(
        received_at=timezone.now() - timedelta(days=days_ago)
    )


def test_old_event_is_trimmed_and_still_rejects_duplicates(settings) -> None:
    settings.STRIPE_EVENT_PAYLOAD_RETENTION_DAYS = 30
    store(charge_event("evt_old"), days_ago=31)

    assert trim_old_payloads() == 1

    row = StripeEvent.objects.get(pk="evt_old")
    assert row.payload_trimmed_at is not None
    assert row.payload == {
        "id": "evt_old",
        "object": "event",
        "type": "charge.succeeded",
        "created": 1793000000,
        "livemode": False,
        "api_version": "2026-09-30.basil",
        "data": {
            "object": {
                "id": "ch_1",
                "object": "charge",
                "currency": "eur",
                "amount": 5000,
                "amount_captured": 5000,
                "amount_refunded": 0,
                "application_fee_amount": 600,
                "metadata": {"booking_id": "42"},
            }
        },
    }
    with transaction.atomic():
        assert record_stripe_event(charge_event("evt_old")) is False


def test_no_personal_field_survives_a_trim() -> None:
    trimmed = json.dumps(trimmed_payload(charge_event("evt_1")))

    for value in ("Ana", "ana@example.com", "385910000000", "Ulica", "Dubrovnik", "1234"):
        assert value not in trimmed
    for key in ("billing_details", "receipt_email", "shipping", "description", "request"):
        assert f'"{key}"' not in trimmed


def test_recent_event_is_untouched(settings) -> None:
    settings.STRIPE_EVENT_PAYLOAD_RETENTION_DAYS = 30
    event = charge_event("evt_recent")
    store(event, days_ago=29)

    assert trim_old_payloads() == 0

    row = StripeEvent.objects.get(pk="evt_recent")
    assert row.payload == event
    assert row.payload_trimmed_at is None


def test_trim_is_idempotent_and_batch_limited() -> None:
    for index in range(3):
        store(charge_event(f"evt_{index}"), days_ago=40)

    assert trim_old_payloads(limit=2) == 2
    first_pass = {row.pk: row.payload_trimmed_at for row in StripeEvent.objects.all()}
    assert trim_old_payloads(limit=2) == 1
    assert trim_old_payloads(limit=2) == 0

    assert StripeEvent.objects.count() == 3
    for row in StripeEvent.objects.all():
        assert row.payload_trimmed_at is not None
        if first_pass[row.pk] is not None:
            assert row.payload_trimmed_at == first_pass[row.pk]


def test_payload_without_an_object_is_trimmed_to_the_event_fields() -> None:
    assert trimmed_payload({"id": "evt_1", "type": "x", "data": "odd"}) == {
        "id": "evt_1",
        "type": "x",
    }


def test_trim_task_requeues_itself_after_a_full_batch(monkeypatch) -> None:
    monkeypatch.setattr(tasks, "TRIM_BATCH_SIZE", 1)
    for index in range(2):
        store(charge_event(f"evt_{index}"), days_ago=40)

    with mock.patch.object(tasks.trim_stripe_event_payloads, "delay") as delay:
        assert tasks.trim_stripe_event_payloads() == 1
    delay.assert_called_once_with()

    with mock.patch.object(tasks.trim_stripe_event_payloads, "delay") as delay:
        assert tasks.trim_stripe_event_payloads() == 1
        assert tasks.trim_stripe_event_payloads() == 0
    delay.assert_called_once_with()
