"""GET /healthz for Render's health check: 200 when the database and Redis answer, else 503."""

import logging

import redis
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

REDIS_TIMEOUT_SECONDS = 2


def check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("healthz: database check failed")
        return False
    return True


def check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
        )
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        logger.exception("healthz: redis check failed")
        return False
    return True


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    # Only "ok"/"error" per dependency: error details go to the logs, never to the caller.
    checks = {
        "database": "ok" if check_database() else "error",
        "redis": "ok" if check_redis() else "error",
    }
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "error", "checks": checks},
        status=200 if healthy else 503,
    )
