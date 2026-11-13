"""pytest. Needs PostGIS and Redis reachable at DATABASE_URL and REDIS_URL."""

import os

from config.env import setdefault_env

setdefault_env("APP_ENV", "test")
setdefault_env("SECRET_KEY", "test-only-not-secret")
setdefault_env("JWT_SIGNING_KEY", "test-only-jwt-key-not-secret-0123456789")
setdefault_env("DATABASE_URL", "postgis://taskbeavo:taskbeavo@localhost:55432/taskbeavo")
setdefault_env("REDIS_URL", "redis://localhost:6379/15")
# Tests assert the local default; FEE_VAT=NOT_CHARGED from a copied backend/.env (compose
# env_file) must not change it. Tests that need a variant override the setting.
os.environ["FEE_VAT"] = "UNDECIDED"
# Tests swap in a fake R2 client (uploads/tests/fakes.py); these only name the buckets.
setdefault_env("R2_ACCOUNT_ID", "test-account")
setdefault_env("R2_ACCESS_KEY_ID", "test-key-id")
setdefault_env("R2_SECRET_ACCESS_KEY", "test-secret-not-secret")
setdefault_env("R2_PRIVATE_BUCKET", "taskbeavo-private-test")
setdefault_env("R2_PUBLIC_BUCKET", "taskbeavo-public-test")
setdefault_env("R2_PUBLIC_BASE_URL", "https://photos.test.taskbeavo.invalid")

from config.settings.base import *  # noqa: E402, F403 (after the env defaults)

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
