import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import Client
from graphql import build_schema as build_sdl_schema
from graphql import parse, validate
from strawberry.extensions import QueryDepthLimiter

from config.schema import API_VERSION, build_schema

BACKEND_DIR = Path(__file__).resolve().parents[2]
INTROSPECTION = "{ __schema { queryType { name } } }"


def post_graphql(client: Client, query: str) -> dict:
    response = client.post("/graphql", {"query": query}, content_type="application/json")
    assert response.status_code == 200
    return response.json()


def test_graphql_endpoint_answers_queries(client: Client) -> None:
    assert post_graphql(client, "{ apiVersion }") == {"data": {"apiVersion": API_VERSION}}


def test_introspection_allowed_outside_production(client: Client) -> None:
    body = post_graphql(client, INTROSPECTION)

    assert body["data"]["__schema"]["queryType"]["name"] == "Query"


def test_introspection_disabled_in_production_schema() -> None:
    result = build_schema(app_env="production").execute_sync(INTROSPECTION)

    assert result.errors
    assert result.data is None


def test_production_settings_disable_introspection_end_to_end() -> None:
    """APP_ENV=production with the production settings module: the served schema refuses it."""
    script = (
        "import django, json; django.setup();"
        "from config.schema import schema;"
        f"r = schema.execute_sync({INTROSPECTION!r});"
        "print(json.dumps({'errors': bool(r.errors), 'data': r.data}))"
    )
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "APP_ENV": "production",
        "SECRET_KEY": "x" * 50,
        "JWT_SIGNING_KEY": "j" * 50,
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "WEB_PROXY_SECRET": "p" * 32,
        "WEB_APP_URL": "https://taskbeavo.com",
        "FEE_LAUNCH_DATE": "2026-12-01",
        "FEE_VAT": "NOT_CHARGED",
        "TASKBEAVO_LEGAL_NAME": "TaskBeavo test d.o.o.",
        "TASKBEAVO_OIB": "00000000124",
        "TASKBEAVO_ADDRESS": "Test ulica 1, 20000 Dubrovnik, HR",
        "TASKBEAVO_CONTACT_EMAIL": "support@example.com",
        "FIELD_ENCRYPTION_KEY": "c3RhZ2luZy1vbmx5LWZpZWxkLWtleS0wMTIzNDU2Nzg=",
        "PII_DIGEST_KEY": "pii-digest-0123456789-not-secret",
        "SMS_BACKEND": "core.infobip.InfobipSmsBackend",
        "INFOBIP_BASE_URL": "https://example.api.infobip.com",
        "INFOBIP_API_KEY": "infobip-key-not-secret",
        "STRIPE_SECRET_KEY": "sk_live_not_a_real_key",
        "STRIPE_WEBHOOK_SECRET": "whsec_not_secret",
        "DATABASE_URL": "postgis://u:p@db.invalid:5432/db",
        "REDIS_URL": "redis://redis.invalid:6379/0",
        "ALLOWED_HOSTS": "api.taskbeavo.hr",
    }
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )

    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"errors": True, "data": None}


def test_production_settings_fail_fast_without_secrets() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"SECRET_KEY", "DATABASE_URL", "REDIS_URL", "APP_ENV"}
    }
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
    out = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )

    assert out.returncode != 0
    assert "Set the SECRET_KEY environment variable" in out.stderr


def test_query_depth_is_limited(settings) -> None:
    # The skeleton schema has no nested types yet and the limiter skips
    # introspection fields, so run the schema's depth rule against a nested SDL.
    settings.GRAPHQL_MAX_DEPTH = 3
    built = [factory() for factory in build_schema(app_env="test").extensions]
    (limiter,) = [ext for ext in built if isinstance(ext, QueryDepthLimiter)]
    nested = build_sdl_schema("type Query { node: Node } type Node { child: Node leaf: Int }")

    def errors(query: str) -> list[str]:
        return [e.message for e in validate(nested, parse(query), limiter.validation_rules)]

    assert errors("{ node { child { child { leaf } } } }") == []
    assert errors("{ node { child { child { child { leaf } } } } }") == [
        "'anonymous' exceeds maximum operation depth of 3"
    ]


def test_aliases_are_limited(settings) -> None:
    aliases = " ".join(f"a{i}: apiVersion" for i in range(settings.GRAPHQL_MAX_ALIASES + 1))

    result = build_schema(app_env="test").execute_sync(f"{{ {aliases} }}")

    assert result.errors
