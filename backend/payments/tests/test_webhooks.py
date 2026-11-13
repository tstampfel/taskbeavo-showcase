import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client, override_settings
from django.utils import timezone
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from payments import webhooks
from payments.models import StripeEvent
from payments.tasks import dispatch_pending_stripe_events
from payments.tests.stripe_fakes import load_event, sign

pytestmark = pytest.mark.django_db

SECRET = "whsec_test_webhook_secret_not_secret"
URL = "/webhooks/stripe"


@pytest.fixture(autouse=True)
def webhook_secret() -> Iterator[None]:
    with override_settings(STRIPE_WEBHOOK_SECRETS=[SECRET]):
        yield


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Registers a recording handler for the fixture event types, for one test."""
    seen: list[str] = []

    def record(event: dict[str, Any]) -> None:
        seen.append(event["id"])

    for event_type in ("payment_intent.succeeded", "charge.refunded", "account.updated"):
        monkeypatch.setitem(webhooks._handlers, event_type, record)
    return seen


def post(
    client: Client, event: dict[str, Any], *, secret: str = SECRET, timestamp: int | None = None
) -> Any:
    body = json.dumps(event).encode()
    return client.post(
        URL,
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": sign(body, secret, timestamp)},
    )


def test_a_signed_event_is_stored_dispatched_and_marked_processed(
    client: Client, calls: list[str]
) -> None:
    event = load_event("payment_intent.succeeded")

    response = post(client, event)

    assert response.status_code == 200
    assert calls == [event["id"]]
    stored = StripeEvent.objects.get()
    assert stored.pk == event["id"]
    assert stored.payload == event
    assert stored.processed_at is not None


def test_a_replayed_event_is_a_no_op(client: Client, calls: list[str]) -> None:
    event = load_event("payment_intent.succeeded")

    first = post(client, event)
    replay = post(client, event)

    assert first.status_code == replay.status_code == 200
    assert calls == [event["id"]]
    assert StripeEvent.objects.count() == 1


def test_a_replay_with_a_changed_body_is_still_a_no_op(client: Client, calls: list[str]) -> None:
    event = load_event("payment_intent.succeeded")
    post(client, event)

    changed = {**event, "data": {"object": {**event["data"]["object"], "amount": 1}}}
    response = post(client, changed)

    assert response.status_code == 200
    assert calls == [event["id"]]
    assert StripeEvent.objects.get().payload == event


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Stripe-Signature": "t=1,v1=deadbeef"},
        {"Stripe-Signature": "garbage"},
    ],
    ids=["missing", "wrong", "malformed"],
)
def test_a_bad_signature_is_rejected_before_anything_is_stored(
    client: Client, calls: list[str], headers: dict[str, str]
) -> None:
    body = json.dumps(load_event("payment_intent.succeeded")).encode()

    response = client.post(URL, data=body, content_type="application/json", headers=headers)

    assert response.status_code == 400
    assert calls == []
    assert not StripeEvent.objects.exists()


def test_an_event_signed_with_another_secret_is_rejected(client: Client, calls: list[str]) -> None:
    response = post(client, load_event("payment_intent.succeeded"), secret="whsec_other")

    assert response.status_code == 400
    assert calls == []


def test_a_stale_signature_is_rejected(client: Client, calls: list[str]) -> None:
    an_hour_ago = int(time.time()) - 3600

    response = post(client, load_event("payment_intent.succeeded"), timestamp=an_hour_ago)

    assert response.status_code == 400
    assert calls == []


def test_a_tampered_body_is_rejected(client: Client, calls: list[str]) -> None:
    body = json.dumps(load_event("payment_intent.succeeded")).encode()
    header = sign(body, SECRET)
    tampered = body.replace(b'"amount": 3360', b'"amount": 1')

    response = client.post(
        URL, data=tampered, content_type="application/json", headers={"Stripe-Signature": header}
    )

    assert response.status_code == 400
    assert calls == []


def test_every_configured_secret_is_accepted(client: Client, calls: list[str]) -> None:
    """The platform and Connect endpoints have their own secrets, and a rotated secret
    overlaps the new one, so STRIPE_WEBHOOK_SECRET may list several."""
    with override_settings(STRIPE_WEBHOOK_SECRETS=["whsec_platform", "whsec_connect"]):
        response = post(client, load_event("account.updated"), secret="whsec_connect")

    assert response.status_code == 200
    assert calls == ["evt_1TestAccountUpdated"]


@override_settings(STRIPE_WEBHOOK_SECRETS=[])
def test_without_a_configured_secret_every_event_is_rejected(
    client: Client, calls: list[str]
) -> None:
    response = post(client, load_event("payment_intent.succeeded"))

    assert response.status_code == 400
    assert calls == []


def test_a_signed_body_that_is_not_an_event_is_rejected(client: Client) -> None:
    body = b'{"not": "an event"}'

    response = client.post(
        URL,
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": sign(body, SECRET)},
    )

    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


def test_an_unhandled_event_type_is_stored_but_not_marked_processed(client: Client) -> None:
    event = {
        **load_event("payment_intent.succeeded"),
        "id": "evt_unhandled",
        "type": "coupon.created",
    }

    response = post(client, event)

    assert response.status_code == 200
    stored = StripeEvent.objects.get()
    assert stored.type == "coupon.created"
    assert stored.processed_at is None


def test_a_failing_handler_rolls_back_so_stripe_redelivers(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def flaky(event: dict[str, Any]) -> None:
        attempts.append(event["id"])
        if len(attempts) == 1:
            raise RuntimeError("downstream unavailable")

    monkeypatch.setitem(webhooks._handlers, "payment_intent.succeeded", flaky)
    event = load_event("payment_intent.succeeded")

    failed = post(client, event)

    assert failed.status_code == 500
    assert not StripeEvent.objects.exists()

    redelivered = post(client, event)

    assert redelivered.status_code == 200
    assert attempts == [event["id"], event["id"]]
    assert StripeEvent.objects.get().processed_at is not None


def test_a_body_that_is_not_utf8_is_rejected_not_an_error(client: Client) -> None:
    body = b"\xff\xfe not utf-8"

    response = client.post(
        URL,
        data=body,
        content_type="application/json",
        headers={"Stripe-Signature": sign(body, SECRET)},
    )

    assert response.status_code == 400
    assert not StripeEvent.objects.exists()


def test_a_live_event_outside_production_is_acknowledged_and_dropped(
    client: Client, calls: list[str]
) -> None:
    event = {**load_event("payment_intent.succeeded"), "livemode": True}

    response = post(client, event)

    assert response.status_code == 200
    assert calls == []
    assert not StripeEvent.objects.exists()


@override_settings(STRIPE_LIVEMODE=True)
def test_a_test_event_in_production_is_acknowledged_and_dropped(
    client: Client, calls: list[str]
) -> None:
    """Stripe sends connected accounts' test events to live Connect endpoints too, signed
    with the live secret; one must never mark a real booking paid."""
    response = post(client, load_event("payment_intent.succeeded"))

    assert response.status_code == 200
    assert calls == []
    assert not StripeEvent.objects.exists()


@override_settings(STRIPE_LIVEMODE=True)
def test_a_live_event_in_production_is_processed(client: Client, calls: list[str]) -> None:
    event = {**load_event("payment_intent.succeeded"), "livemode": True}

    response = post(client, event)

    assert response.status_code == 200
    assert calls == [event["id"]]
    assert StripeEvent.objects.get().livemode is True


def test_a_failed_handler_marks_the_span_as_an_error_without_its_message(
    client: Client, monkeypatch: pytest.MonkeyPatch, spans: InMemorySpanExporter
) -> None:
    def failing(event: dict[str, Any]) -> None:
        raise RuntimeError("Ana Test, ana.test@example.com")

    monkeypatch.setitem(webhooks._handlers, "payment_intent.succeeded", failing)

    assert post(client, load_event("payment_intent.succeeded")).status_code == 500

    span = next(s for s in spans.get_finished_spans() if s.name == "stripe.webhook")
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "RuntimeError"
    (exception,) = span.events
    attributes = dict(exception.attributes or {})
    assert attributes["exception.type"] == "RuntimeError"
    assert "exception.message" not in attributes
    assert "ana.test@example.com" not in str(attributes)


class TestDispatchPending:
    """Events stored while no handler existed (an old instance mid-deploy, or a type
    subscribed before its handler shipped) are handled once a handler is registered."""

    def store_unhandled(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr(webhooks, "_handlers", {})
        event = load_event("payment_intent.succeeded")
        assert post(client, event).status_code == 200
        assert StripeEvent.objects.get().processed_at is None
        return event

    def test_a_stored_event_is_handled_once_its_handler_exists(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = self.store_unhandled(client, monkeypatch)
        seen: list[str] = []
        monkeypatch.setitem(webhooks._handlers, event["type"], lambda e: seen.append(e["id"]))

        assert webhooks.dispatch_pending() == 1
        assert webhooks.dispatch_pending() == 0

        assert seen == [event["id"]]
        assert StripeEvent.objects.get().processed_at is not None

    def test_the_beat_task_runs_the_sweep(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = self.store_unhandled(client, monkeypatch)
        monkeypatch.setitem(webhooks._handlers, event["type"], lambda e: None)

        assert dispatch_pending_stripe_events.delay().get() == 1

    def test_a_redelivery_after_the_sweep_is_still_a_no_op(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = self.store_unhandled(client, monkeypatch)
        seen: list[str] = []
        monkeypatch.setitem(webhooks._handlers, event["type"], lambda e: seen.append(e["id"]))
        webhooks.dispatch_pending()

        post(client, event)

        assert seen == [event["id"]]

    def test_types_without_a_handler_and_trimmed_payloads_are_left_alone(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = self.store_unhandled(client, monkeypatch)
        StripeEvent.objects.update(payload={"trimmed": True}, payload_trimmed_at=timezone.now())
        seen: list[str] = []
        monkeypatch.setitem(webhooks._handlers, "account.updated", lambda e: seen.append("x"))
        monkeypatch.setitem(webhooks._handlers, event["type"], lambda e: seen.append("y"))

        assert webhooks.dispatch_pending() == 0
        assert seen == []

    def test_a_stored_event_from_the_other_mode_is_never_handled(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event = self.store_unhandled(client, monkeypatch)
        StripeEvent.objects.update(livemode=True)
        seen: list[str] = []
        monkeypatch.setitem(webhooks._handlers, event["type"], lambda e: seen.append(e["id"]))

        assert webhooks.dispatch_pending() == 0
        assert seen == []

    def test_a_failing_handler_does_not_block_the_others(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(webhooks, "_handlers", {})
        post(client, load_event("payment_intent.succeeded"))
        post(client, load_event("account.updated"))

        def failing(event: dict[str, Any]) -> None:
            raise RuntimeError("downstream unavailable")

        seen: list[str] = []
        monkeypatch.setitem(webhooks._handlers, "payment_intent.succeeded", failing)
        monkeypatch.setitem(webhooks._handlers, "account.updated", lambda e: seen.append(e["id"]))

        assert webhooks.dispatch_pending() == 1

        assert seen == ["evt_1TestAccountUpdated"]
        unprocessed = StripeEvent.objects.filter(processed_at__isnull=True)
        assert list(unprocessed.values_list("pk", flat=True)) == ["evt_3TestPaymentIntentSucceeded"]


def test_only_post_is_allowed(client: Client) -> None:
    assert client.get(URL).status_code == 405


def test_the_span_carries_the_event_type_and_ids_only(
    client: Client, calls: list[str], spans: InMemorySpanExporter
) -> None:
    post(client, load_event("charge.refunded"))

    span = next(s for s in spans.get_finished_spans() if s.name == "stripe.webhook")
    assert dict(span.attributes or {}) == {
        "stripe.event_type": "charge.refunded",
        "stripe.event_id": "evt_3TestChargeRefunded",
        "stripe.object_id": "ch_3TestDestinationCharge",
    }
    exported = json.dumps([dict(s.attributes or {}) for s in spans.get_finished_spans()])
    for personal in ("ana.test@example.com", "Ana Test", "+4915112345678", "Teststraße"):
        assert personal not in exported


class TestRegistry:
    def test_registering_a_handler_is_one_decorator(
        self, monkeypatch: pytest.MonkeyPatch, client: Client
    ) -> None:
        monkeypatch.setattr(webhooks, "_handlers", {})
        seen: list[str] = []

        @webhooks.stripe_webhook_handler("account.updated")
        def on_account_updated(event: dict[str, Any]) -> None:
            seen.append(event["data"]["object"]["id"])

        post(client, load_event("account.updated"))

        assert seen == ["acct_1TestHelperExpress"]

    def test_one_handler_may_take_several_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(webhooks, "_handlers", {})

        @webhooks.stripe_webhook_handler("payout.paid", "payout.failed")
        def on_payout(event: dict[str, Any]) -> None:
            pass

        assert webhooks._handlers == {"payout.paid": on_payout, "payout.failed": on_payout}

    def test_a_second_handler_for_the_same_type_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(webhooks, "_handlers", {})

        @webhooks.stripe_webhook_handler("account.updated")
        def first(event: dict[str, Any]) -> None:
            pass

        with pytest.raises(ValueError, match=r"account\.updated"):

            @webhooks.stripe_webhook_handler("account.updated")
            def second(event: dict[str, Any]) -> None:
                pass

    def test_handlers_must_name_at_least_one_type(self) -> None:
        with pytest.raises(ValueError):
            webhooks.stripe_webhook_handler()
