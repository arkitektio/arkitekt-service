"""``Service.structure``: what a service hosts, and the descriptors of each. Hosting announces nothing."""

import pytest
from django.conf import settings

from arkitekt_service.service import Descriptor, Service
from test_app.models import Book, Shelf

hosting = Service("hosting", key=settings.THINGS_KEY)
shelf = hosting.structure(
    Shelf,
    "@hosting/shelf",
    descriptors=[Descriptor("@hosting/n_books", "INT", "How many books the shelf holds")],
    describe=lambda shelf: {"@hosting/n_books": shelf.books.count()},
    description="A shelf of books.",
)
book = hosting.structure(Book, "@hosting/book", descriptors=["@hosting/on"], describe=lambda book: {"@hosting/on": book.shelf.name})


class TestDeclaration:
    def test_the_manifest_lists_the_structures(self):
        manifest = hosting.manifest()
        assert manifest["structures"] == [
            {
                "identifier": "@hosting/shelf",
                "label": "Shelf",
                "description": "A shelf of books.",
                "descriptors": [{"key": "@hosting/n_books", "type": "INT", "description": "How many books the shelf holds"}],
            },
            {"identifier": "@hosting/book", "label": "Book", "description": None, "descriptors": [{"key": "@hosting/on", "type": "ANY", "description": None}]},
        ]

    def test_hosting_declares_no_signal(self):
        assert hosting.manifest()["signals"] == [] and hosting.signals == {}

    def test_a_structure_is_found_by_identifier_model_and_instance(self):
        assert hosting.structure_for("@hosting/shelf") is shelf
        assert hosting.structure_for(Book) is book
        assert hosting.structure_for(Shelf(name="x")) is shelf
        assert hosting.structure_for("@hosting/nothing") is None

    def test_declaring_it_again_the_same_way_is_fine_and_differently_is_not(self):
        other = Service("other")
        first = other.structure(Shelf, "@other/thing", descriptors=["@other/a"])
        assert other.structure(Shelf, "@other/thing", descriptors=["@other/a"]) is first
        with pytest.raises(ValueError, match="declared twice"):
            other.structure(Shelf, "@other/thing", descriptors=["@other/b"])
        with pytest.raises(ValueError, match="already hosted"):
            other.structure(Shelf, "@other/item")

    def test_what_cannot_be_declared(self):
        other = Service("other")
        with pytest.raises(ValueError, match="@package/key"):
            other.structure(Shelf, "thing")
        with pytest.raises(ValueError, match="descriptor key twice"):
            other.structure(Shelf, "@other/thing", descriptors=["@other/a", "@other/a"])
        with pytest.raises(ValueError, match="descriptor type"):
            Descriptor("@other/a", "NUMBER")


@pytest.mark.django_db(transaction=True)
class TestDescribing:
    def test_objects_are_described_and_their_saves_are_not_announced(self, intake):
        made = Book.objects.create(shelf=Shelf.objects.create(name="host"))
        assert hosting.describe(made) == {"@hosting/on": "host"}
        assert hosting.describe(made.shelf) == {"@hosting/n_books": 1}
        assert intake.wait(1, timeout=0.5) == []

    def test_a_model_nobody_hosts_has_no_descriptors(self):
        assert Service("empty").describe(Shelf(name="x")) == {}
