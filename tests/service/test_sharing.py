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
from arkitekt_service.sharing import is_shared, kante_narrow, object_scope, require_shared
from test_app.models import Book, Loan, Part, Shelf

HUB = "3"


@dataclass(frozen=True)
class Grant:
    structure: str
    object_id: str
    reach: str = "self"
    sharer: str | None = None


@dataclass
class Token:
    """A stand-in for authentikate's token: scoped when it lists objects."""

    objects: dict[str, list[Grant]] | None = None
    sub: str = "guest"
    iss: str = "lok"
    org: str | None = "org"
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
        reaches=[Reach(key="derived", label="With its loans")],
        closures={"derived": {Loan: "shelf"}},
    )
    return service


@pytest.fixture
def shelves(db):
    first, second = Shelf.objects.create(name="first"), Shelf.objects.create(name="second")
    books = [Book.objects.create(shelf=first), Book.objects.create(shelf=second)]
    for shelf in (first, second):
        Loan.objects.create(shelf=shelf)
    Loan.objects.create(shelf=None)
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

    def test_what_is_part_of_the_object_comes_with_it(self, shelves):
        lending()
        first, _, books = shelves
        alone = scoped(Grant("@lending/shelf", str(first.pk)))
        # A book holds a required foreign key to its shelf: no closure needed.
        assert list(object_scope(Book.objects.all(), alone, hub=HUB)) == [books[0]]
        # A loan only links to one (nullable): not part of the shelf.
        assert list(object_scope(Loan.objects.all(), alone, hub=HUB)) == []

    def test_a_reach_adds_its_closure_and_keeps_self(self, shelves):
        lending()
        first, _, books = shelves
        derived = scoped(Grant("@lending/shelf", str(first.pk), "derived"))
        assert [loan.shelf for loan in object_scope(Loan.objects.all(), derived, hub=HUB)] == [first]
        assert list(object_scope(Book.objects.all(), derived, hub=HUB)) == [books[0]]
        assert list(object_scope(Shelf.objects.all(), derived, hub=HUB)) == [first]

    def test_an_unknown_reach_is_self(self, shelves):
        lending()
        first, _, _ = shelves
        token = scoped(Grant("@lending/shelf", str(first.pk), "everything"))
        assert list(object_scope(Shelf.objects.all(), token, hub=HUB)) == [first]
        assert list(object_scope(Loan.objects.all(), token, hub=HUB)) == []

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
        assert manifest["reaches"] == [{"key": "derived", "label": "With its loans", "description": None, "includes": [], "writes": False}]

    def test_binding_closures_makes_the_service_share(self):
        service = lending()
        assert Service.sharing == [service]
        plain = Service("plain")
        plain.structure(Shelf, "@plain/shelf")
        assert plain not in Service.sharing

    def test_a_closure_for_an_undeclared_reach_is_refused(self):
        with pytest.raises(ValueError, match="no reach derived"):
            Service("other").structure(Shelf, "@other/shelf", closures={"derived": {Loan: "shelf"}})

    def test_self_may_be_bound_without_declaring_it(self):
        service = Service("other")
        declared = service.structure(Shelf, "@other/shelf", closures={"self": {Loan: "shelf"}})
        assert declared.lookups(Loan, "self") == ["shelf"]
        assert declared.lookups(Book, "self") == ["shelf"]  # part of it: found, not bound
        assert declared.lookups(Shelf, "anything") == ["pk"]

    def test_the_least_reaches_that_show_a_model(self):
        service = Service("other")
        declared = service.structure(
            Shelf, "@other/shelf", reaches=[Reach(key="derived"), Reach(key="all", includes=["derived"])], closures={"derived": {Loan: "shelf"}}
        )
        assert declared.revealing(Shelf) == ["self"]
        assert declared.revealing(Book) == ["self"]
        assert declared.revealing(Loan) == ["derived"]  # `all` shows it too, through `derived`
        assert declared.revealing(Part) == []

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


class TestRequireShared:
    """A write a share permits: the object, at a reach that writes, by a sharer who may do it."""

    @pytest.fixture
    def annotating(self, monkeypatch):
        service = Service("lending", key=settings.THINGS_KEY)
        service.structure(
            Shelf,
            "@lending/shelf",
            reaches=[Reach(key="derived"), Reach(key="annotate", includes=["self"], writes=True), Reach(key="all", includes=["annotate"])],
            closures={"derived": {Loan: "shelf"}},
        )
        roles = {"8": ["editor"], "9": ["viewer"]}
        monkeypatch.setattr("arkitekt_service.sharing.sharer_roles", lambda token, sharer: roles.get(sharer))
        return service

    def _write(self, *grants: Grant, **kwargs):
        require_shared("@lending/shelf", 1, "annotate", token=scoped(*grants), hub=HUB, **kwargs)

    def test_a_share_at_a_covering_reach_permits_it(self, annotating):
        self._write(Grant("@lending/shelf", "1", "annotate", "8"), any_role_of=["editor"])
        self._write(Grant("@lending/shelf", "1", "all", "8"), any_role_of=["editor"])  # `all` covers `annotate`
        self._write(Grant("@lending/shelf", "1", "annotate"))  # no roles asked: no sharer needed

    def test_a_share_of_another_object_or_reach_does_not(self, annotating):
        from django.core.exceptions import PermissionDenied

        for grant in (Grant("@lending/shelf", "2", "annotate", "8"), Grant("@lending/shelf", "1", "self", "8"), Grant("@lending/shelf", "1", "derived", "8")):
            with pytest.raises(PermissionDenied):
                self._write(grant, any_role_of=["editor"])

    def test_the_sharer_must_hold_the_roles_here(self, annotating):
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            self._write(Grant("@lending/shelf", "1", "annotate", "9"), any_role_of=["editor"])  # a viewer shared it
        with pytest.raises(PermissionDenied):
            self._write(Grant("@lending/shelf", "1", "annotate", "7"), any_role_of=["editor"])  # never seen here
        with pytest.raises(PermissionDenied):
            self._write(Grant("@lending/shelf", "1", "annotate"), any_role_of=["editor"])  # no sharer said
        # One grant by a sharer who may is enough.
        self._write(Grant("@lending/shelf", "1", "annotate", "9"), Grant("@lending/shelf", "1", "annotate", "8"), roles=["editor"])

    def test_a_full_member_passes_and_a_reach_that_writes_nothing_is_a_mistake(self, annotating):
        require_shared("@lending/shelf", 1, "annotate", token=Token(), hub=HUB, any_role_of=["editor"])
        with pytest.raises(ValueError, match="permits writes"):
            require_shared("@lending/shelf", 1, "derived", token=scoped(), hub=HUB)
        with pytest.raises(ValueError, match="permits writes"):
            require_shared("@other/shelf", 1, "annotate", token=scoped(), hub=HUB)


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
