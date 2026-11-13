"""The demo card form's endpoint (BEAV-198): a stand-in for Stripe's own
`POST /v1/payment_intents/{id}/confirm`, which Stripe.js and the payment sheet call.

THIS IS A DEMO STUB, mounted only when DEMO_PAYMENTS is on (config/urls.py), which
settings refuse in production and next to a live key. Like Stripe's, it is authorised by
the PaymentIntent's client secret, which only the poster's checkout received. It marks the
demo PaymentIntent paid or declined and queues the webhook event
(`demo_stripe.confirm_payment_intent`); the booking changes only when that event goes
through the webhook handler (hard rule a). Browsers may call it from the dev web origins
in DEMO_PAYMENTS_ALLOWED_ORIGINS.
"""

import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from payments import demo_stripe


@csrf_exempt
def confirm_payment_intent(request: HttpRequest, intent_id: str) -> HttpResponse:
    if request.method == "OPTIONS":
        return _cors(request, HttpResponse(status=204))
    if request.method != "POST":
        return _cors(request, HttpResponse(status=405))
    try:
        body = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("client_secret"), str):
        return _cors(request, JsonResponse({"error": {"code": "parameter_missing"}}, status=400))
    card_number = body.get("card_number")
    try:
        intent = demo_stripe.confirm_payment_intent(
            intent_id,
            body["client_secret"],
            card_number if isinstance(card_number, str) else "",
        )
    except demo_stripe.DemoError as exc:
        return _cors(request, JsonResponse(exc.body, status=exc.status))
    return _cors(request, JsonResponse(intent))


def _cors(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    origin = request.headers.get("Origin", "")
    if origin and origin in settings.DEMO_PAYMENTS_ALLOWED_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Vary"] = "Origin"
    return response
