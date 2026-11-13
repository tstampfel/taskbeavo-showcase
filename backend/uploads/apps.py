from django.apps import AppConfig


class UploadsConfig(AppConfig):
    name = "uploads"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from uploads import signals  # noqa: F401  (connects the R2 clean-up on delete)
