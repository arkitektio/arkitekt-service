"""Sharing: a scoped member's reads narrowed to the objects shared with them, by their reaches."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from django.conf import settings
from django.core.checks import Error, Warning

from arkitekt_service import trust
from arkitekt_service.contract.description import Hosts, Reach, Structure
from arkitekt_service.server.checks import check_sharing
from arkitekt_service.service import Service
from arkitekt_service.sharing import is_shared, kante_narrow, object_scope
from test_app.models import Book, Part, Shelf

HUB = "3"


@dataclass(frozen=True)
class Grant:
    structure: str
    object_id: str
    reach: str = "self"


@dataclass
class Token:
    """A stand-in for authentikate's token: scoped when it lists objects."""

    objects: dict[str, list[Grant]] | None = None
    sub: str = "guest"
    _: Any = field(default=None, repr=False)

    @property
    def is_scoped(self) -> bool:
        return self.objects is not None

    def grants_for(self, hub: str) -> tuple[Grant, ...]:
        return tuple((self.objects or {}).get(hub, ()))


def scoped(*grants: Grant, hub: str = HUB) -> Token:
    return Token({hub: list(grants)})


@pytest.fixture(autouse=True)
def sharing_services():
    """Each test's sharing services, and only those, narrow reads."""
    before = list(Service.sharing)
    Service.sharing.clear()
    yield
    Service.sharing[:] = before


def lending() -> Service:
    service = Service("lending", key=settings.THINGS_KEY)
    service.structure(
        Shelf,
        "@lending/shelf",
        reaches=[Reach(key="derived", label="With its books")],
        closures={"derived": {Book: "shelf_id"}},
    )
    return service


@pytest.fixture
def shelves(db):
    first, second = Shelf.objects.create(name="first"), Shelf.objects.create(name="second")
    books = [Book.objects.create(shelf=first), Book.objects.create(shelf=second)]
    return first, second, books


class TestObjectScope:
    def test_a_full_member_reads_the_queryset_unchanged(self, shelves):
        lending()
        queryset = Shelf.objects.all()
        assert object_scope(queryset, Token(), hub=HUB) is queryset
        assert object_scope(queryset, None) is queryset  # outside a request, no token

    def test_the_shared_object_and_nothing_else(self, shelves):
        lending()
        first, _, _ = shelves
        token = scoped(Grant("@lending/shelf", str(first.pk)))
        assert list(object_scope(Shelf.objects.all(), token, hub=HUB)) == [first]

    def test_a_model_no_reach_covers_shows_nothing(self, shelves):
        lending()
        first, _, _ = shelves
        token = scoped(Grant("@lending/shelf", str(first.pk), "derived"))
        assert list(object_scope(Part.objects.all(), token, hub=HUB)) == []

    def test_a_reach_adds_its_closure_and_keeps_self(self, shelves):
        lending()
        first, _, books = shelves
        derived = scoped(Grant("@lending/shelf", str(first.pk), "derived"))
        assert list(object_scope(Book.objects.all(), derived, hub=HUB)) == [books[0]]
        assert list(object_scope(Shelf.objects.all(), derived, hub=HUB)) == [first]
        alone = scoped(Grant("@lending/shelf", str(first.pk)))
        assert list(object_scope(Book.objects.all(), alone, hub=HUB)) == []

    def test_an_unknown_reach_is_self(self, shelves):
        lending()
        first, _, _ = shelves
        token = scoped(Grant("@lending/shelf", str(first.pk), "everything"))
        assert list(object_scope(Shelf.objects.all(), token, hub=HUB)) == [first]
        assert list(object_scope(Book.objects.all(), token, hub=HUB)) == []

    def test_another_hub_and_another_services_structure_share_nothing(self, shelves):
        lending()
        first, _, _ = shelves
        elsewhere = scoped(Grant("@lending/shelf", str(first.pk)), hub="9")
        assert list(object_scope(Shelf.objects.all(), elsewhere, hub=HUB)) == []
        foreign = scoped(Grant("@mikro/arraydataset", str(first.pk)))
        assert list(object_scope(Shelf.objects.all(), foreign, hub=HUB)) == []

    def test_an_id_the_key_cannot_hold_shares_nothing(self, shelves):
        lending()
        token = scoped(Grant("@lending/shelf", "not-a-number"))
        assert list(object_scope(Shelf.objects.all(), token, hub=HUB)) == []

    def test_without_a_hub_a_scoped_member_reads_nothing(self, shelves, monkeypatch):
        lending()
        first, _, _ = shelves
        monkeypatch.setattr("arkitekt_service.sharing.current_hub", lambda: None)
        assert list(object_scope(Shelf.objects.all(), scoped(Grant("@lending/shelf", str(first.pk))))) == []

    def test_empty_grants_share_nothing(self, shelves):
        lending()
        assert list(object_scope(Shelf.objects.all(), Token({}), hub=HUB)) == []

    def test_is_shared(self, shelves):
        lending()
        token = scoped(Grant("@lending/shelf", "7", "derived"))
        assert is_shared("@lending/shelf", 7, token, hub=HUB)
        assert not is_shared("@lending/shelf", 8, token, hub=HUB)
        assert is_shared("@lending/shelf", 8, Token(), hub=HUB)

    def test_kante_narrow_reads_the_requests_token(self, shelves, monkeypatch):
        lending()
        first, _, _ = shelves
        token = scoped(Grant("@lending/shelf", str(first.pk)))
        monkeypatch.setattr("arkitekt_service.sharing.current_hub", lambda: HUB)

        class Request:
            def get_extension(self, name):
                return {"token": token}[name]

        class Info:
            class context:
                request = Request()

        assert list(kante_narrow(Shelf.objects.all(), Info())) == [first]


class TestDeclaration:
    def test_the_manifest_lists_declared_reaches(self):
        manifest = lending().manifest()["structures"][0]
        assert manifest["reaches"] == [{"key": "derived", "label": "With its books", "description": None, "includes": []}]

    def test_binding_closures_makes_the_service_share(self):
        service = lending()
        assert Service.sharing == [service]
        plain = Service("plain")
        plain.structure(Shelf, "@plain/shelf")
        assert plain not in Service.sharing

    def test_a_closure_for_an_undeclared_reach_is_refused(self):
        with pytest.raises(ValueError, match="no reach derived"):
            Service("other").structure(Shelf, "@other/shelf", closures={"derived": {Book: "shelf_id"}})

    def test_self_may_be_bound_without_declaring_it(self):
        service = Service("other")
        declared = service.structure(Shelf, "@other/shelf", closures={"self": {Book: "shelf_id"}})
        assert declared.lookups(Book, "self") == ["shelf_id"]
        assert declared.lookups(Shelf, "anything") == ["pk"]

    @pytest.mark.parametrize(
        ("reaches", "match"),
        [
            ([Reach(key="a"), Reach(key="a")], "declared twice"),
            ([Reach(key="a", includes=["b"])], "does not declare"),
            ([Reach(key="a", includes=["b"]), Reach(key="b", includes=["a"])], "includes itself"),
            ([Reach(key="self", includes=["a"]), Reach(key="a")], "includes no other"),
        ],
    )
    def test_what_reaches_cannot_say(self, reaches, match):
        with pytest.raises(ValueError, match=match):
            Structure(identifier="@x/y", reaches=reaches)
        with pytest.raises(ValueError, match=match):
            Service("other").structure(Shelf, "@other/shelf", reaches=[Reach(key=r.key, includes=r.includes) for r in reaches])  # type: ignore[misc]

    def test_a_reach_key_is_lowercase(self):
        with pytest.raises(ValueError):
            Reach(key="Derived")

    def test_with_a_contract_the_reaches_are_said_there(self):
        hosts = Hosts(structures=[Structure(identifier="@said/shelf", reaches=[Reach(key="derived", includes=["self"])])])
        service = Service("said", hosts=hosts)
        with pytest.raises(ValueError, match="said in said's contract"):
            service.structure(Shelf, "@said/shelf", reaches=["derived"])
        declared = service.structure(Shelf, "@said/shelf", closures={"derived": {Book: "shelf_id"}})
        assert declared.covered("derived") == {"self", "derived"}
        assert service.manifest()["structures"][0]["reaches"] == [r.model_dump() for r in hosts.structures[0].reaches]


class TestChecks:
    def test_a_lookup_that_does_not_resolve_is_an_error(self, settings):
        settings.AUTHENTIKATE = {"HUB": HUB}
        service = Service("broken")
        service.structure(Shelf, "@broken/shelf", closures={"self": {Book: "shelf__nothing"}})
        found = check_sharing(None)
        assert [m.id for m in found if isinstance(m, Error)] == ["arkitekt.E010"]

    def test_a_structure_the_coordinator_cannot_grant_is_a_warning(self, monkeypatch):
        monkeypatch.setattr("arkitekt_service.sharing.current_hub", lambda: HUB)
        service = Service("loud")
        service.structure(Shelf, "@Loud/Shelf", closures={"self": {Book: "shelf_id"}})
        assert [m.id for m in check_sharing(None) if isinstance(m, Warning)] == ["arkitekt.W010"]

    def test_sharing_without_a_hub_is_a_warning(self, monkeypatch):
        monkeypatch.setattr("arkitekt_service.sharing.current_hub", lambda: None)
        lending()
        assert [m.id for m in check_sharing(None)] == ["arkitekt.W011"]

    def test_nothing_to_say_without_sharing(self):
        assert check_sharing(None) == []


class TestHub:
    @pytest.fixture(autouse=True)
    def nothing_learned(self):
        trust._learned.clear()
        yield
        trust._learned.clear()

    def test_the_hub_is_read_from_this_instances_key(self, settings):
        own = {**trust.public_jwk(settings.SERVICE_KEY), "service": "live.arkitekt.testsvc", "hub": 3}
        settings.INSTANCE = {**settings.INSTANCE, "TRUST_JWKS": {"keys": [own]}}
        assert trust.known_hub() == "3"
        assert trust.hub() == "3"

    def test_no_hub_in_the_bundle_is_none(self):
        assert trust.known_hub() is None

    def test_a_fetch_under_way_is_not_waited_for(self, settings):
        own = {**trust.public_jwk(settings.SERVICE_KEY), "service": "live.arkitekt.testsvc", "hub": 3}
        settings.INSTANCE = {**settings.INSTANCE, "TRUST_JWKS": {"keys": [own]}}
        with trust.trust_bundle._lock:  # as while `hub()` fetches
            assert trust.known_hub() is None
        assert trust.known_hub() == "3"
        with trust.trust_bundle._lock:  # learned: the bundle is not asked again
            assert trust.known_hub() == "3"
