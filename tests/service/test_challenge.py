"""The fakts alias challenge: a health route that proves this instance holds its key."""

import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings as django_settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.test import AsyncRequestFactory, RequestFactory
from django.views.decorators.csrf import csrf_exempt
from pytest_django.fixtures import Settings as SettingsWrapper

from arkitekt_service import trust
from arkitekt_service.service.views import answers_challenge


def verifies(nonce: str, signature: str) -> bool:
    """What a fakts client does (``fakts.challenge.verify_challenge_signature``), against the
    ``challenge_key`` the coord hands out for this instance."""
    pinned = trust.raw_public_key_b64(django_settings.SERVICE_KEY)
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(pinned, validate=True))
    try:
        public.verify(
            base64.b64decode(signature, validate=True),
            f"fakts-challenge-v1:{nonce}".encode(),
        )
    except InvalidSignature:
        return False
    return True


@answers_challenge
@csrf_exempt
def healthy(request: HttpRequest) -> HttpResponse:
    return JsonResponse({"Database": "working"})


@answers_challenge
def unhealthy(request: HttpRequest) -> HttpResponse:
    return HttpResponse("down", status=500)


@answers_challenge
async def async_healthy(request: HttpRequest) -> HttpResponse:
    return JsonResponse({"Database": "working"})


def body(response: HttpResponse) -> dict[str, str]:
    import json

    return json.loads(response.content)


def test_a_challenge_is_answered_with_a_signature_the_pinned_key_verifies() -> None:
    response = healthy(RequestFactory().get("/ht", {"nonce": "n-1"}))
    assert response.status_code == 200
    assert verifies("n-1", body(response)["signature"])
    assert not verifies("n-2", body(response)["signature"])


def test_without_a_nonce_the_health_answer_is_untouched() -> None:
    assert body(healthy(RequestFactory().get("/ht"))) == {"Database": "working"}
    assert healthy.csrf_exempt is True


def test_a_release_names_itself_in_every_health_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "Arkitekt-Service-Version" not in healthy(RequestFactory().get("/ht"))
    monkeypatch.setenv("ARKITEKT_SERVICE_VERSION", "12.1.0")
    assert healthy(RequestFactory().get("/ht"))["Arkitekt-Service-Version"] == "12.1.0"
    assert (
        healthy(RequestFactory().get("/ht", {"nonce": "n-1"}))[
            "Arkitekt-Service-Version"
        ]
        == "12.1.0"
    )


def test_an_unhealthy_service_does_not_sign() -> None:
    response = unhealthy(RequestFactory().get("/ht", {"nonce": "n-1"}))
    assert response.status_code == 500 and response.content == b"down"


def test_without_an_instance_key_the_health_answer_is_untouched(
    settings: SettingsWrapper,
) -> None:
    settings.INSTANCE = None
    assert body(healthy(RequestFactory().get("/ht", {"nonce": "n-1"}))) == {
        "Database": "working"
    }


@pytest.mark.parametrize("nonce", ["", "x" * 257])
def test_only_a_nonce_is_signed(nonce: str) -> None:
    assert healthy(RequestFactory().get("/ht", {"nonce": nonce})).status_code == 400


def test_an_async_health_view_is_wrapped_as_async() -> None:
    import asyncio

    response = asyncio.run(
        async_healthy(AsyncRequestFactory().get("/ht", {"nonce": "n-1"}))
    )
    assert verifies("n-1", body(response)["signature"])
