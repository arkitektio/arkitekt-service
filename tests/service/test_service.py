"""rekuest-service end to end, against a real local stand-in for rekuest's signal intake."""

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse
from joserfc.jwk import OKPKey

from arkitekt_service.service import Service, trust
from test_project.urls import service
from tests.service.conftest import as_rekuest


class TestDeclaration:
    def test_a_service_declares_no_actions_and_knows_no_agent(self):
        local = Service("plain")
        assert not hasattr(local, "action") and not hasattr(local, "hook_agent")
        assert set(local.manifest()) == {"service", "identifier", "description", "structures", "signals", "manifest_version"}
        assert local.manifest()["manifest_version"] == 3

    def test_signal_handles_check_their_kind(self):
        local = Service("kinds")
        created = local.signal("@kinds/thing", kinds=["CREATED"])
        both = local.signal("@kinds/other", kinds=["CREATED", "DELETED"])
        assert local.signal("@kinds/thing", kinds=["CREATED"]) is created
        with pytest.raises(ValueError):
            created.emit(1, organization="o", kind="DELETED")
        with pytest.raises(ValueError):
            both.emit(1, organization="o")
        with pytest.raises(ValueError):
            local.signal("@kinds/thing", kinds=["DELETED"])
        with pytest.raises(ValueError):
            local.signal("@kinds/bad", kinds=["EXPLODED"])

    def test_services_do_not_share_declarations(self):
        a, b = Service("a"), Service("b")
        a.signal("@a/thing")
        assert [s["identifier"] for s in a.manifest()["signals"]] == ["@a/thing"]
        assert b.manifest()["signals"] == []

    def test_the_settings_name_overrides_the_declared_one(self, settings):
        settings.REKUEST_SERVICE = {**settings.REKUEST_SERVICE, "SERVICE": "testsvc-2"}
        assert Service("testsvc").service_name() == "testsvc-2"

    def test_the_identifier_defaults_to_the_fakts_convention(self):
        assert Service("mikro").identifier == "live.arkitekt.mikro"
        assert Service("x", identifier="org.example.x").identifier == "org.example.x"


class TestManifestEndpoint:
    def test_the_manifest_is_served_to_rekuest(self):
        service.signal("@testsvc/room", kinds=["CREATED"], descriptors=["@testsvc/area"])
        client = Client()
        url = reverse("rekuest_service_manifest")
        assert url.endswith("_rekuest/service/manifest")

        manifest = client.get(url, headers=as_rekuest("GET", url)).json()
        assert manifest["service"] == "testsvc" and manifest["identifier"] == "live.arkitekt.testsvc"
        assert manifest["signals"] == [{"identifier": "@testsvc/room", "kinds": ["CREATED"], "descriptors": ["@testsvc/area"], "description": None}]
        assert manifest["structures"] == []

    def test_only_rekuest_signing_for_this_service_is_let_in(self, settings):
        url = reverse("rekuest_service_manifest")
        client = Client()

        def get(headers):
            return client.get(url, headers=headers).status_code

        assert get({}) == 401
        assert get(as_rekuest("GET", url, audience="live.arkitekt.other")) == 401  # for another service
        assert get(as_rekuest("GET", "/elsewhere")) == 401  # signed for another path
        assert get(as_rekuest("POST", url)) == 401  # signed for another method
        # The service's own key, claiming to be rekuest: the bundle says that key is testsvc's.
        impostor = trust.sign("GET", url, b"", issuer="live.arkitekt.rekuest", audience="live.arkitekt.testsvc")
        assert get({"Authorization": impostor}) == 401
        # A key the coord never vouched for.
        stranger = trust.sign("GET", url, b"", issuer="live.arkitekt.rekuest", audience="live.arkitekt.testsvc", key=OKPKey.generate_key("Ed25519"))
        assert get({"Authorization": stranger}) == 401
        assert client.post(url, headers=as_rekuest("POST", url)).status_code == 405
        settings.REKUEST_SERVICE = None
        assert get(as_rekuest("GET", url)) == 503


@pytest.mark.django_db(transaction=True)
class TestSignals:
    def test_emit_waits_for_the_commit_and_signs_for_the_service(self, intake):
        created = service.signal("@testsvc/room", kinds=["CREATED"], descriptors=["@testsvc/area"])
        with transaction.atomic():
            created.emit(5, organization="org", descriptors={"@testsvc/area": 12})
            assert intake.received == []

        (received,) = intake.wait()
        assert received["path"] == "/agi/signal/testsvc"
        assert received["issuer"] == "live.arkitekt.testsvc"
        body = received["json"]
        assert (body["kind"], body["identifier"], body["object"], body["organization"]) == ("CREATED", "@testsvc/room", "5", "org")
        assert body["descriptors"] == {"@testsvc/area": 12} and body["provenance"] is None

    def test_a_rolled_back_object_is_never_announced(self, intake):
        created = service.signal("@testsvc/room", kinds=["CREATED"])
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                created.emit(6, organization="org")
                raise RuntimeError("rollback")
        assert intake.wait(timeout=0.5) == []
