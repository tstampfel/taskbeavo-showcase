"""Celery app for the worker (`celery -A config worker`) and Beat (`celery -A config beat`)."""

import os

from celery import Celery
from celery.signals import beat_init, celeryd_init, worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("taskbeavo")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# Sentry starts in the worker's main process, before the pool forks: its Celery
# integration patches the task tracer builder, and prefork children build their task
# tracers before `worker_process_init` is sent. OpenTelemetry starts in each child,
# after the fork, because the span exporter's background thread does not survive it.
# Both are no-ops without their keys.
@celeryd_init.connect(weak=False)
def _setup_worker_sentry(**_: object) -> None:
    from config import telemetry

    telemetry.setup("worker", tracing=False)


@worker_process_init.connect(weak=False)
def _setup_worker_tracing(**_: object) -> None:
    from config import telemetry

    telemetry.setup("worker", sentry=False)


@beat_init.connect(weak=False)
def _setup_beat_telemetry(**_: object) -> None:
    from config import telemetry

    telemetry.setup("beat")
