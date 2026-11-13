"""ASGI entry point: HTTP goes to Django, WebSockets at /graphql to Strawberry over Channels.

Run in production with gunicorn and uvicorn workers (see the backend-deploy skill).
"""

import os

from django.core.asgi import get_asgi_application

from config import telemetry

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

# Sentry and OpenTelemetry (no-ops without their keys). The Django instrumentation adds
# its middleware, so this runs before Django loads it.
telemetry.setup("api")

# Load Django before importing anything that touches models or settings.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import re_path  # noqa: E402

from accounts import geolite  # noqa: E402
from config.schema import schema  # noqa: E402
from config.ws import SubscriptionConsumer  # noqa: E402

# The web service keeps its own GeoLite2 copy for sign-in locations (accounts/geolite.py): one
# background thread downloads it now when missing or due, then checks hourly, deleting a copy
# over 30 days old, with or without sign-ins.
geolite.start_background_updates()

# Subscriptions authenticate with the token in the connection_init payload, not
# cookies (config/ws.py), so there is no ambient credential for an origin check to
# protect, and native apps send no Origin header.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(
            [
                # django-types only knows view functions, not Channels consumers.
                re_path(r"^graphql/?$", SubscriptionConsumer.as_asgi(schema=schema)),  # pyright: ignore[reportCallIssue, reportArgumentType]
            ],
        ),
    }
)
