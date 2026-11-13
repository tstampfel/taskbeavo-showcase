"""Local development. Defaults match the postgis and redis services in docker compose."""

from config.env import setdefault_env

setdefault_env("APP_ENV", "local")
setdefault_env("DEBUG", "true")
setdefault_env("SECRET_KEY", "local-dev-only-not-secret")
setdefault_env("JWT_SIGNING_KEY", "local-dev-only-jwt-key-not-secret-0123456789")
setdefault_env("DATABASE_URL", "postgis://taskbeavo:taskbeavo@localhost:55432/taskbeavo")
setdefault_env("REDIS_URL", "redis://localhost:6379/0")
# 10.0.2.2 is the host machine as the Android emulator sees it.
setdefault_env("ALLOWED_HOSTS", "localhost,127.0.0.1,10.0.2.2")

from config.settings.base import *  # noqa: E402, F403 (after the env defaults)
