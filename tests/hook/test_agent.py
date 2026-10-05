"""rekuest-hook end to end, against a real local stand-in for rekuest's intake."""

import json

import pytest
from django.test import Client
from django.urls import reverse
from joserfc.jwk import OKPKey
from arkitekt_service import trust

from arkitekt_service.hook import HookAgent
from hook_project.urls import agent
from tests.hook.conftest import as_rekuest


class TestDeclaration:
    def test_an_agent_needs_no_service(self):
        local = HookAgent("alone", description="Works alone.")
        assert local.manifest() == {"agent": "alone", "identifier": "live.arkitekt.alone", "description": "Works alone.", "actions": [], "manifest_version": 1}

    def test_actions_take_name_and_description_from_the_docstring(self):
        local = HookAgent("doc")

        @local.action
        def compact() -> dict:
            """Compact the store

            Merges small files into big ones.
            """
            return {}

        @local.action
        def bare() -> dict:
            return {}

        first, second = local.manifest()["actions"]
        assert first == {"interface": "compact", "name": "Compact the store", "description": "Merges small files into big ones."}  # offered, not wired to anything
        assert (second["interface"], second["name"], second["description"]) == ("bare", "bare", None)

    def test_an_action_cannot_be_wired_to_a_schedule(self):
        with pytest.raises(TypeError):
            HookAgent("x").action(default_interval=5)

    def test_an_interface_cannot_be_taken_twice(self):
        local = HookAgent("twice")
        local.action(interface="a")(lambda: {})
        with pytest.raises(ValueError):
            local.action(interface="a")(lambda: {})

    def test_agents_do_not_share_actions(self):
        a, b = HookAgent("a"), HookAgent("b")
        a.action(interface="only_a")(lambda: {})
        assert list(a.actions) == ["only_a"] and b.actions == {}

    def test_the_settings_name_overrides_the_declared_one(self, settings):
        settings.REKUEST_HOOK = {**settings.REKUEST_HOOK, "AGENT": "worker-2"}
        assert HookAgent("worker").agent_name() == "worker-2"

    def test_the_identifier_is_the_instances(self, settings):
        assert HookAgent("mikro").signing_identifier() == "live.arkitekt.mikro"
        # An agent named otherwise than the instance it runs in says whose key it signs with.
        assert HookAgent("nightly", identifier="live.arkitekt.mikro").signing_identifier() == "live.arkitekt.mikro"
        settings.REKUEST_HOOK = {**settings.REKUEST_HOOK, "IDENTIFIER": "org.example.x"}
        assert HookAgent("mikro").signing_identifier() == "org.example.x"


class TestEndpoints:
    def test_the_manifest_is_signed_and_lists_the_actions(self):
        agent.action(interface="tidy")(lambda: {})
        client = Client()
        url = reverse("rekuest_hook_manifest")

        assert client.get(url).status_code == 401
        manifest = client.get(url, headers=as_rekuest("GET", url)).json()
        assert manifest["agent"] == "worker" and manifest["identifier"] == "live.arkitekt.worker"
        assert [a["interface"] for a in manifest["actions"]] == ["tidy"]
        assert "structures" not in manifest and "signals" not in manifest

    def test_only_rekuest_signing_for_this_agent_is_let_in(self, settings):
        url = reverse("rekuest_hook")
        body = json.dumps({"type": "ASSIGN", "task": "1", "interface": "tidy", "args": {}}).encode()
        client = Client()

        def post(headers):
            return client.post(url, data=body, content_type="application/json", headers=headers).status_code

        assert post({}) == 401
        assert post(as_rekuest("POST", url, body, audience="live.arkitekt.other")) == 401  # for another instance
        assert post(as_rekuest("POST", url, b"{}")) == 401  # signed for another body
        assert post(as_rekuest("POST", "/elsewhere", body)) == 401  # signed for another path
        # The instance's own key, claiming to be rekuest: the bundle says that key is the worker's.
        impostor = trust.sign("POST", url, body, issuer="live.arkitekt.rekuest", audience="live.arkitekt.worker")
        assert post({"Authorization": impostor, "X-Rekuest-Agent": "7"}) == 401
        # A key the coord never vouched for.
        stranger = trust.sign("POST", url, body, issuer="live.arkitekt.rekuest", audience="live.arkitekt.worker", key=OKPKey.generate_key("Ed25519"))
        assert post({"Authorization": stranger, "X-Rekuest-Agent": "7"}) == 401
        settings.REKUEST_HOOK = None
        assert post(as_rekuest("POST", url, body)) == 503

    def test_an_assign_runs_and_reports_started_yield_completed(self, intake):
        agent.action(interface="tidy")(lambda: {"acted": 3})
        body = json.dumps({"type": "ASSIGN", "task": "41", "interface": "tidy", "args": {}}).encode()

        url = reverse("rekuest_hook")
        assert Client().post(url, data=body, content_type="application/json", headers=as_rekuest("POST", url, body)).status_code == 202
        reports = intake.wait(3)
        assert [r["json"]["type"] for r in reports] == ["STARTED", "YIELD", "COMPLETED"]
        assert all(r["path"] == "/agi/http/7" for r in reports)
        assert reports[1]["json"]["returns"] == {"acted": 3}
        assert all(r["issuer"] == "live.arkitekt.worker" for r in reports)  # signed with the instance key

    def test_an_action_is_told_which_organization_it_runs_for(self, intake):
        agent.action(interface="tidy_org")(lambda organization: {"tidied": organization})
        url = reverse("rekuest_hook")
        body = json.dumps({"type": "ASSIGN", "task": "9", "interface": "tidy_org", "args": {}, "org": "lab"}).encode()

        assert Client().post(url, data=body, content_type="application/json", headers=as_rekuest("POST", url, body)).status_code == 202
        events = [r["json"] for r in intake.wait(3)]
        assert [e["type"] for e in events] == ["STARTED", "YIELD", "COMPLETED"]
        assert events[1]["returns"] == {"tidied": "lab"}

    def test_an_async_action_is_awaited(self, intake):
        async def later() -> dict:
            return {"awaited": True}

        agent.action(interface="later")(later)
        url = reverse("rekuest_hook")
        body = json.dumps({"type": "ASSIGN", "task": "10", "interface": "later", "args": {}}).encode()
        Client().post(url, data=body, content_type="application/json", headers=as_rekuest("POST", url, body))
        assert intake.wait(3)[1]["json"]["returns"] == {"awaited": True}

    def test_a_failing_action_reports_critical(self, intake):
        def broken() -> dict:
            raise RuntimeError("disk full")

        agent.action(interface="broken")(broken)
        body = json.dumps({"type": "ASSIGN", "task": "42", "interface": "broken", "args": {}}).encode()
        url = reverse("rekuest_hook")
        Client().post(url, data=body, content_type="application/json", headers=as_rekuest("POST", url, body))
        reports = intake.wait(2)
        assert [r["json"]["type"] for r in reports] == ["STARTED", "CRITICAL"]
        assert "disk full" in reports[1]["json"]["error"]

    def test_an_unknown_interface_is_refused_and_reported(self, intake):
        body = json.dumps({"type": "ASSIGN", "task": "43", "interface": "nothing", "args": {}}).encode()
        url = reverse("rekuest_hook")
        assert Client().post(url, data=body, content_type="application/json", headers=as_rekuest("POST", url, body)).status_code == 404
        (report,) = intake.wait(1)
        assert report["json"]["type"] == "CRITICAL"
