"""`cp backend/.env.example backend/.env` must give a working local setup (BEAV-217).

docker compose reads backend/.env with `env_file`, so every `KEY=` line reaches Django as an
empty variable. These tests load the example file the same way and run Django on it.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.env import env, setdefault_env

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = BACKEND_DIR / ".env.example"


def parse_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines the way docker compose's env_file does; # lines are comments."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def env_from_example(example: dict[str, str]) -> dict[str, str]:
    """The process environment with only the example file's variables set, as in compose."""
    base = {
        key: value
        for key, value in os.environ.items()
        if key not in example and key != "DJANGO_SETTINGS_MODULE"
    }
    return {**base, **example}


def run_manage_check(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=BACKEND_DIR,
        env=env_from_example(extra_env),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_manage_check_passes_with_env_example_copied_as_is() -> None:
    result = run_manage_check(parse_env_file(ENV_EXAMPLE))
    assert result.returncode == 0, result.stdout + result.stderr


def test_manage_check_passes_with_env_example_and_compose_overrides() -> None:
    # docker-compose.yml's `environment:` block wins over env_file.
    example = parse_env_file(ENV_EXAMPLE) | {
        "DATABASE_URL": "postgis://taskbeavo:taskbeavo@postgis:5432/taskbeavo",
        "REDIS_URL": "redis://redis:6379/0",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,django,10.0.2.2",
    }
    result = run_manage_check(example)
    assert result.returncode == 0, result.stdout + result.stderr


def test_env_example_does_not_blank_the_settings_module() -> None:
    # An empty DJANGO_SETTINGS_MODULE stops manage.py, asgi.py and celery.py from picking
    # config.settings.local, and pytest from picking config.settings.test.
    assert "DJANGO_SETTINGS_MODULE" not in parse_env_file(ENV_EXAMPLE)


def test_setdefault_env_replaces_an_empty_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEAV217_EMPTY", "")
    setdefault_env("BEAV217_EMPTY", "fallback")
    assert env("BEAV217_EMPTY") == "fallback"


def test_setdefault_env_keeps_a_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEAV217_SET", "mine")
    setdefault_env("BEAV217_SET", "fallback")
    assert env("BEAV217_SET") == "mine"


def test_setdefault_env_sets_a_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEAV217_MISSING", raising=False)
    setdefault_env("BEAV217_MISSING", "fallback")
    assert env("BEAV217_MISSING") == "fallback"
