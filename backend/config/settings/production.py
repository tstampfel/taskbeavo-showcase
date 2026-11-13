"""Production (and staging) on Render. Every secret must come from the environment."""

import os

os.environ.setdefault("APP_ENV", "production")

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, DATABASES, MIDDLEWARE

DEBUG = False

# Render sets this to the service's onrender.com host name.
if render_host := os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, render_host]

# Staging and production both sit behind Render's proxy, where REMOTE_ADDR is the proxy;
# trusting its X-Forwarded-For entry keeps anonymous clients in separate mutation
# rate-limit buckets (config/graphql_limits.py).
GRAPHQL_TRUSTED_PROXY_COUNT = int(os.environ.get("GRAPHQL_TRUSTED_PROXY_COUNT", "1"))

# Render's health check sends a Host header outside ALLOWED_HOSTS (core/middleware.py).
MIDDLEWARE = ["core.middleware.HealthCheckMiddleware", *MIDDLEWARE]

DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DB_CONN_MAX_AGE", "60"))
DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = "require"
# Neon's pooled connection is PgBouncer in transaction mode, which breaks server-side cursors.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# /healthz is probed over plain HTTP by Render's internal health check.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# HSTS preload is hard to undo once browsers ship it; opt in when the domains are final.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
