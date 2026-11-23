"""Plain Django views beside the schema: what the process holds, and what a request arrived with."""

import sys

from django.http import HttpRequest, JsonResponse
from django.urls import path


def modules(request: HttpRequest) -> JsonResponse:
    """Whether OpenTelemetry's SDK is in this process."""
    return JsonResponse(
        {
            "sdk": sorted(
                name for name in sys.modules if name.startswith("opentelemetry.sdk")
            )
        }
    )


def echo(request: HttpRequest) -> JsonResponse:
    """The trace a request arrived in, as its header says it."""
    return JsonResponse({"traceparent": request.headers.get("traceparent")})


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True})


urlpatterns = [path("modules", modules), path("echo", echo), path("ht", health)]
