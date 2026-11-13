"""pytest. Needs PostGIS and Redis reachable at DATABASE_URL and REDIS_URL."""

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-only-not-secret")
os.environ.setdefault("JWT_SIGNING_KEY", "test-only-jwt-key-not-secret-0123456789")
os.environ.setdefault("DATABASE_URL", "postgis://taskbeavo:taskbeavo@localhost:55432/taskbeavo")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
# Tests swap in a fake R2 client (uploads/tests/fakes.py); these only name the buckets.
os.environ.setdefault("R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-not-secret")
os.environ.setdefault("R2_PRIVATE_BUCKET", "taskbeavo-private-test")
os.environ.setdefault("R2_PUBLIC_BUCKET", "taskbeavo-public-test")
os.environ.setdefault("R2_PUBLIC_BASE_URL", "https://photos.test.taskbeavo.invalid")

from config.settings.base import *  # noqa: F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "graphql_rate_limit": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}
SMS_BACKEND = "core.sms.LocmemSmsBackend"
