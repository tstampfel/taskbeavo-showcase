from django.apps import AppConfig


class OffersConfig(AppConfig):
    name = "offers"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from offers import outbox_handlers  # noqa: F401  (registers the outbox topics)
