"""Hard rule d: query complexity limit and mutation rate limiting."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import strawberry
from asgiref.sync import async_to_sync
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.http import HttpResponse
from django.test import Client, RequestFactory
from graphql import build_schema as build_sdl_schema
from graphql import parse, validate
from redis.exceptions import ConnectionError as RedisConnectionError

from accounts.tests.factories import access_token, make_user
from config.graphql_limits import (
    MUTATIONS_REQUIRE_HTTP,
    RATE_LIMITED,
    MutationRateLimiter,
    QueryComplexityLimiter,
    complexity_rule,
)
from config.schema import build_schema
from config.views import GraphQLView
from config.ws import WSContext

BACKEND_DIR = Path(__file__).resolve().parents[2]

NESTED = build_sdl_schema(
    """
    type Query { node: Node nodes: [Node!]! }
    type Node { leaf: Int child: Node children: [Node!]! }
    """
)


def complexity_errors(query: str, *, max_complexity: int, multiplier: int = 10) -> list[str]:
    rule = complexity_rule(max_complexity=max_complexity, list_multiplier=multiplier)
    return [e.message for e in validate(NESTED, parse(query), [rule])]


def test_complexity_counts_each_field_once() -> None:
    # node + child + leaf = 3
    assert complexity_errors("{ node { child { leaf } } }", max_complexity=3) == []
    assert complexity_errors("{ node { child { leaf } } }", max_complexity=2) == [
        "Query complexity 3 exceeds the maximum of 2."
    ]


def test_complexity_multiplies_what_a_list_field_selects() -> None:
    # nodes(1) + 10 x leaf(1) = 11; nested lists multiply again: 1 + 10 x (1 + 10 x 1) = 111
    assert complexity_errors("{ nodes { leaf } }", max_complexity=11) == []
    assert complexity_errors("{ nodes { leaf } }", max_complexity=10)
    assert complexity_errors("{ nodes { children { leaf } } }", max_complexity=111) == []
    assert complexity_errors("{ nodes { children { leaf } } }", max_complexity=110)


def test_complexity_follows_fragments_and_skips_typename() -> None:
    query = """
        query { node { __typename ...F ... on Node { leaf } } }
        fragment F on Node { child { leaf } }
    """
    # node + child + leaf (fragment) + leaf (inline) = 4
    assert complexity_errors(query, max_complexity=4) == []
    assert complexity_errors(query, max_complexity=3)


def test_complexity_survives_fragment_cycles() -> None:
    query = "{ node { ...A } } fragment A on Node { child { ...A } }"

    # graphql-core's own rules report the cycle; this rule must just terminate.
    rule = complexity_rule(max_complexity=5, list_multiplier=10)
    assert validate(NESTED, parse(query), [rule]) == []


def test_served_schema_applies_the_complexity_limit(settings) -> None:
    settings.GRAPHQL_MAX_COMPLEXITY = 1
    schema = build_schema(app_env="test")

    assert schema.execute_sync("{ apiVersion }").errors is None
    result = schema.execute_sync("{ a: apiVersion b: apiVersion }")
    assert result.errors
    assert result.errors[0].message == "Query complexity 2 exceeds the maximum of 1."


def test_served_schema_includes_both_limits() -> None:
    built = [factory() for factory in build_schema(app_env="test").extensions]

    assert any(isinstance(ext, QueryComplexityLimiter) for ext in built)
    assert any(isinstance(ext, MutationRateLimiter) for ext in built)


@strawberry.type
class _Query:
    @strawberry.field
    def ping(self) -> str:
        return "pong"


@strawberry.type
class _Mutation:
    @strawberry.mutation
    def touch(self) -> int:
        return 1

    @strawberry.mutation
    def other(self) -> int:
        return 2


@pytest.fixture
def rate_limit_cache():
    cache = caches["graphql_rate_limit"]
    cache.clear()
    yield cache
    cache.clear()


@pytest.fixture
def limited_schema(settings, rate_limit_cache) -> strawberry.Schema:
    settings.GRAPHQL_MUTATION_RATE_LIMIT = 2
    settings.GRAPHQL_MUTATION_RATE_WINDOW_SECONDS = 60
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {}
    return strawberry.Schema(query=_Query, mutation=_Mutation, extensions=[MutationRateLimiter])


def http_context(ip: str = "203.0.113.7", user=None) -> SimpleNamespace:
    request = SimpleNamespace(META={"REMOTE_ADDR": ip})
    return SimpleNamespace(request=request, user=user)


def run(schema: strawberry.Schema, query: str, context: SimpleNamespace):
    return async_to_sync(schema.execute)(query, context_value=context)


def test_mutations_over_the_limit_are_rejected_with_a_code(limited_schema) -> None:
    ctx = http_context()
    assert run(limited_schema, "mutation { touch }", ctx).data == {"touch": 1}
    assert run(limited_schema, "mutation { touch }", ctx).data == {"touch": 1}

    result = run(limited_schema, "mutation { touch }", ctx)

    assert result.data is None
    assert result.errors
    assert result.errors[0].extensions == {"code": RATE_LIMITED}


def test_rate_limit_also_runs_under_sync_execution(limited_schema) -> None:
    ctx = http_context()
    for _ in range(2):
        assert limited_schema.execute_sync("mutation { touch }", context_value=ctx).errors is None

    result = limited_schema.execute_sync("mutation { touch }", context_value=ctx)

    assert result.errors
    assert result.errors[0].extensions == {"code": RATE_LIMITED}


def test_each_mutation_field_counts_and_has_its_own_bucket(limited_schema) -> None:
    ctx = http_context()
    # Two aliased calls in one operation use up the touch budget.
    assert run(limited_schema, "mutation { a: touch b: touch }", ctx).errors is None
    assert run(limited_schema, "mutation { touch }", ctx).errors
    assert run(limited_schema, "mutation { other }", ctx).data == {"other": 2}


def test_queries_are_not_rate_limited(limited_schema) -> None:
    ctx = http_context()
    for _ in range(5):
        assert run(limited_schema, "{ ping }", ctx).errors is None


def test_clients_are_limited_separately(limited_schema) -> None:
    for _ in range(2):
        run(limited_schema, "mutation { touch }", http_context(ip="203.0.113.7"))

    assert run(limited_schema, "mutation { touch }", http_context(ip="203.0.113.7")).errors
    assert run(limited_schema, "mutation { touch }", http_context(ip="198.51.100.1")).errors is None


def test_signed_in_users_are_limited_by_account_not_address(limited_schema) -> None:
    alice = SimpleNamespace(is_authenticated=True, pk=1)
    bob = SimpleNamespace(is_authenticated=True, pk=2)
    for _ in range(2):
        run(limited_schema, "mutation { touch }", http_context(user=alice))

    assert run(limited_schema, "mutation { touch }", http_context(user=alice)).errors
    # Same address, different account.
    assert run(limited_schema, "mutation { touch }", http_context(user=bob)).errors is None


def test_per_mutation_override(limited_schema, settings) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"touch": 1}
    ctx = http_context()

    assert run(limited_schema, "mutation { touch }", ctx).errors is None
    assert run(limited_schema, "mutation { touch }", ctx).errors


def test_anonymous_clients_behind_the_same_proxy_are_limited_separately(
    limited_schema, settings
) -> None:
    settings.GRAPHQL_TRUSTED_PROXY_COUNT = 1

    def via_proxy(client_ip: str) -> SimpleNamespace:
        # A spoofed first entry must not matter: the proxy appends the real client address.
        meta = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": f"1.2.3.4, {client_ip}"}
        return SimpleNamespace(request=SimpleNamespace(META=meta), user=None)

    for _ in range(2):
        run(limited_schema, "mutation { touch }", via_proxy("203.0.113.7"))

    assert run(limited_schema, "mutation { touch }", via_proxy("203.0.113.7")).errors
    assert run(limited_schema, "mutation { touch }", via_proxy("198.51.100.1")).errors is None


def test_mutations_over_the_websocket_are_refused(limited_schema, rate_limit_cache) -> None:
    """Behind Render every WebSocket shares the proxy address, so it can't be rate-limited."""
    # Even a consumer that looks like an HTTP request is refused: the context type decides.
    consumer = SimpleNamespace(scope={"client": ("10.0.0.1", 5000)}, META={"REMOTE_ADDR": "x"})
    ws_context = WSContext(request=consumer, ws=consumer, connection_params={})  # pyright: ignore[reportArgumentType]

    result = async_to_sync(limited_schema.execute)("mutation { touch }", context_value=ws_context)

    assert result.data is None
    assert result.errors
    assert result.errors[0].extensions == {"code": MUTATIONS_REQUIRE_HTTP}
    # Refused before counting, so it uses no client's budget.
    assert run(limited_schema, "mutation { touch }", http_context()).errors is None
    assert run(limited_schema, "mutation { touch }", http_context()).errors is None


@strawberry.type
class _WhoAmI:
    @strawberry.field
    def who(self, info: strawberry.Info) -> str:
        return str(info.context.user.pk)


@pytest.mark.django_db
def test_graphql_view_passes_the_bearer_tokens_user_to_the_context() -> None:
    user = make_user("ana")
    request = RequestFactory().post(
        "/graphql",
        {"query": "{ who }"},
        content_type="application/json",
        headers={"Authorization": f"Bearer {access_token(user)}"},
    )

    view = GraphQLView.as_view(schema=strawberry.Schema(query=_WhoAmI))
    response = async_to_sync(view)(request)

    assert response.status_code == 200
    assert json.loads(response.content) == {"data": {"who": str(user.pk)}}


@pytest.mark.django_db
def test_graphql_view_ignores_the_django_session_cookie(client: Client) -> None:
    """The endpoint is CSRF-exempt, so an ambient cookie (the admin's) must not sign in."""
    user = make_user("ana")
    client.force_login(user)
    request = RequestFactory().post(
        "/graphql", {"query": "{ who }"}, content_type="application/json"
    )
    request.session = client.session
    AuthenticationMiddleware(lambda _: HttpResponse()).process_request(request)

    view = GraphQLView.as_view(schema=strawberry.Schema(query=_WhoAmI))
    response = async_to_sync(view)(request)

    assert json.loads(response.content) == {"data": {"who": "None"}}


def test_rate_limit_counts_in_redis_across_workers(limited_schema, settings) -> None:
    """Production keeps the counters in Redis; add() then incr() must count there too."""
    settings.CACHES = {
        **settings.CACHES,
        "graphql_rate_limit": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": settings.REDIS_URL,
            "KEY_PREFIX": "test-rate-limit",
        },
    }
    caches["graphql_rate_limit"].clear()
    ctx = http_context()
    try:
        for _ in range(2):
            assert run(limited_schema, "mutation { touch }", ctx).errors is None
        assert run(limited_schema, "mutation { touch }", ctx).errors
    finally:
        caches["graphql_rate_limit"].clear()


def test_complexity_of_fragment_fan_out_is_computed_quickly() -> None:
    """Each fragment spreads the next one twice: 2^40 paths, but 40 fragments to cost."""
    depth = 40
    fragments = "".join(
        f"fragment F{i} on Node {{ a: child {{ ...F{i + 1} }} b: child {{ ...F{i + 1} }} }}"
        for i in range(depth)
    )
    query = f"{{ node {{ ...F0 }} }} {fragments} fragment F{depth} on Node {{ leaf }}"

    errors = complexity_errors(query, max_complexity=1000)

    assert len(errors) == 1
    assert "exceeds the maximum of 1000" in errors[0]


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_render_settings_trust_one_proxy_by_default(app_env: str) -> None:
    """Staging and production both run config.settings.production behind Render's proxy."""
    script = (
        "import django; django.setup(); from django.conf import settings;"
        "print(settings.GRAPHQL_TRUSTED_PROXY_COUNT)"
    )
    env = {
        **{k: v for k, v in os.environ.items() if k != "GRAPHQL_TRUSTED_PROXY_COUNT"},
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "APP_ENV": app_env,
        "SECRET_KEY": "x" * 50,
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
        "STRIPE_SECRET_KEY": f"sk_{'live' if app_env == 'production' else 'test'}_not_a_real_key",
        "STRIPE_WEBHOOK_SECRET": "whsec_not_secret",
        "DATABASE_URL": "postgis://u:p@db.invalid:5432/db",
        "REDIS_URL": "redis://redis.invalid:6379/0",
    }
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "1"


def test_local_settings_trust_no_proxy_and_allow_the_android_emulator() -> None:
    script = (
        "import django; django.setup(); from django.conf import settings;"
        "print(settings.GRAPHQL_TRUSTED_PROXY_COUNT, '10.0.2.2' in settings.ALLOWED_HOSTS)"
    )
    drop = {"GRAPHQL_TRUSTED_PROXY_COUNT", "ALLOWED_HOSTS", "APP_ENV", "DJANGO_SETTINGS_MODULE"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.local"
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["0", "True"]


class _BrokenCache:
    def add(self, *args, **kwargs):
        raise RedisConnectionError("redis down")

    def incr(self, *args, **kwargs):
        raise RedisConnectionError("redis down")


def test_rate_limit_counts_per_process_when_redis_is_down(
    limited_schema, monkeypatch, caplog
) -> None:
    local = LocMemCache("fallback", {})
    monkeypatch.setattr(
        "config.graphql_limits.caches", {"graphql_rate_limit": _BrokenCache(), "default": local}
    )
    ctx = http_context()

    for _ in range(2):
        assert run(limited_schema, "mutation { touch }", ctx).data == {"touch": 1}
    result = run(limited_schema, "mutation { touch }", ctx)

    assert result.errors
    assert result.errors[0].extensions == {"code": RATE_LIMITED}
    assert "GraphQL rate limit store unavailable: ConnectionError" in caplog.text
    assert "203.0.113.7" not in caplog.text


class _ExpiringCache(LocMemCache):
    """The key expires between add() and incr() on the first call."""

    expired_once = False

    def incr(self, key, delta=1, version=None):
        if not self.expired_once:
            self.expired_once = True
            self.delete(key, version=version)
        return super().incr(key, delta, version)


def test_rate_limit_restarts_the_count_when_the_key_expires_mid_call(
    limited_schema, monkeypatch
) -> None:
    monkeypatch.setattr(
        "config.graphql_limits.caches",
        {"graphql_rate_limit": _ExpiringCache("expiring", {}), "default": LocMemCache("d", {})},
    )
    ctx = http_context()

    assert run(limited_schema, "mutation { touch }", ctx).errors is None
    assert run(limited_schema, "mutation { touch }", ctx).errors is None
    assert run(limited_schema, "mutation { touch }", ctx).errors


def test_spoofed_forwarded_prefix_cannot_evade_the_limit(limited_schema, settings) -> None:
    settings.GRAPHQL_TRUSTED_PROXY_COUNT = 1

    def spoofed(prefix: str) -> SimpleNamespace:
        # The client sends its own X-Forwarded-For; Render appends the real address.
        meta = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": f"{prefix}, 203.0.113.7"}
        return SimpleNamespace(request=SimpleNamespace(META=meta), user=None)

    for prefix in ("1.1.1.1", "2.2.2.2"):
        assert run(limited_schema, "mutation { touch }", spoofed(prefix)).errors is None

    result = run(limited_schema, "mutation { touch }", spoofed("3.3.3.3, 4.4.4.4"))
    assert result.errors
    assert result.errors[0].extensions == {"code": RATE_LIMITED}


def _via_web_proxy(client_ip: str, secret: str) -> SimpleNamespace:
    # The /api/graphql route handler on Vercel, then Render's proxy: XFF ends in Vercel's
    # address, which is the same for every visitor.
    meta = {
        "REMOTE_ADDR": "10.0.0.1",
        "HTTP_X_FORWARDED_FOR": "76.76.21.21",
        "HTTP_X_TASKBEAVO_CLIENT_IP": client_ip,
        "HTTP_X_TASKBEAVO_PROXY_SECRET": secret,
    }
    return SimpleNamespace(request=SimpleNamespace(META=meta), user=None)


def test_web_proxy_visitors_are_limited_separately(limited_schema, settings) -> None:
    settings.GRAPHQL_TRUSTED_PROXY_COUNT = 1
    settings.WEB_PROXY_SECRET = "proxy-secret"
    for _ in range(2):
        run(limited_schema, "mutation { touch }", _via_web_proxy("203.0.113.7", "proxy-secret"))

    assert run(
        limited_schema, "mutation { touch }", _via_web_proxy("203.0.113.7", "proxy-secret")
    ).errors
    assert (
        run(
            limited_schema, "mutation { touch }", _via_web_proxy("198.51.100.1", "proxy-secret")
        ).errors
        is None
    )


@pytest.mark.parametrize("configured, sent", [("proxy-secret", "wrong"), ("", "")])
def test_client_ip_header_is_ignored_without_the_proxy_secret(
    limited_schema, settings, configured: str, sent: str
) -> None:
    """A direct client can't pick its own bucket by sending the header."""
    settings.GRAPHQL_TRUSTED_PROXY_COUNT = 1
    settings.WEB_PROXY_SECRET = configured
    for address in ("203.0.113.7", "198.51.100.1"):
        assert (
            run(limited_schema, "mutation { touch }", _via_web_proxy(address, sent)).errors is None
        )

    # All three share the XFF address's bucket.
    assert run(limited_schema, "mutation { touch }", _via_web_proxy("192.0.2.9", sent)).errors


def test_a_wrong_proxy_secret_is_logged_without_its_value(limited_schema, settings, caplog) -> None:
    """A secret that differs between Vercel and Render would otherwise fail silently."""
    settings.GRAPHQL_TRUSTED_PROXY_COUNT = 1
    settings.WEB_PROXY_SECRET = "proxy-secret"

    run(limited_schema, "mutation { touch }", _via_web_proxy("203.0.113.7", "stale-secret"))

    assert "X-TaskBeavo-Proxy-Secret does not match WEB_PROXY_SECRET" in caplog.text
    assert "stale-secret" not in caplog.text
    assert "proxy-secret" not in caplog.text
    assert "203.0.113.7" not in caplog.text
