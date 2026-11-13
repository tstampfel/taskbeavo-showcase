from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from config.schema import schema
from config.views import GraphQLView
from core.views import healthz
from payments.webhooks import stripe_webhook

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("webhooks/stripe", stripe_webhook, name="stripe-webhook"),
    # Clients authenticate with a bearer token in the Authorization header, not
    # cookies, so the CSRF check does not protect anything here.
    path(
        "graphql",
        csrf_exempt(
            GraphQLView.as_view(
                schema=schema,
                graphql_ide="graphiql" if settings.APP_ENV != "production" else None,
            )
        ),
        name="graphql",
    ),
    path("admin/", admin.site.urls),
]

if settings.DEMO_PAYMENTS:
    # The demo Stripe stand-in's confirm endpoint (payments/demo_views.py). Never mounted in
    # production: settings refuse DEMO_PAYMENTS there.
    from payments.demo_views import confirm_payment_intent

    urlpatterns.append(
        path(
            "demo-stripe/v1/payment_intents/<str:intent_id>/confirm",
            confirm_payment_intent,
            name="demo-stripe-confirm",
        )
    )
