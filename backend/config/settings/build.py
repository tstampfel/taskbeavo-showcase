"""Docker build step (collectstatic). Needs no secrets and never connects to anything."""

from config.env import setdefault_env

setdefault_env("APP_ENV", "build")
setdefault_env("SECRET_KEY", "build-only-not-secret")
setdefault_env("DATABASE_URL", "postgis://build:build@localhost:5432/build")
setdefault_env("REDIS_URL", "redis://localhost:6379/0")

from config.settings.base import *  # noqa: E402, F403 (after the env defaults)
