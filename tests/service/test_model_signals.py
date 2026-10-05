"""``Service.model_signal``: saves and deletes of a hosted structure's model become signals, with no emit in the code."""

import pytest
from django.conf import settings
from django.db import transaction

from arkitekt_service.service import Service
from test_app.models import Part, Thing

things = Service("things", key=settings.THINGS_KEY)
# Hosting first ...
thing = things.structure(
    Thing,
    "@things/thing",
    descriptors=("@things/n_parts",),
    # Read at commit: counts the parts written after the thing itself.
    describe=lambda thing: {"@things/n_parts": thing.parts.count()},
)
# ... then, separately, announcing.
thing_signal = things.model_signal(
    thing,
    organization=lambda thing: thing.organization,
    when=lambda thing, kind: not thing.secret,
    descriptors=("@things/renamed",),
    description="A thing happened.",
)


def _bodies(intake, count, timeout=5):
    return [r["json"] for r in intake.wait(count, timeout=timeout)]


@pytest.mark.django_db(transaction=True)
class TestModelSignals:
    def test_create_update_delete_are_signalled_after_commit(self, intake):
        with transaction.atomic():
            thing = Thing.objects.create(name="a")
            assert intake.received == []  # nothing before the commit
        thing.name = "b"
        thing.save()
        pk = thing.pk
        thing.delete()

        bodies = _bodies(intake, 3)
        assert [b["kind"] for b in bodies] == ["CREATED", "UPDATED", "DELETED"]
        assert all(b["identifier"] == "@things/thing" and b["object"] == str(pk) and b["organization"] == "org" for b in bodies)
        assert all(r["path"] == "/agi/signal/things" and r["issuer"] == "live.arkitekt.things" for r in intake.received)

    def test_the_signal_carries_what_the_structure_describes(self, intake):
        with transaction.atomic():
            made = Thing.objects.create(name="with parts")
            Part.objects.create(thing=made)
            Part.objects.create(thing=made)
        (body,) = _bodies(intake, 1)
        # Read at commit, so it sees the parts written after the thing.
        assert body["descriptors"] == things.describe(made) == {"@things/n_parts": 2}

    def test_a_rolled_back_save_sends_nothing(self, intake):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                Thing.objects.create(name="never")
                raise RuntimeError
        assert _bodies(intake, 1, timeout=0.5) == []

    def test_when_can_veto(self, intake):
        Thing.objects.create(name="hidden", secret=True)
        assert _bodies(intake, 1, timeout=0.5) == []

    def test_a_failing_description_never_breaks_the_save(self, intake):
        broken = Service("broken", key=settings.BROKEN_KEY)
        broken.model_signal(broken.structure(Part, "@things/part"), organization=lambda part: 1 / 0)
        Part.objects.create(thing=Thing.objects.create(name="host", secret=True))  # the save succeeds
        assert _bodies(intake, 1, timeout=0.5) == []

    def test_fixture_loading_is_not_an_event(self, intake):
        from django.db.models.signals import post_save

        thing = Thing(name="loaded", pk=4242)
        thing.save_base(raw=True)
        post_save.send(sender=Thing, instance=thing, created=True, raw=True)
        assert _bodies(intake, 1, timeout=0.5) == []


class TestDeclaration:
    def test_the_signal_declares_the_structures_keys_and_its_own(self):
        (declared,) = things.manifest()["signals"]
        assert declared == {
            "identifier": "@things/thing",
            "kinds": ["CREATED", "UPDATED", "DELETED"],
            "descriptors": ["@things/n_parts", "@things/renamed"],
            "description": "A thing happened.",
        }
        assert thing_signal.identifier == thing.identifier

    def test_the_model_may_stand_for_its_structure(self):
        local = Service("local")
        hosted = local.structure(Part, "@local/part")
        assert local.model_signal(Part, organization=lambda part: "org", kinds=["CREATED"]).identifier == hosted.identifier

    def test_only_a_hosted_structure_can_be_signalled(self):
        local = Service("local")
        with pytest.raises(ValueError, match="not a structure this service hosts"):
            local.model_signal(Part, organization=lambda part: "org")
        with pytest.raises(ValueError, match="not a structure this service hosts"):
            local.model_signal(thing, organization=lambda part: "org")  # another service's


def test_organization_of_follows_a_path_to_the_slug():
    from types import SimpleNamespace

    from arkitekt_service.service import organization_of

    message = SimpleNamespace(room=SimpleNamespace(organization=SimpleNamespace(slug="lab")))
    assert organization_of("room.organization")(message) == "lab"
    assert organization_of("room.organization")(SimpleNamespace(room=None)) is None
