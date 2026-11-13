from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "notifications"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from accounts.sessions import session_revoked
        from notifications.devices import forget_revoked_sessions

        session_revoked.connect(forget_revoked_sessions, dispatch_uid="notifications-forget")
