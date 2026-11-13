from unittest import mock

import pytest
from django.db import OperationalError
from django.test import Client


@pytest.mark.django_db
def test_healthz_ok_when_database_and_redis_answer(client: Client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.django_db
def test_healthz_503_when_redis_is_down(client: Client, settings) -> None:
    settings.REDIS_URL = "redis://127.0.0.1:1/0"

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"database": "ok", "redis": "error"}}


@pytest.mark.django_db
def test_healthz_503_when_database_is_down(client: Client) -> None:
    with mock.patch(
        "core.views.connection.cursor",
        side_effect=OperationalError("could not connect to server at 10.0.0.1"),
    ):
        response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "error", "checks": {"database": "error", "redis": "ok"}}
    assert "10.0.0.1" not in response.content.decode()


def test_healthz_rejects_post(client: Client) -> None:
    assert client.post("/healthz").status_code == 405


@pytest.mark.django_db
def test_healthz_answers_render_probe_with_unknown_host(client: Client, settings) -> None:
    settings.ALLOWED_HOSTS = ["api.taskbeavo.com"]
    settings.MIDDLEWARE = ["core.middleware.HealthCheckMiddleware", *settings.MIDDLEWARE]

    response = client.get("/healthz", HTTP_HOST="10.214.3.7:10000")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.django_db
def test_other_paths_still_check_the_host(client: Client, settings) -> None:
    settings.ALLOWED_HOSTS = ["api.taskbeavo.com"]
    settings.MIDDLEWARE = ["core.middleware.HealthCheckMiddleware", *settings.MIDDLEWARE]

    response = client.get("/graphql", HTTP_HOST="10.214.3.7:10000")

    assert response.status_code == 400
