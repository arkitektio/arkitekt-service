"""Counting and paging a service's collaborations, as the mounted resolvers answer it.

A card used to pull every item of every collaboration to say how many there were, and a home
page counted collaborations with ``list.length``. These are the resolvers answering both, and
paging both lists -- held to a guest's collaborations before a page is cut, not after.
"""
# ruff: noqa: F811 -- the fixtures shared with test_collaborations are imported, then requested by name

from types import SimpleNamespace
from typing import Any

import pytest

strawberry = pytest.importorskip("strawberry")
pytest.importorskip("authentikate.models")

from authentikate.models import Organization
from strawberry.extensions import FieldExtension

from arkitekt_service.collaborations import graphql as collaboration_types
from arkitekt_service.collaborations import items
from arkitekt_service.collaborations.models import Collaboration
from arkitekt_service.graphql import CollaborationGate
from tests.service.test_collaborations import (
    Authenticated,
    Token,
    granted,
    lending,  # noqa: F401
    library,  # noqa: F401
)

api = collaboration_types.resolvers(
    fetch=lambda model, id, info: model.objects.get(pk=id)
)


class AllowScoped(FieldExtension):
    """What a service mounts the stats with (``AuthExtension(allow_scoped=True)``): a guest may ask."""

    allow_scoped = True

    def resolve(self, next_: Any, source: Any, info: Any, **kwargs: Any) -> Any:
        return next_(source, info, **kwargs)


@strawberry.type
class Query:
    collaborations: list[collaboration_types.Collaboration] = strawberry.field(
        resolver=api.collaborations
    )
    collaborations_stats: collaboration_types.CollaborationStats = strawberry.field(
        resolver=api.collaborations_stats, extensions=[AllowScoped()]
    )


class Served:
    """A built schema run as one request in the library's organization, as a service mounts it."""

    def __init__(self) -> None:
        self.schema = strawberry.Schema(
            query=Query, extensions=[Authenticated, CollaborationGate]
        )
        self.organization = Organization.objects.get(identifier="lok#org")

    def execute_sync(
        self, query: str, variable_values: dict[str, Any] | None = None
    ) -> Any:
        return self.schema.execute_sync(
            query,
            variable_values=variable_values,
            context_value=SimpleNamespace(
                request=SimpleNamespace(organization=self.organization)
            ),
        )


def schema() -> Served:
    return Served()


@pytest.fixture
def filled(library):
    """The reading room holds the first shelf, the archive the second: each brings its loan."""
    items.add(library.collaboration, library.shelves[0])
    other = Collaboration.objects.create(
        organization=library.organization, name="Archive"
    )
    items.add(other, library.shelves[1])
    Collaboration.objects.create(organization=library.organization, name="Empty")
    return library, other


@pytest.mark.django_db
def test_each_collaboration_counts_what_is_in_it(filled, django_assert_max_num_queries):
    Authenticated.token = Token()

    served = schema()
    # One for the stand-in authentication's organization, one for the list with its counts.
    with django_assert_max_num_queries(2):
        result = served.execute_sync(
            "{ collaborations { name itemCount put: itemCount(brought: false) brought: itemCount(brought: true) } }"
        )
    assert result.errors is None, result.errors
    rows = {row["name"]: row for row in result.data["collaborations"]}

    assert rows["Reading room"] == {
        "name": "Reading room",
        "itemCount": 2,
        "put": 1,
        "brought": 1,
    }, "the shelf was put in; its loan came along"
    assert rows["Archive"]["itemCount"] == 2 and rows["Empty"]["itemCount"] == 0


@pytest.mark.django_db
def test_items_page_and_split_on_brought(filled):
    Authenticated.token = Token()
    query = """
    query {
      collaborations { id name
        first: items(pagination: { offset: 0, limit: 1 }) { id }
        second: items(pagination: { offset: 1, limit: 1 }) { id }
        put: items(brought: false) { brought }
      }
    }
    """
    result = schema().execute_sync(query)
    assert result.errors is None, result.errors
    room = next(
        row for row in result.data["collaborations"] if row["name"] == "Reading room"
    )

    assert (
        len(room["first"]) == 1
        and len(room["second"]) == 1
        and room["first"] != room["second"]
    )
    assert room["put"] == [{"brought": False}]


@pytest.mark.django_db
def test_the_root_list_pages(filled):
    Authenticated.token = Token()
    pages = [
        schema()
        .execute_sync(
            f"{{ collaborations(pagination: {{ offset: {offset}, limit: 2 }}) {{ name }} }}"
        )
        .data["collaborations"]
        for offset in (0, 2)
    ]
    assert [len(page) for page in pages] == [2, 1]
    assert {row["name"] for page in pages for row in page} == {
        "Reading room",
        "Archive",
        "Empty",
    }


@pytest.mark.django_db
def test_stats_count_what_the_list_lists(filled):
    Authenticated.token = Token()
    result = schema().execute_sync("{ collaborationsStats { count itemCount } }")
    assert result.errors is None, result.errors
    assert result.data["collaborationsStats"] == {"count": 3, "itemCount": 4}


@pytest.mark.django_db
def test_a_guest_counts_and_pages_only_their_own(filled):
    _, other = filled
    Authenticated.token = granted(**{str(other.pk): []})

    listed = schema().execute_sync(
        "{ collaborations(pagination: { offset: 0, limit: 1 }) { name itemCount } }"
    )
    assert listed.errors is None, listed.errors
    assert listed.data["collaborations"] == [{"name": "Archive", "itemCount": 2}], (
        "narrowed before the page is cut: the first page is theirs, not short"
    )

    stats = schema().execute_sync("{ collaborationsStats { count itemCount } }")
    assert stats.errors is None, stats.errors
    assert stats.data["collaborationsStats"] == {"count": 1, "itemCount": 2}
