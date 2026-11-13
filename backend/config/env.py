"""Reading settings from environment variables, failing fast when a required one is missing."""

import os

from django.core.exceptions import ImproperlyConfigured

_UNSET = object()


def env(name: str, default: str | object = _UNSET) -> str:
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    if default is _UNSET:
        raise ImproperlyConfigured(f"Set the {name} environment variable.")
    return str(default)


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str | object = _UNSET) -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def setdefault_env(name: str, default: str) -> None:
    """Like os.environ.setdefault, but an empty value counts as unset.

    docker compose passes every `KEY=` line of backend/.env as an empty variable, which
    os.environ.setdefault would keep, hiding the settings module's default.
    """
    if not os.environ.get(name):
        os.environ[name] = default
