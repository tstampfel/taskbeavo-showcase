"""Answers Render's health check before Django validates the Host header."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.views import healthz


class HealthCheckMiddleware:
    """Serves GET /healthz ahead of every other middleware.

    Render probes the instance directly, with a Host header that is not in ALLOWED_HOSTS,
    and CommonMiddleware would answer that with 400. The response only says "ok" or
    "error" per dependency, so skipping the host check here exposes nothing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == "/healthz":
            return healthz(request)
        return self.get_response(request)
