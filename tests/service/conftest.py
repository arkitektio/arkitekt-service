"""Stand-ins for rekuest: its signal intake (a local HTTP server) and its key (signing requests to the service)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.conf import settings as django_settings

from arkitekt_service import trust
from test_project.urls import service

REKUEST = "live.arkitekt.rekuest"


class Intake:
    """Records every POST and whether its service token verifies as rekuest would check it."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        intake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                try:
                    verified = trust.verify("POST", self.path, body, self.headers.get("Authorization"), audience=REKUEST)
                    issuer = verified.issuer
                except trust.TrustError as error:
                    issuer = f"refused: {error}"
                intake.received.append({"path": self.path, "headers": dict(self.headers), "body": body, "json": json.loads(body), "issuer": issuer})
                self.send_response(202)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def wait(self, count: int = 1, timeout: float = 10) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.received) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.received


@pytest.fixture
def intake(settings):
    server = Intake()
    settings.REKUEST_SERVICE = {**settings.REKUEST_SERVICE, "REKUEST_URL": server.url}
    yield server
    server.server.shutdown()


def as_rekuest(method: str, path: str, body: bytes = b"", *, audience: str = "live.arkitekt.testsvc") -> dict:
    """Headers of a request rekuest sends to the service."""
    return {"Authorization": trust.sign(method, path, body, issuer=REKUEST, audience=audience, key=django_settings.REKUEST_KEY)}


@pytest.fixture(autouse=True)
def clean_service():
    yield
    service._signals.clear()
    service._structures.clear()
