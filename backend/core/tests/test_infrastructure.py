import uuid

import pytest
from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from django.conf import settings
from django.db import connection

from config import celery_app
from core.tasks import ping


@pytest.mark.django_db
def test_postgis_extension_is_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT PostGIS_Version()")
        row = cursor.fetchone()

    assert row is not None
    assert row[0]


def test_production_channel_layer_is_redis() -> None:
    from config.settings import base

    assert base.CHANNEL_LAYERS["default"]["BACKEND"] == "channels_redis.core.RedisChannelLayer"


def test_redis_channel_layer_round_trip() -> None:
    layer = RedisChannelLayer(hosts=[settings.REDIS_URL])
    channel = f"test.{uuid.uuid4().hex}"

    async def round_trip() -> dict:
        await layer.send(channel, {"type": "test.message", "value": 1})
        message = await layer.receive(channel)
        await layer.flush()
        return message

    assert async_to_sync(round_trip)() == {"type": "test.message", "value": 1}


def test_celery_uses_redis_broker() -> None:
    assert celery_app.conf.broker_url == settings.REDIS_URL
    assert celery_app.conf.broker_url.startswith("redis://")


def test_celery_runs_registered_task() -> None:
    assert "core.ping" in celery_app.tasks
    assert ping.delay().get(timeout=5) == "pong"
