"""A described row keeps its descriptors: stored on every save, restored by whoever writes what a save cannot see."""

from dataclasses import dataclass

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import transaction

from arkitekt_service.service import Service
from test_app.models import Barrel, Bottle, Crate

crates = Service("crates", key=settings.THINGS_KEY)


def _describe(crate: Crate) -> dict:
    if crate.name == "unreadable":
        raise RuntimeError("cannot read this crate")
    return {"@crates/status": crate.status, "@crates/n_bottles": crate.bottles.count()}


crate = crates.structure(Crate, "@crates/crate", descriptors=["@crates/status", "@crates/n_bottles"], describe=_describe)


def _stored(made: Crate) -> dict:
    return Crate.objects.values_list("stored_descriptors", flat=True).get(pk=made.pk)


@pytest.mark.django_db
class TestStoring:
    def test_a_save_stores_what_the_row_says(self):
        made = Crate.objects.create(name="a")
        assert _stored(made) == crates.describe(made) == {"@crates/status": "OPEN", "@crates/n_bottles": 0}

    def test_a_save_of_some_fields_stores_them_too(self):
        made = Crate.objects.create(name="a")
        made.status = "SEALED"
        made.save(update_fields=["status"])
        assert _stored(made)["@crates/status"] == "SEALED"

    def test_rows_written_later_are_restored_by_their_writer(self):
        made = Crate.objects.create(name="a")
        Bottle.objects.create(crate=made)
        Bottle.objects.create(crate=made)
        assert _stored(made)["@crates/n_bottles"] == 0
        assert crates.restore(made) == 1
        assert _stored(made)["@crates/n_bottles"] == 2 and made.stored_descriptors["@crates/n_bottles"] == 2
        assert crates.restore(made) == 0

    def test_a_queryset_is_restored_row_by_row(self):
        first, second = Crate.objects.create(name="a"), Crate.objects.create(name="b")
        Bottle.objects.create(crate=second)
        Crate.objects.update(status="SEALED")  # a bulk write: no pre_save
        assert crates.restore(Crate.objects.order_by("pk")) == 2
        assert [_stored(first), _stored(second)] == [{"@crates/status": "SEALED", "@crates/n_bottles": 0}, {"@crates/status": "SEALED", "@crates/n_bottles": 1}]

    def test_bulk_written_rows_are_stamped_first(self):
        rows = [Crate.objects.create(name="a"), Crate.objects.create(name="b")]
        rows[0].status = "SEALED"
        Crate.objects.bulk_update(crates.stamp(rows), ["status", "stored_descriptors"])
        assert [_stored(row)["@crates/status"] for row in rows] == ["SEALED", "OPEN"]

    def test_a_describer_that_raises_keeps_what_the_row_had_and_the_save_goes_through(self):
        made = Crate.objects.create(name="a")
        made.name, made.status = "unreadable", "SEALED"
        made.save()
        assert Crate.objects.get(pk=made.pk).status == "SEALED"
        assert _stored(made) == {"@crates/status": "OPEN", "@crates/n_bottles": 0}


@pytest.mark.django_db
class TestTheSetupJob:
    @pytest.fixture(autouse=True)
    def mounted(self, monkeypatch):
        monkeypatch.setattr(Service, "mounted", [crates])

    def test_it_stores_what_a_row_owes_and_leaves_the_rest(self):
        owed, kept = Crate.objects.create(name="owed"), Crate.objects.create(name="kept")
        Bottle.objects.create(crate=kept)
        Crate.objects.filter(pk=owed.pk).update(stored_descriptors={})
        call_command("restore_descriptors")
        assert _stored(owed) == {"@crates/status": "OPEN", "@crates/n_bottles": 0}
        assert _stored(kept)["@crates/n_bottles"] == 0  # stale, but owes no key

    def test_all_describes_every_row_again(self):
        kept = Crate.objects.create(name="kept")
        Bottle.objects.create(crate=kept)
        call_command("restore_descriptors", "--all")
        assert _stored(kept)["@crates/n_bottles"] == 1


@dataclass
class Asked:
    key: str
    operator: str
    value: object = None


def test_only_what_a_structure_declares_can_be_required():
    assert crates.requires(Crate, [Asked("@crates/status", "EQUALS", "OPEN")]) is not None
    with pytest.raises(ValueError, match="not a descriptor here"):
        crates.requires(Crate, [Asked("@crates/colour", "EQUALS", "red")])
    with pytest.raises(ValueError, match="not a descriptor operator"):
        crates.requires(Crate, [Asked("@crates/status", "LIKE", "O%")])
    with pytest.raises(ValueError, match="no structure of crates"):
        crates.requires(Bottle, [])


barrels = Service("barrels", key=settings.THINGS_KEY)
barrels.model_signal(
    barrels.structure(Barrel, "@barrels/barrel", descriptors=["@barrels/name"], describe=lambda barrel: {"@barrels/name": barrel.name}), organization=lambda barrel: barrel.organization
)


@pytest.mark.django_db(transaction=True)
def test_a_row_restored_on_its_way_out_is_not_announced_as_updated(intake):
    made = Barrel.objects.create(name="doomed")
    with transaction.atomic():
        Barrel.objects.filter(pk=made.pk).update(name="renamed")
        barrels.restore(Barrel.objects.filter(pk=made.pk))  # an UPDATED queued for the commit ...
        made.delete()  # ... of a row that is gone by then
    assert [r["json"]["kind"] for r in intake.wait(3, timeout=1)] == ["CREATED", "DELETED"]
