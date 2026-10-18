"""Collaborations: what a guest is let in to, held to at the root, below it, and when they write."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

strawberry = pytest.importorskip("strawberry")
strawberry_django = pytest.importorskip("strawberry_django")
pytest.importorskip("authentikate.models")

from authentikate.errors import ShareInsufficient  # noqa: E402
from authentikate.models import Organization  # noqa: E402
from strawberry.extensions import SchemaExtension  # noqa: E402
from strawberry_django.optimizer import DjangoOptimizerExtension  # noqa: E402

from arkitekt_service.graphql import COLLABORATION_DOES_NOT_REACH, CollaborationGate, annotate_schema  # noqa: E402
from arkitekt_service.service import Service  # noqa: E402
from arkitekt_service.collaborations import items  # noqa: E402
from arkitekt_service.collaborations.guests import Guest, guest_of, object_scope, require_in_collaboration, wholes  # noqa: E402
from arkitekt_service.collaborations.models import Collaboration, CollaborationItem  # noqa: E402
from test_app.models import Book, Label, Loan, Shelf  # noqa: E402

INSTANCE = "12"


@dataclass
class Token:
    """A stand-in for authentikate's token: a guest's when it lists collaborations."""

    collabs: dict[str, dict[str, list[str]]] | None = None
    sub: str = "guest"
    _: Any = field(default=None, repr=False)

    @property
    def is_scoped(self) -> bool:
        return self.collabs is not None

    def collabs_here(self, instance: str | None) -> dict[str, tuple[str, ...]]:
        return {collaboration: tuple(roles) for collaboration, roles in (self.collabs or {}).get(str(instance), {}).items()}


def granted(**collaborations: list[str]) -> Token:
    """A guest's token granting ``{collaboration id: roles}`` on this instance (``granted(**{"5": ["editor"]})``)."""
    return Token({INSTANCE: dict(collaborations)})


@pytest.fixture(autouse=True)
def lending():
    """The lending service, sharing: a shelf brings its loans along."""
    before = list(Service.sharing)
    Service.sharing.clear()
    service = Service("lending")
    service.structure(Shelf, "@lending/shelf", depends=lambda shelf: list(shelf.loans.all()))
    service.structure(Book, "@lending/book")
    service.structure(Loan, "@lending/loan")
    service.share()
    yield service
    Service.sharing[:] = before


@pytest.fixture
def library(db):
    organization = Organization.objects.create(slug="org")
    first, second = Shelf.objects.create(name="first"), Shelf.objects.create(name="second")
    books = [Book.objects.create(shelf=first), Book.objects.create(shelf=second)]
    loans = [Loan.objects.create(shelf=first), Loan.objects.create(shelf=second)]
    labels = [Label.objects.create(shelf=first), Label.objects.create(shelf=second)]
    collaboration = Collaboration.objects.create(organization=organization, name="Reading room")
    return SimpleNamespace(organization=organization, shelves=(first, second), books=books, loans=loans, labels=labels, collaboration=collaboration)


@pytest.fixture
def in_organization(library):
    """Work in the library's organization, as a request authenticated there does."""
    from authentikate.vars import organization_var

    reset = organization_var.set(library.organization)
    yield
    organization_var.reset(reset)


def guest(library, token: Token) -> Guest:
    return Guest(token, library.organization)


class TestWhatIsInASpace:
    def test_an_object_brings_what_it_needs(self, library):
        first = library.shelves[0]
        item = items.add(library.collaboration, first)
        loan = CollaborationItem.objects.get(object_id=str(library.loans[0].pk), content_type__model="loan")
        assert loan.because_of == item
        assert library.collaboration.items.count() == 2

    def test_its_parts_are_in_it_live_and_its_references_are_not(self, library):
        items.add(library.collaboration, library.shelves[0])
        late = Book.objects.create(shelf=library.shelves[0])
        seen = guest(library, granted(**{str(library.collaboration.pk): []}))
        assert seen.sees(library.books[0]) and seen.sees(late)
        assert not seen.sees(library.books[1])
        assert not seen.sees(library.labels[0])  # PROTECT: it points at the shelf, it is not of it
        assert seen.sees(library.loans[0]) and not seen.sees(library.loans[1])  # brought along

    def test_taking_out_takes_what_it_brought_unless_another_needs_it(self, library):
        first = library.shelves[0]
        items.add(library.collaboration, first)
        items.add(library.collaboration, library.loans[0])  # also by hand
        items.remove(library.collaboration, first)
        assert list(library.collaboration.items.values_list("object_id", flat=True)) == [str(library.loans[0].pk)]
        items.add(library.collaboration, first)
        items.remove(library.collaboration, library.loans[0])
        assert library.collaboration.items.filter(object_id=str(library.loans[0].pk), content_type__model="loan").exists()  # the shelf still needs it

    def test_refresh_and_include(self, library):
        first = library.shelves[0]
        items.add(library.collaboration, first)
        newer = Loan.objects.create(shelf=first)
        items.refresh(first)
        assert library.collaboration.items.filter(object_id=str(newer.pk), content_type__model="loan").exists()
        minted = Loan.objects.create(shelf=None)
        items.include(minted, alongside=first)
        assert library.collaboration.items.get(object_id=str(minted.pk), content_type__model="loan").because_of.object_id == str(first.pk)

    def test_a_reference_can_be_said_not_to_make_a_part(self, lending, library):
        from arkitekt_service.collaborations import guests

        lending.not_part_of(Book, "shelf")
        try:
            assert wholes(Book) == ()
            items.add(library.collaboration, library.shelves[0])
            assert not guest(library, granted(**{str(library.collaboration.pk): []})).sees(library.books[0])
        finally:
            guests._references.clear()
            wholes.cache_clear()

    def test_what_a_row_is_part_of(self):
        assert wholes(Book) == ((Shelf, "shelf"),)
        assert wholes(Loan) == ()  # nullable
        assert wholes(Label) == ()  # PROTECT


class TestAGuest:
    def test_only_collaborations_of_the_organization_hold_anything(self, library):
        elsewhere = Collaboration.objects.create(organization=Organization.objects.create(slug="other"), name="Elsewhere")
        items.add(elsewhere, library.shelves[1])
        seen = guest(library, granted(**{str(elsewhere.pk): ["editor"]}))
        assert not seen.sees(library.shelves[1])

    def test_roles_hold_only_inside_their_collaboration(self, library):
        other = Collaboration.objects.create(organization=library.organization, name="Viewing room")
        items.add(library.collaboration, library.shelves[0])
        items.add(other, library.shelves[1])
        seen = guest(library, granted(**{str(library.collaboration.pk): ["editor"], str(other.pk): []}))
        assert seen.holds(library.books[0], any_role_of=["editor", "admin"])
        assert not seen.holds(library.books[1], any_role_of=["editor"])
        assert seen.holds(library.books[1])  # view

    def test_require_in_collaboration(self, library, in_organization):
        items.add(library.collaboration, library.shelves[0])
        token = granted(**{str(library.collaboration.pk): ["editor"]})
        require_in_collaboration(library.books[0], any_role_of=["editor"], token=token)
        with pytest.raises(ShareInsufficient):
            require_in_collaboration(library.books[1], any_role_of=["editor"], token=token)
        with pytest.raises(ShareInsufficient):
            require_in_collaboration(library.books[0], roles=["admin"], token=token)
        require_in_collaboration(library.books[1], roles=["admin"], token=Token())  # a full member was checked already

    def test_root_narrowing_reads_nothing_until_used(self, library, in_organization, django_assert_num_queries):
        items.add(library.collaboration, library.shelves[0])
        token = granted(**{str(library.collaboration.pk): []})
        with django_assert_num_queries(0):
            narrowed = object_scope(Book.objects.all(), token)
        assert list(narrowed) == [library.books[0]]
        assert object_scope(Book.objects.all(), Token()).count() == 2  # a full member's, as it is

    def test_an_operation_keeps_its_guest(self, library, in_organization):
        from arkitekt_service.collaborations.guests import serve, served

        token = granted()
        assert guest_of(token=token) is not guest_of(token=token)  # outside an operation: read afresh
        reset = serve(token)
        try:
            assert guest_of(token=token) is guest_of(token=token)
            assert guest_of(token=Token()) is None
        finally:
            served(reset)
        assert serve(Token()) is None  # a full member: no guest to keep


# --- over GraphQL ------------------------------------------------------------------------------


@strawberry_django.type(Book)
class BookType:
    id: strawberry.auto

    @classmethod
    def get_queryset(cls, queryset: Any, info: Any, **kwargs: Any) -> Any:
        return object_scope(queryset, info=info)


@strawberry_django.type(Label)
class LabelType:
    id: strawberry.auto

    @classmethod
    def get_queryset(cls, queryset: Any, info: Any, **kwargs: Any) -> Any:
        return object_scope(queryset, info=info)


@strawberry_django.type(Shelf)
class ShelfType:
    id: strawberry.auto
    name: strawberry.auto
    books: list[BookType]
    labels: list[LabelType]

    @strawberry_django.field
    def any_label(self) -> LabelType | None:
        return Label.objects.filter(shelf=self).first()  # a resolver of its own: held all the same

    @classmethod
    def get_queryset(cls, queryset: Any, info: Any, **kwargs: Any) -> Any:
        return object_scope(queryset, info=info)


@strawberry.type
class Count:
    total: int


@strawberry.type
class Query:
    shelves: list[ShelfType] = strawberry_django.field()
    labels: list[LabelType] = strawberry_django.field()

    @strawberry.field
    def tally(self) -> Count:
        return Count(total=Book.objects.count())  # a value computed from every row


class Authenticated(SchemaExtension):
    """Stands in for authentikate's extension: the request carries the test's token and organization."""

    token: Any = None

    def on_operation(self):
        from authentikate.vars import organization_var, token_var

        reset_token, reset_org = token_var.set(Authenticated.token), organization_var.set(Organization.objects.first())
        yield
        token_var.reset(reset_token)
        organization_var.reset(reset_org)


def schema(*extensions: Any) -> Any:
    built = strawberry.Schema(query=Query, extensions=[Authenticated, *extensions, CollaborationGate])
    return built


@pytest.mark.django_db
@pytest.mark.parametrize("optimizer", [True, False], ids=["optimized", "plain"])
def test_a_guest_is_handed_only_what_is_in_their_collaborations(library, lending, optimizer):
    items.add(library.collaboration, library.shelves[0])
    Authenticated.token = granted(**{str(library.collaboration.pk): []})
    built = schema(*([DjangoOptimizerExtension()] if optimizer else []))
    first, mine = library.shelves[0], library.books[0]

    result = built.execute_sync("{ shelves { id books { id } labels { id } anyLabel { id } } }")
    assert result.errors is None, result.errors
    assert result.data == {"shelves": [{"id": str(first.pk), "books": [{"id": str(mine.pk)}], "labels": [], "anyLabel": None}]}
    assert built.execute_sync("{ labels { id } }").data == {"labels": []}

    Authenticated.token = Token()  # a full member: as it is
    full = built.execute_sync("{ shelves { id labels { id } } }")
    assert len(full.data["shelves"]) == 2 and all(shelf["labels"] for shelf in full.data["shelves"])


@pytest.mark.django_db
def test_a_root_field_handing_out_no_objects_is_closed_to_a_guest(library):
    Authenticated.token = granted(**{str(library.collaboration.pk): []})
    result = schema().execute_sync("{ tally { total } }")
    (error,) = result.errors
    assert error.extensions["reason"] == COLLABORATION_DOES_NOT_REACH and result.data is None
    Authenticated.token = Token()
    assert schema().execute_sync("{ tally { total } }").data == {"tally": {"total": 2}}


def test_the_schema_says_which_structure_a_type_is(lending):
    built = annotate_schema(strawberry.Schema(query=Query), lending)
    sdl = str(built)
    assert 'type ShelfType @identified(by: "@lending/shelf") {' in sdl
    assert 'type BookType @identified(by: "@lending/book") {' in sdl
    assert "type LabelType {" in sdl


def test_behind_the_gate_the_service_shares():
    Service.sharing.clear()
    plain = Service("plain")
    annotate_schema(strawberry.Schema(query=Query), plain)
    assert Service.sharing == []
    annotate_schema(strawberry.Schema(query=Query, extensions=[CollaborationGate]), plain)
    assert Service.sharing == [plain]


def test_guests_let_in_without_an_instance_is_a_warning(monkeypatch):
    from arkitekt_service.server.checks import check_guests

    monkeypatch.setattr("arkitekt_service.collaborations.guests.current_instance", lambda: None)
    assert [m.id for m in check_guests(None)] == ["arkitekt.W011"]
    monkeypatch.setattr("arkitekt_service.collaborations.guests.current_instance", lambda: "12")
    assert check_guests(None) == []


def test_a_guest_reads_again_once_a_collaboration_changed(library, in_organization):
    seen = guest(library, granted(**{str(library.collaboration.pk): []}))
    assert not seen.sees(library.shelves[0])
    items.add(library.collaboration, library.shelves[0])
    assert seen.sees(library.shelves[0]) and seen.sees(library.books[0])
