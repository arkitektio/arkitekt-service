"""A service with a contract declares what it hosts there, as data; its ``Service`` only binds it."""

import pytest
from django.conf import settings

from arkitekt_service.contract import Descriptor, Hosts, Signal, Structure
from arkitekt_service.service import Service, organization_of
from test_app.models import Book, Shelf

HOSTS = Hosts(
    structures=[
        Structure(
            identifier="@bound/shelf",
            label="Shelf",
            description="A shelf of books.",
            descriptors=[Descriptor(key="@bound/n_books", type="INT", description="How many books the shelf holds")],
        ),
        Structure(identifier="@bound/book"),
    ],
    signals=[Signal(identifier="@bound/shelf", kinds=["CREATED", "DELETED"], descriptors=["@bound/n_books"])],
)


def bound() -> Service:
    service = Service("bound", key=settings.THINGS_KEY, hosts=HOSTS)
    shelf = service.structure(Shelf, "@bound/shelf", describe=lambda shelf: {"@bound/n_books": shelf.books.count()})
    service.structure(Book, "@bound/book")
    service.model_signal(shelf, organization=organization_of())
    return service


def test_the_manifest_is_what_the_contract_says() -> None:
    manifest = bound().manifest()
    assert manifest["structures"] == [structure.model_dump() for structure in HOSTS.structures]
    assert manifest["signals"] == [signal.model_dump() for signal in HOSTS.signals]


def test_a_structure_the_contract_does_not_host_cannot_be_bound() -> None:
    service = Service("bound", hosts=HOSTS)
    with pytest.raises(ValueError, match="contract hosts no structure '@bound/magazine'"):
        service.structure(Shelf, "@bound/magazine")
    with pytest.raises(ValueError, match="contract announces no signal '@bound/book'"):
        service.signal("@bound/book")


def test_what_a_structure_carries_is_said_in_the_contract_and_nowhere_else() -> None:
    service = Service("bound", hosts=HOSTS)
    with pytest.raises(ValueError, match="said in bound's contract, once"):
        service.structure(Shelf, "@bound/shelf", descriptors=["@bound/other"])
    with pytest.raises(ValueError, match="said in bound's contract, once"):
        service.signal("@bound/shelf", kinds=["UPDATED"])


def test_a_service_does_not_start_on_a_promise_its_code_does_not_keep() -> None:
    service = Service("bound", hosts=HOSTS)
    service.structure(Shelf, "@bound/shelf")
    assert service.unbound() == ["structure @bound/book", "signal @bound/shelf"]
    with pytest.raises(RuntimeError, match="binds none of it"):
        _ = service.urls
    assert bound().unbound() == []


def test_a_signal_for_every_save_carries_its_structures_descriptors() -> None:
    hosts = Hosts(
        structures=[Structure(identifier="@bound/shelf", descriptors=[Descriptor(key="@bound/n_books")])],
        signals=[Signal(identifier="@bound/shelf")],
    )
    service = Service("bound", hosts=hosts)
    shelf = service.structure(Shelf, "@bound/shelf")
    with pytest.raises(ValueError, match="leaves out @bound/n_books"):
        service.model_signal(shelf, organization=organization_of())


def test_what_is_hosted_is_said_once() -> None:
    with pytest.raises(ValueError, match="structure @bound/shelf is declared twice"):
        Hosts(structures=[Structure(identifier="@bound/shelf"), Structure(identifier="@bound/shelf")])
    with pytest.raises(ValueError, match="looks like @package/key"):
        Structure(identifier="shelf")
    with pytest.raises(ValueError, match="descriptor key is declared twice"):
        Structure(identifier="@bound/shelf", descriptors=[Descriptor(key="a"), Descriptor(key="a")])
