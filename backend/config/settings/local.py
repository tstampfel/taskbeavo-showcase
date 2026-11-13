"""Local development. Defaults match the postgis and redis services in docker compose."""

import os

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "local-dev-only-not-secret")
os.environ.setdefault("JWT_SIGNING_KEY", "local-dev-only-jwt-key-not-secret-0123456789")
os.environ.setdefault("DATABASE_URL", "postgis://taskbeavo:taskbeavo@localhost:55432/taskbeavo")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# 10.0.2.2 is the host machine as the Android emulator sees it.
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,10.0.2.2")

from config.settings.base import *  # noqa: F403
