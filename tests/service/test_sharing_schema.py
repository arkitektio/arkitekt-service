"""`annotate_schema`: the SDL says which share reveals each type, and which reach each list field needs."""

import pytest

strawberry = pytest.importorskip("strawberry")
strawberry_django = pytest.importorskip("strawberry_django")

from strawberry.schema_directive import Location  # noqa: E402

from arkitekt_service.contract.description import Reach  # noqa: E402
from arkitekt_service.graphql import annotate_schema  # noqa: E402
from arkitekt_service.service import Service  # noqa: E402
from test_app.models import Book, Loan, Part, Shelf  # noqa: E402


@strawberry.schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION], name="shared")
class Shared:
    by: list[str]


@strawberry_django.type(Book)
class BookType:
    id: strawberry.auto


@strawberry_django.type(Loan)
class LoanType:
    id: strawberry.auto


@strawberry_django.type(Part)
class PartType:
    id: strawberry.auto


@strawberry_django.type(Shelf)
class ShelfType:
    id: strawberry.auto
    name: strawberry.auto
    books: list[BookType]
    loans: list[LoanType]

    @strawberry_django.field
    def parts(self) -> list[PartType]:
        return []

    @strawberry_django.field
    def first_book(self) -> BookType | None:
        return None


@strawberry.type
class Query:
    shelves: list[ShelfType] = strawberry_django.field()
    all_parts: list[PartType] = strawberry_django.field()


def _sdl() -> str:
    service = Service("lending")
    declared = service.structure(Shelf, "@lending/shelf", reaches=[Reach(key="derived")], closures={"derived": {Loan: "shelf"}})
    return str(annotate_schema(strawberry.Schema(query=Query), [declared], directive=Shared))


def _line(sdl: str, start: str) -> str:
    (line,) = [line.strip() for line in sdl.splitlines() if line.strip().startswith(start)]
    return line


def test_a_type_says_which_shares_reveal_it():
    sdl = _sdl()
    assert _line(sdl, "type ShelfType") == 'type ShelfType @shared(by: ["@lending/shelf"]) {'
    assert _line(sdl, "type BookType") == 'type BookType @shared(by: ["@lending/shelf"]) {'  # part of the shelf
    assert _line(sdl, "type LoanType") == 'type LoanType @shared(by: ["@lending/shelf#derived"]) {'
    assert _line(sdl, "type PartType") == "type PartType {"  # no share reveals it


def test_a_list_field_says_which_reach_of_the_parents_share_reveals_it():
    sdl = _sdl()
    assert _line(sdl, "books:").endswith('@shared(by: ["#self"])')
    assert _line(sdl, "loans:").endswith('@shared(by: ["#derived"])')
    assert _line(sdl, "parts:").endswith("@shared(by: [])")
    assert "@shared" not in _line(sdl, "firstBook:")  # a to-one field comes with its row
    assert "@shared" not in _line(sdl, "name:")
