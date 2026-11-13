from django.apps import AppConfig


class BookingsConfig(AppConfig):
    name = "bookings"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from bookings import outbox_handlers  # noqa: F401  (registers the outbox topics)
