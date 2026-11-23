from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = "chat"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from chat import outbox_handlers  # noqa: F401  (registers the outbox topics)
