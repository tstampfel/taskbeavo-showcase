from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from accounts import outbox_handlers  # noqa: F401  (registers the outbox topics)
