import ast
import inspect
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import stripe
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from payments import stripe_client
from payments.idempotency import idempotency_key
from payments.stripe_client import SettlementSplit, StripeGateway
from payments.tests.stripe_fakes import RecordingHttpClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
HELPER = "acct_1TestHelperExpress"
# Late cancel of a €30 task (fee €3.60): helper keeps 750, TaskBeavo 90 (BEAV-106 handoff).
SPLIT_30 = SettlementSplit(
    charge_cents=3_360,
    application_fee_cents=360,
    helper_compensation_cents=750,
    fee_kept_cents=90,
    refund_cents=2_520,
    transfer_reversal_cents=2_520,
    fee_refund_cents=270,
)


def gateway(
    response: dict[str, Any] | None = None, status: int = 200
) -> tuple[StripeGateway, RecordingHttpClient]:
    http = RecordingHttpClient(response, status)
    client = stripe.StripeClient("sk_test_not_a_real_key", http_client=http, max_network_retries=0)
    return StripeGateway(client), http


# Sample arguments for every public gateway method. A new method must be added here,
# which makes the idempotency-key tests below cover it. Every argument ending in
# `idempotency_key` is a key, listed in the order the method sends its requests.
CALLS: dict[str, dict[str, Any]] = {
    "create_destination_charge": {
        "booking_id": 42,
        "amount_cents": 3360,
        "application_fee_cents": 360,
        "destination_account": HELPER,
        "idempotency_key": idempotency_key("booking", 42, "payment_intent"),
    },
    "cancel_payment_intent": {
        "booking_id": 42,
        "payment_intent": "pi_3TestDestinationCharge",
        "idempotency_key": idempotency_key("offer", 7, "withdraw"),
    },
    "create_full_refund": {
        "booking_id": 42,
        "payment_intent": "pi_3TestDestinationCharge",
        "idempotency_key": idempotency_key("booking", 42, "cancel", "no_fault"),
    },
    "create_express_account": {
        "helper_profile_id": 7,
        "idempotency_key": idempotency_key("helper", 7, "express-account"),
    },
    "create_onboarding_link": {
        "connected_account": HELPER,
        "return_url": "https://taskbeavo.test/helper/payouts/return",
        "refresh_url": "https://taskbeavo.test/helper/payouts/refresh",
        "idempotency_key": idempotency_key("helper", 7, "account-link", "1"),
    },
    "refund_application_fee": {
        "booking_id": 42,
        "application_fee": "fee_1TestFee",
        "split": SPLIT_30,
        "idempotency_key": idempotency_key("booking", 42, "cancel", "fee_refund"),
    },
    "reverse_transfer": {
        "booking_id": 42,
        "transfer": "tr_1TestTransfer",
        "split": SPLIT_30,
        "fee_refund_id": "fr_1TestFeeRefund",
        "idempotency_key": idempotency_key("booking", 42, "cancel", "transfer_reversal"),
    },
    "refund_poster": {
        "booking_id": 42,
        "payment_intent": "pi_3TestDestinationCharge",
        "split": SPLIT_30,
        "transfer_reversal_id": "trr_1TestReversal",
        "idempotency_key": idempotency_key("booking", 42, "cancel", "refund"),
    },
    "create_payout": {
        "booking_id": 42,
        "connected_account": HELPER,
        "amount_cents": 3000,
        "idempotency_key": idempotency_key("booking", 42, "release"),
    },
}


# Read-only methods: GET requests, which take no idempotency key.
READS: dict[str, dict[str, Any]] = {
    "retrieve_account": {"connected_account": HELPER},
    "find_booking_payment_intents": {"booking_id": "0b6a8c1e-1f7c-4a4e-9a51-6a8b1e0c2d3f"},
    "retrieve_payment_intent": {"booking_id": 42, "payment_intent": "pi_3TestDestinationCharge"},
    "retrieve_charge_parts": {"booking_id": 42, "payment_intent": "pi_3TestDestinationCharge"},
    "find_fee_refund": {"booking_id": 42, "application_fee": "fee_1TestFee"},
    "find_transfer_reversal": {"booking_id": 42, "transfer": "tr_1TestTransfer"},
    "find_poster_refund": {"booking_id": 42, "payment_intent": "pi_3TestDestinationCharge"},
}


def key_params(method: str) -> list[str]:
    return [name for name in CALLS[method] if name.endswith("idempotency_key")]


KEY_PARAMS = [(method, name) for method in sorted(CALLS) for name in key_params(method)]


def public_methods() -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(StripeGateway, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_public_method_has_a_sample_call() -> None:
    assert public_methods() == set(CALLS) | set(READS)


@pytest.mark.parametrize("method", sorted(CALLS))
def test_every_stripe_call_carries_the_callers_idempotency_key(method: str) -> None:
    gw, http = gateway()

    getattr(gw, method)(**CALLS[method])

    assert [request.method for request in http.requests] == ["post"] * len(key_params(method))
    assert [request.headers["Idempotency-Key"] for request in http.requests] == [
        CALLS[method][name] for name in key_params(method)
    ]


@pytest.mark.parametrize(("method", "key_param"), KEY_PARAMS)
def test_the_idempotency_key_is_required(method: str, key_param: str) -> None:
    gw, http = gateway()
    kwargs = {k: v for k, v in CALLS[method].items() if k != key_param}

    with pytest.raises(TypeError):
        getattr(gw, method)(**kwargs)

    assert http.requests == []


@pytest.mark.parametrize(("method", "key_param"), KEY_PARAMS)
@pytest.mark.parametrize(
    "key",
    [str(uuid.uuid4()), "", "booking:42:release", "booking:42:release:v0", "booking 42:release:v1"],
    ids=["random", "empty", "no-version", "version-0", "space"],
)
def test_a_key_not_derived_from_the_action_is_refused(
    method: str, key_param: str, key: str
) -> None:
    gw, http = gateway()

    with pytest.raises(ValueError, match="idempotency"):
        getattr(gw, method)(**{**CALLS[method], key_param: key})

    assert http.requests == []


def test_a_destination_charge_is_on_behalf_of_the_helper() -> None:
    gw, http = gateway({"id": "pi_3TestDestinationCharge", "object": "payment_intent"})

    intent = gw.create_destination_charge(**CALLS["create_destination_charge"])

    assert intent.id == "pi_3TestDestinationCharge"
    request = http.requests[0]
    assert request.url.endswith("/v1/payment_intents")
    assert "Stripe-Account" not in request.headers
    assert request.params == {
        "amount": ["3360"],
        "currency": ["eur"],
        "application_fee_amount": ["360"],
        "on_behalf_of": [HELPER],
        "transfer_data[destination]": [HELPER],
        "automatic_payment_methods[enabled]": ["true"],
        "metadata[booking_id]": ["42"],
    }


def test_a_fee_a_credit_covered_sends_no_application_fee() -> None:
    """The poster's TaskBeavo credit took the whole fee off (BEAV-123)."""
    gw, http = gateway({"id": "pi_3TestDestinationCharge", "object": "payment_intent"})

    gw.create_destination_charge(
        **{**CALLS["create_destination_charge"], "amount_cents": 3000, "application_fee_cents": 0}
    )

    params = http.requests[0].params
    assert "application_fee_amount" not in params
    assert (params["amount"], params["transfer_data[destination]"]) == (["3000"], [HELPER])


@pytest.mark.parametrize(
    ("methods", "warned"),
    [(["card", "link", "klarna"], True), (["card", "klarna"], False), (None, False)],
)
def test_a_destination_charge_offering_stripe_link_warns_sentry(
    methods: list[str] | None, warned: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Link is off in the Stripe account settings (Stripe refuses excluded_payment_method_types
    ["link"]); if it comes back on, checkout continues and Sentry gets IDs only (R1-2)."""
    response: dict[str, Any] = {"id": "pi_3TestDestinationCharge", "object": "payment_intent"}
    if methods is not None:
        response["payment_method_types"] = methods
    gw, _ = gateway(response)
    sent: list[tuple[str, str, dict[str, str]]] = []

    class Scope:
        def __init__(self) -> None:
            self.tags: dict[str, str] = {}

        def __enter__(self) -> "Scope":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def set_tag(self, key: str, value: str) -> None:
            self.tags[key] = value

    scope = Scope()
    monkeypatch.setattr(stripe_client.sentry_sdk, "new_scope", lambda: scope)
    monkeypatch.setattr(
        stripe_client.sentry_sdk,
        "capture_message",
        lambda message, level: sent.append((message, level, dict(scope.tags))),
    )

    intent = gw.create_destination_charge(**CALLS["create_destination_charge"])

    assert intent.id == "pi_3TestDestinationCharge"
    if warned:
        assert sent == [
            (
                "Stripe Link is on in the payment method configuration",
                "warning",
                {
                    "booking.id": "42",
                    "stripe.object_id": "pi_3TestDestinationCharge",
                    "support.reason": "STRIPE_LINK_ENABLED",
                },
            )
        ]
    else:
        assert sent == []


@pytest.mark.parametrize(
    "change",
    [
        {"application_fee_cents": -1},
        {"application_fee_cents": True},
        {"application_fee_cents": 3360},
        {"amount_cents": 0},
        {"amount_cents": 33.6},
        {"amount_cents": True},
        {"destination_account": ""},
        {"destination_account": "cus_123"},
    ],
)
def test_a_destination_charge_refuses_bad_amounts_and_accounts(change: dict[str, Any]) -> None:
    gw, http = gateway()

    with pytest.raises(ValueError):
        gw.create_destination_charge(**{**CALLS["create_destination_charge"], **change})

    assert http.requests == []


def test_the_destination_charge_takes_no_payment_method_types() -> None:
    """The sandbox's API version rejects payment_method_types (BEAV-23)."""
    gw, _ = gateway()

    with pytest.raises(TypeError):
        gw.create_destination_charge(
            **CALLS["create_destination_charge"],
            payment_method_types=["card"],  # pyright: ignore[reportCallIssue]
        )


def test_a_full_refund_reverses_the_whole_transfer_and_fee() -> None:
    gw, http = gateway({"id": "re_1TestRefund", "object": "refund"})

    gw.create_full_refund(**CALLS["create_full_refund"])

    assert http.requests[0].url.endswith("/v1/refunds")
    assert http.requests[0].params == {
        "payment_intent": ["pi_3TestDestinationCharge"],
        "reverse_transfer": ["true"],
        "refund_application_fee": ["true"],
        "metadata[booking_id]": ["42"],
    }


def test_cancelling_a_payment_intent_posts_to_its_cancel_endpoint() -> None:
    gw, http = gateway({"id": "pi_3TestDestinationCharge", "object": "payment_intent"})

    gw.cancel_payment_intent(**CALLS["cancel_payment_intent"])

    [request] = http.requests
    assert request.url.endswith("/v1/payment_intents/pi_3TestDestinationCharge/cancel")
    assert request.params == {"cancellation_reason": ["abandoned"]}
    with pytest.raises(ValueError):
        gw.cancel_payment_intent(**{**CALLS["cancel_payment_intent"], "payment_intent": "ch_1"})


def test_a_refused_request_raises_with_stripes_code_and_the_intents_status() -> None:
    """Cancelling a PaymentIntent that already succeeded: Stripe answers 400 with the
    intent attached. The error carries the code and status, never Stripe's message."""
    gw, _ = gateway(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "payment_intent_unexpected_state",
                "message": "You cannot cancel this PaymentIntent because it has a status of "
                "succeeded.",
                "payment_intent": {
                    "id": "pi_3TestDestinationCharge",
                    "object": "payment_intent",
                    "status": "succeeded",
                },
            }
        },
        status=400,
    )

    with pytest.raises(stripe_client.StripeRequestError) as exc:
        gw.cancel_payment_intent(**CALLS["cancel_payment_intent"])

    assert exc.value.code == "payment_intent_unexpected_state"
    assert exc.value.payment_intent_status == "succeeded"
    assert exc.value.http_status == 400
    assert "succeeded" not in str(exc.value)
    assert not exc.value.is_connected_account_problem


@pytest.mark.parametrize(
    "error",
    [
        {"code": "insufficient_capabilities_for_transfer"},
        {"code": "account_invalid"},
        {"param": "transfer_data[destination]"},
        {"param": "on_behalf_of"},
    ],
)
def test_a_charge_the_helpers_account_cant_take_is_flagged(error: dict[str, str]) -> None:
    gw, _ = gateway({"error": {"type": "invalid_request_error", **error}}, status=400)

    with pytest.raises(stripe_client.StripeRequestError) as exc:
        gw.create_destination_charge(**CALLS["create_destination_charge"])

    assert exc.value.is_connected_account_problem


def test_a_payout_is_made_from_the_helpers_own_account() -> None:
    gw, http = gateway({"id": "po_test_1", "object": "payout"})

    gw.create_payout(**CALLS["create_payout"])

    request = http.requests[0]
    assert request.url.endswith("/v1/payouts")
    assert request.headers["Stripe-Account"] == HELPER
    assert request.params == {
        "amount": ["3000"],
        "currency": ["eur"],
        "metadata[booking_id]": ["42"],
    }


def test_an_express_account_takes_destination_charges_and_pays_out_manually() -> None:
    gw, http = gateway({"id": HELPER, "object": "account"})

    account = gw.create_express_account(**CALLS["create_express_account"])

    assert account.id == HELPER
    [request] = http.requests
    assert request.url.endswith("/v1/accounts")
    assert request.params == {
        "type": ["express"],
        "country": ["HR"],
        "capabilities[card_payments][requested]": ["true"],
        "capabilities[transfers][requested]": ["true"],
        "settings[payouts][schedule][interval]": ["manual"],
        "metadata[helper_profile_id]": ["7"],
    }


def test_an_onboarding_link_is_for_the_helpers_account() -> None:
    gw, http = gateway(
        {"object": "account_link", "url": "https://connect.stripe.com/x", "expires_at": 1}
    )

    link = gw.create_onboarding_link(**CALLS["create_onboarding_link"])

    assert link.url == "https://connect.stripe.com/x"
    [request] = http.requests
    assert request.url.endswith("/v1/account_links")
    assert request.params == {
        "account": [HELPER],
        "type": ["account_onboarding"],
        "return_url": ["https://taskbeavo.test/helper/payouts/return"],
        "refresh_url": ["https://taskbeavo.test/helper/payouts/refresh"],
    }
    with pytest.raises(ValueError):
        gw.create_onboarding_link(**{**CALLS["create_onboarding_link"], "connected_account": "x"})


def test_reading_an_account_is_a_get_that_returns_a_plain_dict() -> None:
    gw, http = gateway(
        {
            "id": HELPER,
            "object": "account",
            "requirements": {"currently_due": ["external_account"]},
        }
    )

    account = gw.retrieve_account(**READS["retrieve_account"])

    assert account["requirements"] == {"currently_due": ["external_account"]}
    assert isinstance(account["requirements"], dict)
    [request] = http.requests
    assert request.method == "get"
    assert request.url.endswith(f"/v1/accounts/{HELPER}")
    with pytest.raises(ValueError):
        gw.retrieve_account(connected_account="not-an-account")


def test_reading_a_payment_intent_is_a_get_with_its_client_secret() -> None:
    gw, http = gateway(
        {
            "id": "pi_3TestDestinationCharge",
            "object": "payment_intent",
            "status": "requires_payment_method",
            "client_secret": "pi_3TestDestinationCharge_secret_abc",
        }
    )

    intent = gw.retrieve_payment_intent(**READS["retrieve_payment_intent"])

    assert (intent.status, intent.client_secret) == (
        "requires_payment_method",
        "pi_3TestDestinationCharge_secret_abc",
    )
    [request] = http.requests
    assert request.method == "get"
    assert request.url.endswith("/v1/payment_intents/pi_3TestDestinationCharge")
    with pytest.raises(ValueError):
        gw.retrieve_payment_intent(booking_id=42, payment_intent="../accounts")


def test_finding_a_bookings_payment_intents_searches_its_metadata() -> None:
    gw, http = gateway(
        {
            "object": "search_result",
            "data": [
                {
                    "id": "pi_3TestOrphan",
                    "object": "payment_intent",
                    "status": "requires_payment_method",
                }
            ],
            "has_more": False,
            "url": "/v1/payment_intents/search",
        }
    )

    found = gw.find_booking_payment_intents(**READS["find_booking_payment_intents"])

    assert found == [{"id": "pi_3TestOrphan", "status": "requires_payment_method"}]
    [request] = http.requests
    assert request.method == "get"
    assert "/v1/payment_intents/search" in request.url
    assert "booking_id" in request.url
    with pytest.raises(ValueError):
        gw.find_booking_payment_intents(booking_id="1' OR metadata['x']:'y")


def test_the_settlement_steps_move_exactly_the_given_amounts() -> None:
    gw, http = gateway({"id": "obj_test_1", "object": "refund"})

    for method in ("refund_application_fee", "reverse_transfer", "refund_poster"):
        getattr(gw, method)(**CALLS[method])

    fee, reversal, refund = http.requests
    assert fee.url.endswith("/v1/application_fees/fee_1TestFee/refunds")
    assert fee.params == {
        "amount": ["270"],
        "metadata[booking_id]": ["42"],
        "metadata[action]": ["fee_refund"],
    }
    assert reversal.url.endswith("/v1/transfers/tr_1TestTransfer/reversals")
    assert reversal.params == {
        "amount": ["2520"],
        "metadata[booking_id]": ["42"],
        "metadata[action]": ["transfer_reversal"],
    }
    assert refund.url.endswith("/v1/refunds")
    assert refund.params == {
        "payment_intent": ["pi_3TestDestinationCharge"],
        "amount": ["2520"],
        "reverse_transfer": ["false"],
        "refund_application_fee": ["false"],
        "metadata[booking_id]": ["42"],
        "metadata[action]": ["partial_refund"],
    }


def test_a_full_refund_split_is_valid() -> None:
    SettlementSplit(
        charge_cents=3_360,
        application_fee_cents=360,
        helper_compensation_cents=0,
        fee_kept_cents=0,
        refund_cents=3_360,
        transfer_reversal_cents=3_360,
        fee_refund_cents=360,
    )


def test_a_split_without_a_fee_is_valid() -> None:
    """A booking whose fee a credit covered: no fee to refund or keep (BEAV-123)."""
    SettlementSplit(
        charge_cents=3_000,
        application_fee_cents=0,
        helper_compensation_cents=750,
        fee_kept_cents=0,
        refund_cents=2_250,
        transfer_reversal_cents=2_250,
        fee_refund_cents=0,
    )


@pytest.mark.parametrize(
    "change",
    [
        # TaskBeavo would fund part of the refund (hard rule b).
        {"transfer_reversal_cents": 2_000},
        # The helper's money would move to TaskBeavo.
        {"transfer_reversal_cents": 3_000},
        {"refund_cents": 2_521, "transfer_reversal_cents": 2_521},
        {"fee_refund_cents": 360},
        # C can't exceed the task price (round 3).
        {"helper_compensation_cents": 3_100, "fee_kept_cents": 372, "refund_cents": -112},
        {"application_fee_cents": 3_360},
        {"refund_cents": 0, "transfer_reversal_cents": 0},
        {"fee_refund_cents": 0},
    ],
)
def test_a_split_that_would_misroute_money_is_refused(change: dict[str, Any]) -> None:
    fields = {name: getattr(SPLIT_30, name) for name in SPLIT_30.__dataclass_fields__}
    with pytest.raises(ValueError):
        SettlementSplit(**{**fields, **change})


@pytest.mark.parametrize(
    ("method", "change"),
    [
        ("refund_application_fee", {"application_fee": "ch_1"}),
        ("reverse_transfer", {"transfer": HELPER}),
        ("reverse_transfer", {"fee_refund_id": "re_1TestRefund"}),
        ("refund_poster", {"transfer_reversal_id": "fr_1TestFeeRefund"}),
        ("refund_poster", {"payment_intent": "ch_1"}),
    ],
)
def test_a_step_refuses_wrong_ids_before_any_request(method: str, change: dict[str, Any]) -> None:
    gw, http = gateway()

    with pytest.raises(ValueError):
        getattr(gw, method)(**{**CALLS[method], **change})

    assert http.requests == []


def listing(*items: dict[str, Any]) -> dict[str, Any]:
    return {"object": "list", "data": list(items), "has_more": False, "url": "/v1/x"}


@pytest.mark.parametrize(
    ("method", "path", "action"),
    [
        ("find_fee_refund", "/v1/application_fees/fee_1TestFee/refunds", "fee_refund"),
        ("find_transfer_reversal", "/v1/transfers/tr_1TestTransfer/reversals", "transfer_reversal"),
        ("find_poster_refund", "/v1/refunds", "partial_refund"),
    ],
)
def test_a_settlement_step_is_found_by_booking_and_action(
    method: str, path: str, action: str
) -> None:
    gw, http = gateway(
        listing(
            {"id": "x_other_booking", "metadata": {"booking_id": "41", "action": action}},
            {"id": "x_other_action", "metadata": {"booking_id": "42", "action": "nope"}},
            {"id": "x_no_metadata", "metadata": {}},
            {"id": "x_found", "metadata": {"booking_id": "42", "action": action}},
        )
    )

    assert getattr(gw, method)(**READS[method]) == "x_found"
    assert [r.method for r in http.requests] == ["get"]
    assert path in http.requests[0].url


@pytest.mark.parametrize(
    "method", ["find_fee_refund", "find_transfer_reversal", "find_poster_refund"]
)
def test_a_missing_settlement_step_is_none(method: str) -> None:
    gw, _ = gateway(listing())

    assert getattr(gw, method)(**READS[method]) is None


def test_the_charge_parts_come_from_the_latest_charge() -> None:
    gw, http = gateway(
        {
            "id": "pi_3TestDestinationCharge",
            "object": "payment_intent",
            "latest_charge": {
                "id": "ch_1TestCharge",
                "object": "charge",
                "transfer": "tr_1TestTransfer",
                "application_fee": "fee_1TestFee",
            },
        }
    )

    parts = gw.retrieve_charge_parts(**READS["retrieve_charge_parts"])

    assert parts == stripe_client.ChargeParts(
        charge="ch_1TestCharge", transfer="tr_1TestTransfer", application_fee="fee_1TestFee"
    )
    [request] = http.requests
    assert request.method == "get"
    assert "expand" in request.url


def test_a_charge_without_an_application_fee_has_no_fee_id() -> None:
    gw, _ = gateway(
        {
            "id": "pi_3TestDestinationCharge",
            "object": "payment_intent",
            "latest_charge": {
                "id": "ch_1TestCharge",
                "object": "charge",
                "transfer": "tr_1TestTransfer",
                "application_fee": None,
            },
        }
    )

    parts = gw.retrieve_charge_parts(**READS["retrieve_charge_parts"], expect_application_fee=False)

    assert parts.application_fee == ""


def test_a_missing_application_fee_is_refused_when_a_fee_was_charged() -> None:
    # Stripe may fill the transfer before the fee: retried, never saved as "no fee".
    gw, _ = gateway(
        {
            "id": "pi_3TestDestinationCharge",
            "object": "payment_intent",
            "latest_charge": {
                "id": "ch_1TestCharge",
                "object": "charge",
                "transfer": "tr_1TestTransfer",
                "application_fee": None,
            },
        }
    )

    with pytest.raises(stripe_client.StripeRequestError) as exc:
        gw.retrieve_charge_parts(**READS["retrieve_charge_parts"])
    assert exc.value.code == "not_a_destination_charge"


def test_a_reversal_needs_no_fee_refund_when_there_was_no_fee() -> None:
    gw, http = gateway({"id": "trr_1TestReversal", "object": "transfer_reversal"})
    split = SettlementSplit(
        charge_cents=3_000,
        application_fee_cents=0,
        helper_compensation_cents=0,
        fee_kept_cents=0,
        refund_cents=3_000,
        transfer_reversal_cents=3_000,
        fee_refund_cents=0,
    )

    gw.reverse_transfer(**{**CALLS["reverse_transfer"], "split": split, "fee_refund_id": ""})

    assert http.requests[0].params["amount"] == ["3000"]


def test_a_charge_without_a_transfer_is_refused() -> None:
    gw, _ = gateway(
        {
            "id": "pi_3TestDestinationCharge",
            "object": "payment_intent",
            "latest_charge": {"id": "ch_1TestCharge", "object": "charge", "transfer": None},
        }
    )

    with pytest.raises(stripe_client.StripeRequestError) as exc:
        gw.retrieve_charge_parts(**READS["retrieve_charge_parts"])
    assert exc.value.code == "not_a_destination_charge"


def test_calls_are_traced_with_ids_only(spans: InMemorySpanExporter) -> None:
    gw, _ = gateway({"id": "po_test_1", "object": "payout"})

    gw.create_payout(**CALLS["create_payout"])

    span = next(s for s in spans.get_finished_spans() if s.name == "stripe.payouts.create")
    assert dict(span.attributes or {}) == {
        "booking.id": "42",
        "amount.cents": 3000,
        "currency": "eur",
        "stripe.object_id": "po_test_1",
    }


class TestGetGateway:
    def setup_method(self) -> None:
        stripe_client.get_gateway.cache_clear()

    def teardown_method(self) -> None:
        stripe_client.get_gateway.cache_clear()

    @override_settings(STRIPE_SECRET_KEY="")
    def test_needs_a_secret_key(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="STRIPE_SECRET_KEY"):
            stripe_client.get_gateway()

    @override_settings(STRIPE_SECRET_KEY="sk_test_not_a_real_key")
    def test_is_built_once(self) -> None:
        assert stripe_client.get_gateway() is stripe_client.get_gateway()


def test_only_the_wrapper_imports_the_stripe_sdk() -> None:
    """Every Stripe call goes through StripeGateway, so none can skip the key checks. The
    demo stand-in only answers the SDK's requests (its transport); it makes none."""
    allowed = {
        BACKEND_DIR / "payments" / "stripe_client.py",
        BACKEND_DIR / "payments" / "demo_stripe.py",
    }
    offenders = []
    for path in first_party_modules():
        if path in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name == "stripe" or name.startswith("stripe.") for name in names):
                offenders.append(str(path.relative_to(BACKEND_DIR)))
    assert offenders == []


def first_party_modules() -> list[Path]:
    """Backend source files, without descending into the virtualenv or test folders."""
    skipped = {".venv", "node_modules", "tests", "__pycache__"}
    modules = []
    for root, dirs, files in os.walk(BACKEND_DIR):
        dirs[:] = [d for d in dirs if d not in skipped and not d.startswith(".")]
        modules += [Path(root) / name for name in files if name.endswith(".py")]
    return modules
