"""Docker build step (collectstatic). Needs no secrets and never connects to anything."""

import os

os.environ.setdefault("APP_ENV", "build")
os.environ.setdefault("SECRET_KEY", "build-only-not-secret")
os.environ.setdefault("DATABASE_URL", "postgis://build:build@localhost:5432/build")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from config.settings.base import *  # noqa: F403
