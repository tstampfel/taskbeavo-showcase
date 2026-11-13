"""Demo payments: a stand-in for Stripe's API inside this process (BEAV-198).

THIS IS A DEMO STUB. It moves no money and talks to no one. It exists so a visitor of the
public showcase can run checkout, refunds and payouts without Stripe keys or a Connect
setup. It is on only with `DEMO_PAYMENTS=1`, which settings refuse in production and next
to a live key.

It replaces the SDK's HTTP transport, not the gateway: `get_gateway()` builds the usual
`StripeGateway` around a real `stripe.StripeClient` whose `http_client` is
`DemoHttpClient`. So every call still goes through `StripeGateway`'s checks and sends its
idempotency key, and the answers come back as the SDK's own objects. `DemoHttpClient`
answers the requests the gateway makes, keeps the objects in `demo_stripe_objects`, and
replays a request with a key it has seen (`demo_stripe_requests`), as Stripe does.

Like Stripe, it reports what happened through webhook events. Each one is stored with the
object (`object="event"`) and delivered after the request's transaction commits, by the
Celery task `payments.demo_stripe.deliver_events`, to `payments.webhooks.process_event`:
the same insert into `stripe_events` with ON CONFLICT DO NOTHING and the same handlers as
a signed event from Stripe (hard rule a). Nothing here changes a booking: a PaymentIntent
is paid only when the demo card form confirms it (`confirm_payment_intent`, called by
payments/demo_views.py), and the booking becomes FUNDS_HELD only when its
payment_intent.succeeded event goes through the webhook handler.

Event types sent: payment_intent.succeeded and payment_intent.payment_failed (the demo
card form), charge.refunded (a refund), transfer.reversed (a transfer reversal),
account.updated (a new account, enabled at once: there is no onboarding to do) and
payout.paid (a payout, stored until its handler exists, as Stripe's would be).
"""

import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from payments.models import DemoStripeObject, DemoStripeRequest

logger = logging.getLogger(__name__)

# Where the SDK thinks it sends requests (it shows in its logs): nowhere real.
DEMO_API_BASE = "https://demo-stripe.invalid"
# Not a key: the SDK wants one. Settings refuse DEMO_PAYMENTS next to a live key.
DEMO_API_KEY = "sk_test_demo_payments_no_real_key"
API_VERSION = "demo"
# The card numbers the demo card form offers, as on Stripe's test cards page.
DECLINED_CARD = "4000000000000002"

_PATH = re.compile(r"/v1/(?P<rest>[a-z_/]+?)(?:/(?P<id>[a-z]+_[A-Za-z0-9]+))?(?P<tail>/[a-z_]+)?")


def demo_id(prefix: str) -> str:
    """A fresh demo object ID, `pi_demo…`: never a real Stripe ID."""
    return f"{prefix}_demo{secrets.token_hex(8)}"


class DemoError(Exception):
    def __init__(
        self, status: int, code: str, *, param: str = "", extra: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status = status
        self.body = {
            "error": {
                "type": "invalid_request_error",
                "code": code,
                "param": param,
                "message": f"Demo Stripe: {code}",
                **(extra or {}),
            }
        }


class DemoHttpClient(stripe.HTTPClient):
    """The SDK's transport, answered here instead of by api.stripe.com."""

    name = "taskbeavo-demo"

    def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        post_data: Any = None,
        *,
        _usage: Any = None,
    ) -> tuple[str, int, Mapping[str, str]]:
        headers = dict(headers or {})
        parts = urlsplit(url)
        body = post_data.decode() if isinstance(post_data, bytes) else post_data or ""
        params = decode_params(body if method.lower() == "post" else parts.query)
        key = headers.get("Idempotency-Key", "")
        status, response = handle(
            method.upper(),
            parts.path,
            params,
            idempotency_key=key,
            stripe_account=headers.get("Stripe-Account", ""),
        )
        return json.dumps(response), status, {"request-id": demo_id("req")}

    def close(self) -> None:
        pass


def demo_client() -> stripe.StripeClient:
    """The SDK's client, wired to the demo transport."""
    return stripe.StripeClient(
        DEMO_API_KEY,
        http_client=DemoHttpClient(),
        base_addresses={"api": DEMO_API_BASE},
        max_network_retries=0,
    )


def handle(
    method: str,
    path: str,
    params: dict[str, Any],
    *,
    idempotency_key: str = "",
    stripe_account: str = "",
) -> tuple[int, dict[str, Any]]:
    """Answer one API request: (HTTP status, JSON body).

    Known gap, demo only: two concurrent requests with the same idempotency key can both
    miss the replay lookup. The second then fails on DemoStripeRequest's primary key and
    the SDK sees an error, where Stripe would answer 409 idempotency_key_in_use. Its
    transaction rolls back, so nothing is charged, refunded or paid out twice, and the
    callers already serialise on the booking's row lock.
    """
    if method == "POST" and idempotency_key:
        seen = DemoStripeRequest.objects.filter(pk=idempotency_key).first()
        if seen is not None:
            if (seen.method, seen.path) != (method, path):
                return 400, DemoError(400, "idempotency_key_in_use").body
            return seen.status, seen.response
    try:
        with transaction.atomic():
            response = _route(method, path, params, idempotency_key, stripe_account)
            status = 200
            if method == "POST" and idempotency_key:
                DemoStripeRequest.objects.create(
                    idempotency_key=idempotency_key,
                    method=method,
                    path=path,
                    status=status,
                    response=response,
                )
    except DemoError as exc:
        return exc.status, exc.body
    return status, response


def _route(
    method: str, path: str, params: dict[str, Any], key: str, account: str
) -> dict[str, Any]:
    match = _PATH.fullmatch(path)
    if match is None:
        raise DemoError(404, "resource_missing")
    rest, object_id, tail = match["rest"], match["id"], match["tail"] or ""
    route = (method, rest, bool(object_id), tail)
    if route == ("POST", "payment_intents", False, ""):
        return _create_payment_intent(params)
    if route == ("GET", "payment_intents", False, "/search"):
        return _search_payment_intents(params)
    if route == ("GET", "payment_intents", True, ""):
        return _expanded(_get(object_id, "payment_intent"), params)
    if route == ("POST", "payment_intents", True, "/cancel"):
        return _cancel_payment_intent(object_id)
    if route == ("POST", "refunds", False, ""):
        return _create_refund(params, key)
    if route == ("GET", "refunds", False, ""):
        return _list("refund", lambda data: data["payment_intent"] == params.get("payment_intent"))
    if route == ("POST", "application_fees", True, "/refunds"):
        return _refund_application_fee(object_id, params)
    if route == ("GET", "application_fees", True, "/refunds"):
        return _list("fee_refund", lambda data: data["fee"] == object_id)
    if route == ("POST", "transfers", True, "/reversals"):
        return _reverse_transfer(object_id, params, key)
    if route == ("GET", "transfers", True, "/reversals"):
        return _list("transfer_reversal", lambda data: data["transfer"] == object_id)
    if route == ("POST", "payouts", False, ""):
        return _create_payout(params, account, key)
    if route == ("POST", "accounts", False, ""):
        return _create_account(params)
    if route == ("GET", "accounts", True, ""):
        return _get(object_id, "account")
    if route == ("POST", "account_links", False, ""):
        return _create_account_link(params)
    raise DemoError(404, "resource_missing")


# --- PaymentIntents and the demo card form. ---


def _create_payment_intent(params: dict[str, Any]) -> dict[str, Any]:
    amount = _cents(params, "amount")
    fee = _cents(params, "application_fee_amount", required=False)
    destination = (params.get("transfer_data") or {}).get("destination", "")
    _account(destination, "transfer_data[destination]")
    intent_id = demo_id("pi")
    data = {
        "id": intent_id,
        "object": "payment_intent",
        "amount": amount,
        "amount_received": 0,
        "currency": params.get("currency", "eur"),
        "status": "requires_payment_method",
        "client_secret": f"{intent_id}_secret_{secrets.token_urlsafe(18)}",
        "application_fee_amount": fee,
        "on_behalf_of": params.get("on_behalf_of"),
        "transfer_data": {"destination": destination},
        "automatic_payment_methods": {"enabled": True},
        "payment_method_types": ["card"],
        "metadata": params.get("metadata") or {},
        "latest_charge": None,
        "last_payment_error": None,
        "livemode": False,
        "created": int(time.time()),
    }
    return _save(data)


def confirm_payment_intent(intent_id: str, client_secret: str, card_number: str) -> dict[str, Any]:
    """The demo card form's "Pay", standing in for Stripe.js confirming the PaymentIntent
    with its client secret. Marks the PaymentIntent paid (or declined) and queues the event;
    it never touches a booking. A second confirm of a paid PaymentIntent changes nothing.

    Raises DemoError 404 for an unknown PaymentIntent or a wrong client secret."""
    with transaction.atomic():
        row = (
            DemoStripeObject.objects.select_for_update()
            .filter(pk=intent_id, object="payment_intent")
            .first()
        )
        if row is None or not hmac.compare_digest(
            str(row.data.get("client_secret", "")), client_secret
        ):
            raise DemoError(404, "resource_missing", param="intent")
        intent = row.data
        if intent["status"] in ("succeeded", "canceled"):
            return _public_intent(intent)
        if card_number == DECLINED_CARD:
            intent["status"] = "requires_payment_method"
            intent["last_payment_error"] = {
                "type": "card_error",
                "code": "card_declined",
                "decline_code": "generic_decline",
            }
            _store(row, intent)
            _queue_event("payment_intent.payment_failed", intent)
            return _public_intent(intent)
        charge = _charge_for(intent)
        intent.update(
            status="succeeded",
            amount_received=intent["amount"],
            latest_charge=charge["id"],
            last_payment_error=None,
        )
        _store(row, intent)
        _queue_event("payment_intent.succeeded", intent)
    return _public_intent(intent)


def _public_intent(intent: dict[str, Any]) -> dict[str, Any]:
    """What a browser holding the client secret sees, as from Stripe.js."""
    return {key: intent[key] for key in ("id", "object", "status", "amount", "currency")} | {
        "last_payment_error": intent.get("last_payment_error"),
    }


def _charge_for(intent: dict[str, Any]) -> dict[str, Any]:
    """The charge of a paid destination charge, with its transfer and application fee. As
    at Stripe, the whole amount is transferred to the helper's account and the fee is then
    collected from it, so the fee refund and the reversal of the settlement both fit."""
    fee = intent.get("application_fee_amount") or 0
    destination = intent["transfer_data"]["destination"]
    charge_id = demo_id("ch")
    transfer = _save(
        {
            "id": demo_id("tr"),
            "object": "transfer",
            "amount": intent["amount"],
            "amount_reversed": 0,
            "currency": intent["currency"],
            "destination": destination,
            "source_transaction": charge_id,
            "reversals": {"object": "list", "data": [], "has_more": False},
            "metadata": {},
        }
    )
    application_fee = None
    if fee:
        application_fee = _save(
            {
                "id": demo_id("fee"),
                "object": "application_fee",
                "amount": fee,
                "amount_refunded": 0,
                "currency": intent["currency"],
                "account": destination,
                "charge": charge_id,
            }
        )
    return _save(
        {
            "id": charge_id,
            "object": "charge",
            "amount": intent["amount"],
            "amount_refunded": 0,
            "refunded": False,
            "currency": intent["currency"],
            "payment_intent": intent["id"],
            "transfer": transfer["id"],
            "application_fee": application_fee["id"] if application_fee else None,
            "metadata": intent.get("metadata") or {},
        }
    )


def _cancel_payment_intent(intent_id: str) -> dict[str, Any]:
    row = _lock(intent_id, "payment_intent")
    intent = row.data
    if intent["status"] in ("succeeded", "processing"):
        raise DemoError(
            400,
            "payment_intent_unexpected_state",
            extra={"payment_intent": {"id": intent_id, "status": intent["status"]}},
        )
    intent["status"] = "canceled"
    _store(row, intent)
    return intent


def _search_payment_intents(params: dict[str, Any]) -> dict[str, Any]:
    match = re.fullmatch(r"metadata\['booking_id'\]:'([0-9a-f-]+)'", params.get("query", ""))
    booking_id = match.group(1) if match else None
    found = [
        row.data
        for row in DemoStripeObject.objects.filter(object="payment_intent").order_by("created_at")
        if booking_id and (row.data.get("metadata") or {}).get("booking_id") == booking_id
    ]
    return {"object": "search_result", "data": found, "has_more": False, "url": "/v1/search"}


def _expanded(intent: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    expand = params.get("expand") or []
    if "latest_charge" in expand and intent.get("latest_charge"):
        return {**intent, "latest_charge": _get(intent["latest_charge"], "charge")}
    return intent


# --- Refunds, fee refunds and transfer reversals. ---


def _create_refund(params: dict[str, Any], key: str) -> dict[str, Any]:
    intent = _get(params.get("payment_intent", ""), "payment_intent")
    if intent["status"] != "succeeded" or not intent.get("latest_charge"):
        raise DemoError(400, "charge_not_refundable", param="payment_intent")
    charge_row = _lock(intent["latest_charge"], "charge")
    charge = charge_row.data
    left = charge["amount"] - charge["amount_refunded"]
    amount = _cents(params, "amount", required=False) or left
    if amount > left:
        raise DemoError(400, "amount_too_large", param="amount")
    if _flag(params, "reverse_transfer"):
        # Stripe's proportional reversal: the share of the transfer this refund is of the
        # charge. Only full refunds use it (StripeGateway.create_full_refund).
        transfer_row = _lock(charge["transfer"], "transfer")
        transfer = transfer_row.data
        share = transfer["amount"] * amount // charge["amount"]
        _add_reversal(transfer_row, transfer, min(share, _reversible(transfer)), {}, key)
    if _flag(params, "refund_application_fee") and charge.get("application_fee"):
        fee_row = _lock(charge["application_fee"], "application_fee")
        fee = fee_row.data
        share = fee["amount"] * amount // charge["amount"]
        fee["amount_refunded"] = min(fee["amount"], fee["amount_refunded"] + share)
        _store(fee_row, fee)
    refund = _save(
        {
            "id": demo_id("re"),
            "object": "refund",
            "amount": amount,
            "currency": charge["currency"],
            "charge": charge["id"],
            "payment_intent": intent["id"],
            "status": "succeeded",
            "metadata": params.get("metadata") or {},
        }
    )
    charge["amount_refunded"] += amount
    charge["refunded"] = charge["amount_refunded"] == charge["amount"]
    _store(charge_row, charge)
    _queue_event("charge.refunded", charge)
    return refund


def _refund_application_fee(fee_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = _lock(fee_id, "application_fee")
    fee = row.data
    amount = _cents(params, "amount", required=False) or fee["amount"] - fee["amount_refunded"]
    if amount > fee["amount"] - fee["amount_refunded"]:
        raise DemoError(400, "amount_too_large", param="amount")
    fee["amount_refunded"] += amount
    _store(row, fee)
    return _save(
        {
            "id": demo_id("fr"),
            "object": "fee_refund",
            "amount": amount,
            "currency": fee["currency"],
            "fee": fee_id,
            "metadata": params.get("metadata") or {},
        }
    )


def _reverse_transfer(transfer_id: str, params: dict[str, Any], key: str) -> dict[str, Any]:
    row = _lock(transfer_id, "transfer")
    transfer = row.data
    amount = _cents(params, "amount", required=False) or _reversible(transfer)
    if amount > _reversible(transfer):
        raise DemoError(400, "insufficient_funds", param="amount")
    return _add_reversal(row, transfer, amount, params.get("metadata") or {}, key)


def _reversible(transfer: dict[str, Any]) -> int:
    return int(transfer["amount"] - transfer["amount_reversed"])


def _add_reversal(
    row: DemoStripeObject,
    transfer: dict[str, Any],
    amount: int,
    metadata: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    reversal = _save(
        {
            "id": demo_id("trr"),
            "object": "transfer_reversal",
            "amount": amount,
            "currency": transfer["currency"],
            "transfer": transfer["id"],
            "metadata": metadata,
        }
    )
    transfer["amount_reversed"] += amount
    transfer["reversals"]["data"].append(reversal)
    _store(row, transfer)
    _queue_event("transfer.reversed", transfer, idempotency_key=key)
    return reversal


# --- Payouts and connected accounts. ---


def _create_payout(params: dict[str, Any], account: str, key: str) -> dict[str, Any]:
    _account(account, "Stripe-Account")
    payout = _save(
        {
            "id": demo_id("po"),
            "object": "payout",
            "amount": _cents(params, "amount"),
            "currency": params.get("currency", "eur"),
            "status": "paid",
            "method": "standard",
            "metadata": params.get("metadata") or {},
        }
    )
    _queue_event("payout.paid", payout, account=account, idempotency_key=key)
    return payout


def _create_account(params: dict[str, Any]) -> dict[str, Any]:
    """An Express account that is enabled at once: the demo has no onboarding."""
    account = _save(
        {
            "id": demo_id("acct"),
            "object": "account",
            "type": "express",
            "country": params.get("country", "HR"),
            "details_submitted": True,
            "charges_enabled": True,
            "payouts_enabled": True,
            "capabilities": {"card_payments": "active", "transfers": "active"},
            "requirements": {
                "currently_due": [],
                "eventually_due": [],
                "past_due": [],
                "current_deadline": None,
                "disabled_reason": None,
            },
            "settings": {"payouts": {"schedule": {"interval": "manual"}}},
            "metadata": params.get("metadata") or {},
            "created": int(time.time()),
        }
    )
    _queue_event("account.updated", account, account=account["id"])
    return account


def _create_account_link(params: dict[str, Any]) -> dict[str, Any]:
    """Onboarding that is already done: the link goes straight back to the app."""
    _get(params.get("account", ""), "account")
    return {
        "object": "account_link",
        "url": params.get("return_url", ""),
        "created": int(time.time()),
        "expires_at": int(time.time()) + 300,
    }


# --- Events. ---


def _queue_event(
    event_type: str,
    obj: dict[str, Any],
    *,
    account: str = "",
    idempotency_key: str = "",
) -> None:
    event: dict[str, Any] = {
        "id": demo_id("evt"),
        "object": "event",
        "type": event_type,
        "api_version": API_VERSION,
        "created": int(time.time()),
        "livemode": False,
        "data": {"object": json.loads(json.dumps(obj))},
        "request": {"id": None, "idempotency_key": idempotency_key or None},
    }
    if account:
        event["account"] = account
    DemoStripeObject.objects.create(id=event["id"], object="event", data=event)
    transaction.on_commit(schedule_delivery)


def schedule_delivery() -> None:
    """Deliver the queued events in a moment, as Stripe sends them after its answer."""
    from payments.tasks import deliver_demo_stripe_events

    deliver_demo_stripe_events.apply_async(countdown=1)


def deliver_events(limit: int = 100) -> int:
    """Hand each undelivered demo event to the webhook handler, oldest first, each in its
    own transaction under a row lock (a concurrent delivery skips it). A handler that fails
    leaves its event for the next run. Returns how many were delivered.

    Known gap, demo only: there is no Beat schedule, so a failed event waits until the
    next stand-in call queues another delivery (or the next `seed_demo`), and a checkout
    can sit on "Confirming payment…" until then. Retrying is safe: the stripe_events
    insert rolls back with the failed handler.
    """
    from payments.webhooks import process_event

    if not settings.DEMO_PAYMENTS or settings.STRIPE_LIVEMODE:
        return 0
    pending = list(
        DemoStripeObject.objects.filter(object="event", delivered_at__isnull=True)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    delivered = 0
    for event_id in pending:
        try:
            with transaction.atomic():
                row = (
                    DemoStripeObject.objects.select_for_update(skip_locked=True)
                    .filter(pk=event_id, delivered_at__isnull=True)
                    .first()
                )
                if row is None:
                    continue
                process_event(row.data)
                row.delivered_at = timezone.now()
                row.save(update_fields=["delivered_at"])
        except Exception:
            logger.exception(
                "demo stripe: event delivery failed", extra={"stripe.event_id": event_id}
            )
            continue
        delivered += 1
    return delivered


# --- Storage and parameters. ---


def _save(data: dict[str, Any]) -> dict[str, Any]:
    DemoStripeObject.objects.create(id=data["id"], object=data["object"], data=data)
    return data


def _store(row: DemoStripeObject, data: dict[str, Any]) -> None:
    row.data = data
    row.save(update_fields=["data"])


def _get(object_id: str, kind: str) -> dict[str, Any]:
    row = DemoStripeObject.objects.filter(pk=object_id, object=kind).first()
    if row is None:
        raise DemoError(404, "resource_missing", param="id")
    return row.data


def _lock(object_id: str, kind: str) -> DemoStripeObject:
    row = DemoStripeObject.objects.select_for_update().filter(pk=object_id, object=kind).first()
    if row is None:
        raise DemoError(404, "resource_missing", param="id")
    return row


def _list(kind: str, keep: Any) -> dict[str, Any]:
    rows = DemoStripeObject.objects.filter(object=kind).order_by("-created_at")
    return {
        "object": "list",
        "data": [row.data for row in rows if keep(row.data)],
        "has_more": False,
        "url": "/v1/list",
    }


def _cents(params: dict[str, Any], name: str, *, required: bool = True) -> int:
    value = params.get(name)
    if value is None and not required:
        return 0
    try:
        cents = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise DemoError(400, "parameter_invalid_integer", param=name) from None
    if cents <= 0:
        raise DemoError(400, "parameter_invalid_integer", param=name)
    return cents


def _flag(params: dict[str, Any], name: str) -> bool:
    return str(params.get(name, "")).lower() == "true"


def _account(account: str, param: str) -> None:
    if not DemoStripeObject.objects.filter(pk=account, object="account").exists():
        raise DemoError(400, "account_invalid", param=param)


def decode_params(encoded: str) -> dict[str, Any]:
    """Stripe's form encoding back into nested values: `metadata[booking_id]=1` and
    `expand[0]=latest_charge` become `{"metadata": {"booking_id": "1"}, "expand": [...]}`."""
    result: dict[str, Any] = {}
    for key, value in parse_qsl(encoded, keep_blank_values=True):
        names = [key.split("[", 1)[0], *re.findall(r"\[([^\]]*)\]", key)]
        node: Any = result
        for index, name in enumerate(names):
            last = index == len(names) - 1
            following = names[index + 1] if not last else ""
            child: Any = value if last else ([] if following.isdigit() or following == "" else {})
            if isinstance(node, list):
                if last:
                    node.append(value)
                    break
                if name.isdigit() and int(name) < len(node):
                    node = node[int(name)]
                    continue
                node.append(child)
                node = node[-1]
                continue
            if last:
                node[name] = value
            else:
                node = node.setdefault(name, child)
    return result
