"""A real collector, jaeger and Postgres (dokker), and the test service as processes of its own.

What OpenTelemetry sets up belongs to a process and cannot be taken back, so a server that
records and one that does not are two daphne processes, each started the way an image starts.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import psycopg
import pytest
from dokker import PortNotFoundError, testing

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@dataclass(frozen=True)
class Stack:
    collector: str
    """Where services send to (``telemetry.endpoint``)."""
    metrics: str
    """The collector's own page of the metrics it was sent."""
    jaeger: str
    """jaeger's query API."""
    db_port: int


def _until(ready, timeout: float = 90.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return ready()
        except (httpx.HTTPError, psycopg.OperationalError, PortNotFoundError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.3)


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    with testing(os.path.join(os.path.dirname(__file__), "docker-compose.yaml")) as deployment:
        deployment.up()

        def ready() -> Stack:
            found = Stack(
                collector=f"http://localhost:{deployment.get_port('collector', 4318)}",
                metrics=f"http://localhost:{deployment.get_port('collector', 8889)}/metrics",
                jaeger=f"http://localhost:{deployment.get_port('jaeger', 16686)}",
                db_port=deployment.get_port("db", 5432),
            )
            with psycopg.connect(dbname="testdb", user="test", password="test", host="localhost", port=found.db_port, connect_timeout=1) as connection:
                connection.execute("SELECT 1")
            httpx.get(f"{found.jaeger}/api/services", timeout=2).raise_for_status()
            # Anything but a refused connection: the receiver is listening.
            httpx.post(f"{found.collector}/v1/traces", content=b"", timeout=2)
            return found

        yield _until(ready)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _serve(stack: Stack, environment: dict[str, str]) -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(port), "telemetry_project.asgi:application"],
        cwd=ROOT,
        env={
            **{name: value for name, value in os.environ.items() if not name.startswith(("TELEMETRY__", "OTEL_", "DJANGO_SETTINGS"))},
            "ARKITEKT_SERVICE": "tests.server.served",
            "ARKITEKT_CONFIG_FILE": os.devnull,
            "TELEMETRY_TEST_DB_PORT": str(stack.db_port),
            **environment,
        },
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _until(lambda: httpx.get(f"{url}/ht", timeout=2).raise_for_status(), timeout=30)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def recording(stack: Stack) -> Iterator[str]:
    """The service with a collector named, sending what it has every fraction of a second."""
    yield from _serve(stack, {"TELEMETRY__ENDPOINT": stack.collector, "OTEL_BSP_SCHEDULE_DELAY": "200", "OTEL_METRIC_EXPORT_INTERVAL": "500"})


@pytest.fixture(scope="session")
def without_statements(stack: Stack) -> Iterator[str]:
    """The service recording, but no span per database statement."""
    yield from _serve(stack, {"TELEMETRY__ENDPOINT": stack.collector, "TELEMETRY__STATEMENTS": "false", "OTEL_BSP_SCHEDULE_DELAY": "200", "OTEL_METRIC_EXPORT_INTERVAL": "500"})


@pytest.fixture(scope="session")
def silent(stack: Stack) -> Iterator[str]:
    """The service as it is by default: no collector named."""
    yield from _serve(stack, {})
