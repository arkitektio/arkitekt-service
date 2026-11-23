"""Sharing a workflow: what a root task created goes in a collaboration, as it is now.

The lending service says what a workflow made through ``made_in`` (``created_under``); a service
with koherent reads ``created_through`` or the history instead. A shelf is a whole, its books are
part of it (a required, cascading key), and a loan is shared with its shelf (a declared whole).
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

strawberry = pytest.importorskip("strawberry")
strawberry_django = pytest.importorskip("strawberry_django")
pytest.importorskip("authentikate.models")

from authentikate.models import Organization  # noqa: E402
from django.db.models import Q  # noqa: E402
from strawberry.types import ExecutionResult  # noqa: E402

from arkitekt_service.collaborations import graphql as collaboration_types  # noqa: E402
from arkitekt_service.collaborations import items  # noqa: E402
from arkitekt_service.collaborations.filters import (  # noqa: E402
    SharingFilterMixin,
    created_under_q,
    in_collaboration_q,
)
from arkitekt_service.collaborations.models import (  # noqa: E402
    Collaboration,
    CollaborationOfAnotherOrganization,
    ensure,
)
from arkitekt_service.graphql import CollaborationGate  # noqa: E402
from arkitekt_service.service import Service  # noqa: E402
from test_app.models import Book, Crate, Loan, Shelf  # noqa: E402
from tests.service.test_collaborations import Authenticated, Token, granted  # noqa: E402

WORKFLOW = "root-task-7"


def _made_in(root_task_id: str) -> Q:
    return Q(made_in=root_task_id)


@pytest.fixture(autouse=True)
def lending() -> Iterator[Service]:
    """The lending service, sharing, saying what a workflow made by ``made_in``."""
    before = list(Service.sharing)
    Service.sharing.clear()
    service = Service("lending")
    service.structure(
        Shelf,
        "@lending/shelf",
        depends=lambda shelf: list(shelf.loans.all()),
        created_under=_made_in,
    )
    service.structure(Book, "@lending/book", created_under=_made_in)
    service.structure(
        Loan, "@lending/loan", whole=lambda loan: loan.shelf, created_under=_made_in
    )
    service.structure(Crate, "@lending/crate")  # says nothing of what made it
    service.share()
    yield service
    Service.sharing[:] = before


@dataclass
class Workflow:
    organization: Organization
    shelf: Shelf  # made in the workflow
    book: Book  # made in it, on its shelf
    loan: Loan  # made in it, of its shelf
    older: Shelf  # made before it
    late_book: Book  # made in it, on the older shelf
    late_loan: Loan  # made in it, of the older shelf
    unrelated: Shelf  # another workflow's
    collaboration: Collaboration


@pytest.fixture
def workflow(db: None) -> Workflow:
    organization = Organization.objects.create(identifier="lok#org")
    shelf = Shelf.objects.create(name="new", made_in=WORKFLOW)
    older = Shelf.objects.create(name="old")
    return Workflow(
        organization=organization,
        shelf=shelf,
        book=Book.objects.create(shelf=shelf, made_in=WORKFLOW),
        loan=Loan.objects.create(shelf=shelf, made_in=WORKFLOW),
        older=older,
        late_book=Book.objects.create(shelf=older, made_in=WORKFLOW),
        late_loan=Loan.objects.create(shelf=older, made_in=WORKFLOW),
        unrelated=Shelf.objects.create(name="other", made_in="root-task-8"),
        collaboration=ensure(uuid.uuid4(), organization, name="Reading room"),
    )


def _in(collaboration: Collaboration) -> set[tuple[str, str]]:
    return set(collaboration.items.values_list("content_type__model", "object_id"))


class TestSharingAWorkflow:
    def test_wholes_go_in_parts_come_along_and_outside_wholes_are_left_out(
        self, workflow: Workflow
    ) -> None:
        share = items.share_workflow(workflow.collaboration, WORKFLOW)

        assert set(share.added) == {workflow.shelf, workflow.loan}
        assert share.covered == [workflow.book]
        assert {(s.obj, s.whole) for s in share.skipped} == {
            (workflow.late_book, workflow.older),
            (workflow.late_loan, workflow.older),
        }
        assert share.unknown == ["@lending/crate"]
        # The shelf, and its loan (put in by hand, and brought by the shelf: once).
        assert _in(workflow.collaboration) == {
            ("shelf", str(workflow.shelf.pk)),
            ("loan", str(workflow.loan.pk)),
        }
        assert items.holds(workflow.collaboration, workflow.book)
        assert not items.holds(workflow.collaboration, workflow.late_book)

    def test_a_part_of_a_whole_already_shared_goes_in(self, workflow: Workflow) -> None:
        items.add(workflow.collaboration, workflow.older)
        share = items.share_workflow(workflow.collaboration, WORKFLOW)

        assert share.skipped == []
        assert workflow.late_book in share.covered
        assert workflow.late_loan in share.added

    def test_a_dry_run_writes_nothing(self, workflow: Workflow) -> None:
        share = items.share_workflow(workflow.collaboration, WORKFLOW, dry_run=True)

        assert set(share.added) == {workflow.shelf, workflow.loan}
        assert len(share.skipped) == 2
        assert _in(workflow.collaboration) == set()

    def test_a_snapshot_leaves_what_the_workflow_makes_later(
        self, workflow: Workflow
    ) -> None:
        items.share_workflow(workflow.collaboration, WORKFLOW)
        later = Shelf.objects.create(name="later", made_in=WORKFLOW)

        assert not items.holds(workflow.collaboration, later)


class TestFilters:
    def test_in_collaboration_holds_what_was_put_in_and_its_parts(
        self, workflow: Workflow
    ) -> None:
        items.add(workflow.collaboration, workflow.shelf)
        key, org = workflow.collaboration.key, workflow.organization

        assert set(Shelf.objects.filter(in_collaboration_q(Shelf, key, org))) == {
            workflow.shelf
        }
        assert set(Book.objects.filter(in_collaboration_q(Book, key, org))) == {
            workflow.book
        }
        elsewhere = Organization.objects.create(identifier="lok#elsewhere")
        assert not Shelf.objects.filter(in_collaboration_q(Shelf, key, elsewhere))
        assert not Shelf.objects.filter(in_collaboration_q(Shelf, "no-key", org))

    def test_created_under_reads_the_structure(self, workflow: Workflow) -> None:
        assert set(Book.objects.filter(created_under_q(Book, WORKFLOW))) == {
            workflow.book,
            workflow.late_book,
        }
        # A model that cannot say what made it: nothing, never everything.
        assert not Crate.objects.filter(created_under_q(Crate, WORKFLOW)).exists()


class TestTheKey:
    def test_a_key_is_one_organizations(self, workflow: Workflow) -> None:
        elsewhere = Organization.objects.create(identifier="lok#elsewhere")
        with pytest.raises(CollaborationOfAnotherOrganization):
            ensure(workflow.collaboration.key, elsewhere)

    def test_ensuring_again_finds_the_row_and_keeps_a_first_name(
        self, workflow: Workflow
    ) -> None:
        key = uuid.uuid4()
        made = ensure(key, workflow.organization)
        assert made.name == ""
        assert ensure(key, workflow.organization, name="Lab") == made
        made.refresh_from_db()
        assert made.name == "Lab"


# --- over GraphQL ---------------------------------------------------------------------------

api = collaboration_types.resolvers(
    fetch=lambda model, id, info: model.objects.get(pk=id)
)


@strawberry_django.filter_type(Shelf)
class ShelfFilter(SharingFilterMixin):
    name: strawberry.auto


@strawberry_django.filter_type(Book)
class BookFilter(SharingFilterMixin):
    shelf: ShelfFilter | None


@strawberry_django.type(Shelf)
class ShelfType:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.type(Book, filters=BookFilter)
class BookType:
    id: strawberry.auto


@strawberry.type
class Query:
    books: list[BookType] = strawberry_django.field()
    shelves: list[ShelfType] = strawberry_django.field(filters=ShelfFilter)
    collaboration: collaboration_types.Collaboration = strawberry.field(
        resolver=api.collaboration
    )


@strawberry.type
class Mutation:
    share_workflow: collaboration_types.WorkflowShare = strawberry.field(
        resolver=api.share_workflow
    )
    add_many_to_collaboration: collaboration_types.Collaboration = strawberry.field(
        resolver=api.add_many_to_collaboration
    )


def run(
    workflow: Workflow, query: str, variables: dict[str, Any] | None = None
) -> ExecutionResult:
    served = strawberry.Schema(
        query=Query, mutation=Mutation, extensions=[Authenticated, CollaborationGate]
    )
    return served.execute_sync(
        query,
        variable_values=variables,
        context_value=SimpleNamespace(
            request=SimpleNamespace(organization=workflow.organization, user=None)
        ),
    )


SHARE = """
mutation($key: ID!, $dry: Boolean!) {
  shareWorkflow(input: {collaboration: $key, rootTask: "root-task-7", dryRun: $dry, name: "Run 7"}) {
    collaboration dryRun covered unknown
    added { identifier object }
    skipped { identifier object wholeIdentifier whole }
  }
}
"""


@pytest.mark.django_db
def test_sharing_a_workflow_over_graphql_makes_the_row_on_first_use(
    workflow: Workflow,
) -> None:
    Authenticated.token = Token()
    key = str(uuid.uuid4())

    dry = run(workflow, SHARE, {"key": key, "dry": True})
    assert dry.errors is None, dry.errors
    assert dry.data is not None
    assert dry.data["shareWorkflow"]["dryRun"] is True
    assert not Collaboration.objects.filter(key=key).exists()

    done = run(workflow, SHARE, {"key": key, "dry": False})
    assert done.errors is None, done.errors
    assert done.data is not None
    report = done.data["shareWorkflow"]
    assert report["collaboration"] == key
    assert {(a["identifier"], a["object"]) for a in report["added"]} == {
        ("@lending/shelf", str(workflow.shelf.pk)),
        ("@lending/loan", str(workflow.loan.pk)),
    }
    assert report["covered"] == 1
    assert {(s["object"], s["whole"]) for s in report["skipped"]} == {
        (str(workflow.late_book.pk), str(workflow.older.pk)),
        (str(workflow.late_loan.pk), str(workflow.older.pk)),
    }
    assert report["unknown"] == ["@lending/crate"]
    assert Collaboration.objects.get(key=key).name == "Run 7"


@pytest.mark.django_db
def test_a_guest_granted_the_key_sees_what_the_workflow_shared(
    workflow: Workflow,
) -> None:
    items.share_workflow(workflow.collaboration, WORKFLOW)
    key = str(workflow.collaboration.key)

    Authenticated.token = granted(**{key: []})
    seen = run(workflow, "{ books { id } }")
    assert seen.errors is None, seen.errors
    assert seen.data == {"books": [{"id": str(workflow.book.pk)}]}

    Authenticated.token = granted(**{str(uuid.uuid4()): []})
    assert run(workflow, "{ books { id } }").data == {"books": []}


@pytest.mark.django_db
def test_lists_filter_by_collaboration_and_workflow(workflow: Workflow) -> None:
    items.add(workflow.collaboration, workflow.older)
    Authenticated.token = Token()
    key = str(workflow.collaboration.key)

    in_it = run(
        workflow,
        "query($key: ID!) { books(filters: {inCollaboration: $key}) { id } }",
        {"key": key},
    )
    assert in_it.errors is None, in_it.errors
    assert in_it.data == {"books": [{"id": str(workflow.late_book.pk)}]}

    made = run(
        workflow,
        'query { shelves(filters: {createdUnder: "root-task-7"}) { id } }',
    )
    assert made.data == {"shelves": [{"id": str(workflow.shelf.pk)}]}

    # Nested under a relation: books whose shelf is in the collaboration.
    nested = run(
        workflow,
        "query($key: ID!) { books(filters: {shelf: {inCollaboration: $key}}) { id } }",
        {"key": key},
    )
    assert nested.errors is None, nested.errors
    assert nested.data == {"books": [{"id": str(workflow.late_book.pk)}]}


@pytest.mark.django_db
def test_adding_many_makes_the_row_and_puts_each_in(workflow: Workflow) -> None:
    Authenticated.token = Token()
    key = str(uuid.uuid4())
    answer = run(
        workflow,
        """mutation($key: ID!, $a: ID!, $b: ID!) {
          addManyToCollaboration(input: {collaboration: $key, objects: [
            {identifier: "@lending/shelf", object: $a},
            {identifier: "@lending/shelf", object: $b},
            {identifier: "@lending/shelf", object: $a}
          ]}) { key }
        }""",
        {"key": key, "a": str(workflow.shelf.pk), "b": str(workflow.older.pk)},
    )
    assert answer.errors is None, answer.errors
    assert answer.data == {"addManyToCollaboration": {"key": key}}
    made = Collaboration.objects.get(key=key)
    assert items.holds(made, workflow.shelf) and items.holds(made, workflow.older)
