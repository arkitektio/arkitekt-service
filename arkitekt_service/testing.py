"""Prove a service's image as a hub runs it: described, configured, prepared, served, and talked to.

A pytest plugin. A service's repository says, in ``tests/conftest.py``::

    pytest_plugins = ["arkitekt_service.testing"]

and in one test::

    import pytest
    from arkitekt_service.testing import check_service

    @pytest.mark.hub
    def test_the_image_runs_in_a_hub(service_hub):
        check_service(service_hub)

``service_hub`` starts a hub for the session with nothing but a coordination server and the
image under test (``--service-image``, or ``$ARKITEKT_SERVICE_IMAGE``): the installer asks the
image what it is, has it write its own config, runs its ``migrate`` job and starts it, exactly
as for any deployment. ``check_service`` then asks of the running service what every service
has to answer.

It is an end-to-end test: it needs Docker and `konstruktor <https://github.com/arkitektio/konstruktor>`_
(``pip install konstruktor``), takes half a minute, and belongs in the job that builds the
image, not in the suite run on every save. Without either, the tests are skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from konstruktor import Hub  # pyright: ignore[reportMissingImports]

#: Names the image under test, when ``--service-image`` does not.
IMAGE = "ARKITEKT_SERVICE_IMAGE"
#: The redeem grant of a hub's coordination server.
REDEEM = "urn:fakts:grant-type:redeem"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("arkitekt-service").addoption("--service-image", default=None, help=f"The image `service_hub` runs (default: ${IMAGE}).")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "hub: needs a hub started for the tests (Docker and konstruktor): end to end, not for the dev loop")


@dataclass(frozen=True)
class ServiceHub:
    """A hub running one service's image, and the name the image gave itself."""

    hub: Hub
    service: str
    image: str

    @property
    def url(self) -> str:
        """Where the service answers, through the hub's gateway."""
        return self.hub.services[self.service].url

    def token(self, app: str = "smoke") -> str:
        """An access token the hub's coordination server issues to an app that requires the service."""
        return redeem(self.hub, self.service, app=app)

    def graphql(self, query: str, *, token: str | None = None) -> dict[str, Any]:
        """One GraphQL request to the service; with ``token`` as the app it was issued to."""
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
        return _json(urllib.request.Request(f"{self.url}/graphql", data=json.dumps({"query": query}).encode(), headers=headers))


def _json(request: urllib.request.Request | str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {"http": error.code, "body": error.read().decode(errors="replace")[:500]}


def redeem(hub: Hub, service: str, *, app: str = "smoke") -> str:
    """Trade one of the hub's redeem tokens for an access token of an app that requires ``service``.

    The way any app gets in, unattended: nothing here is a test double.
    """
    well_known = _json(f"{hub.fakts_url}/.well-known/fakts")
    manifest = {
        "identifier": f"live.arkitekt.testing.{app}",
        "version": "1.0.0",
        "scopes": ["openid"],
        "requirements": [{"key": service, "service": hub.services[service].identifier}],
    }
    body = urllib.parse.urlencode({"grant_type": REDEEM, "redeem_token": hub.redeem_token(app), "manifest": json.dumps(manifest)}).encode()
    grant = _json(urllib.request.Request(well_known["token_endpoint"], data=body))
    assert "access_token" in grant, f"the hub's coordination server issued no token for {service}: {grant}"
    assert grant.get("statuses", {}).get(service) == "granted", f"{service} was not granted to the app: {grant.get('statuses')}"
    return grant["access_token"]


def _restarts(running: ServiceHub) -> int | None:
    """How often the service's container was restarted, when Docker says."""
    container = f"{running.hub.directory.name}-{running.service}-1"
    said = subprocess.run(["docker", "inspect", "-f", "{{.RestartCount}}", container], capture_output=True, text=True, check=False)
    return int(said.stdout.strip()) if said.returncode == 0 and said.stdout.strip().isdigit() else None


def check_service(running: ServiceHub, *, admin: bool = True) -> None:
    """What every service has to answer, asked of a running one.

    - it became ready: its health endpoint answers through the gateway;
    - it does not answer GraphQL to nobody, and does to an app the hub issued a token to;
    - its ``plan`` job finds nothing left to apply: ``migrate`` ran, and ran all of it;
    - an operator can be given an account in its admin (``admin=False`` for a service without one);
    - it did not restart on the way.
    """
    hub, service = running.hub, running.service
    not_ready = [endpoint for endpoint in hub.wait(timeout=180) if not endpoint.ready]
    assert not not_ready, f"not ready: {', '.join(endpoint.name for endpoint in not_ready)}\n{hub.logs(service)}"

    with urllib.request.urlopen(hub.services[service].health_url, timeout=30) as health:
        assert health.status == 200

    anonymous = running.graphql("{ __typename }")
    assert anonymous.get("data") is None, f"{service} answered a request that carried no token: {anonymous}"

    answer = running.graphql("{ __typename }", token=running.token())
    assert answer.get("data") == {"__typename": "Query"} and "errors" not in answer, f"{service} refused a token its own hub issued: {answer}"

    hub.job(service, "plan")
    if admin:
        hub.superuser(service, "smoke", "smoke-pass-1")

    restarts = _restarts(running)
    assert not restarts, f"{service} restarted {restarts} time(s) before it served:\n{hub.logs(service)}"


@pytest.fixture(scope="session")
def service_image(pytestconfig: pytest.Config) -> str:
    """The image under test: ``--service-image``, else ``$ARKITEKT_SERVICE_IMAGE``."""
    image = pytestconfig.getoption("--service-image") or os.environ.get(IMAGE)
    if not image:
        pytest.skip(f"no image to test: pass --service-image, or set ${IMAGE}")
    return image


@pytest.fixture(scope="session")
def service_hub(request: pytest.FixtureRequest, service_image: str) -> Iterator[ServiceHub]:
    """A hub for the session that runs the image under test, and a coordination server."""
    pytest.importorskip("konstruktor", reason="the hub is made by konstruktor: pip install konstruktor")
    if not request.config.pluginmanager.hasplugin("konstruktor"):
        request.config.pluginmanager.import_plugin("konstruktor.pytest_plugin")
    make = request.getfixturevalue("konstruktor_hub")
    hub: Hub = make(service_images=[service_image], redeem_tokens=4)
    services = [name for name in hub.services if name != "lok"]
    assert len(services) == 1, f"a hub made for one image runs {services}"
    yield ServiceHub(hub=hub, service=services[0], image=service_image)
