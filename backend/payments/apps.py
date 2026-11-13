from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class PaymentsConfig(AppConfig):
    name = "payments"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Each app registers its Stripe webhook handlers in stripe_handlers.py
        # (payments/webhooks.py).
        autodiscover_modules("stripe_handlers")
        from payments import outbox_handlers  # noqa: F401  (registers the outbox topics)
