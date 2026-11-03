"""``prune_organizations``: an organization's rows go, whatever links them; nobody else's do."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

pytest.importorskip("authentikate")

from authentikate.models import Membership, Organization, User  # noqa: E402

from arkitekt_service.server.prune import pruning  # noqa: E402
from test_org_app.models import Board, Child, Item, Link, Note, Pin, Space  # noqa: E402

pytestmark = pytest.mark.django_db


def org(name: str, issuer: str = "https://lok") -> Organization:
    return Organization.objects.create(identifier=f"{issuer}#{name}")


def seed(owner: Organization, *, notes: int = 3, children: int = 2) -> Space:
    """A space with an item (and its children), and a chain of notes each superseding the last."""
    space = Space.objects.create(organization=owner)
    item = Item.objects.create(organization=owner, space=space)
    Child.objects.bulk_create([Child(item=item) for _ in range(children)])
    previous = None
    for _ in range(notes):
        previous = Note.objects.create(organization=owner, supersedes=previous)
    return space


def owned(owner: Organization) -> int:
    return sum(model.objects.filter(organization=owner).count() for model in (Space, Item, Note, Link))


def test_only_deletes_everything_the_organization_owns_and_nothing_else(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = org("a"), org("b")
    seed(a, notes=5)
    seed(b)
    user = User.objects.create(username="ada")
    Membership.objects.create(user=user, organization=a)

    call_command("prune_organizations", "--only", "a", "--yes")

    assert owned(a) == 0 and not Child.objects.filter(item__organization=a).exists()
    assert owned(b) == 5 and Child.objects.count() == 2, "another organization keeps all it has"
    assert Organization.objects.filter(pk=a.pk).exists() and Membership.objects.filter(organization=a).exists(), "the identity stays"
    out = capsys.readouterr().out
    assert "https://lok#a: 9 rows deleted" in out and "test_org_app.Note: 5" in out and "test_org_app.Child: 2" in out


def test_keep_prunes_every_other_organization() -> None:
    a, b, c = org("a"), org("b"), org("c")
    for owner in (a, b, c):
        seed(owner)

    call_command("prune_organizations", "--keep", "https://lok#b", "--yes")

    assert owned(a) == owned(c) == 0
    assert owned(b) == 5


def test_without_yes_it_counts_and_deletes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    a = org("a")
    seed(a, notes=4)

    call_command("prune_organizations", "--only", "a")

    assert owned(a) == 6
    out = capsys.readouterr().out
    assert "https://lok#a: 6 rows owned" in out and "test_org_app.Note: 4" in out and "pass --yes to delete" in out


def test_a_long_chain_of_protections_goes_in_passes() -> None:
    a = org("a")
    seed(a, notes=120)

    call_command("prune_organizations", "--only", "a", "--yes", "--batch-size", "7")

    assert owned(a) == 0


def test_running_it_again_finds_nothing_to_prune(capsys: pytest.CaptureFixture[str]) -> None:
    a = org("a")
    seed(a)
    call_command("prune_organizations", "--only", "a", "--yes")
    capsys.readouterr()

    call_command("prune_organizations", "--only", "a", "--yes")

    assert "Nothing to prune" in capsys.readouterr().out


def test_a_row_another_organization_protects_is_left_and_named(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = org("a"), org("b")
    space = seed(a)
    theirs = Item.objects.create(organization=b, space=space)

    with pytest.raises(CommandError, match="1 row"):
        call_command("prune_organizations", "--only", "a", "--yes")

    assert list(Space.objects.filter(organization=a)) == [space], "only the protected space is left"
    assert owned(a) == 1
    assert Item.objects.filter(pk=theirs.pk).exists()
    assert f"test_org_app.Space {space.pk}" in capsys.readouterr().err


def test_a_cascade_never_takes_another_organizations_row_along(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = org("a"), org("b")
    seed(a)
    item = Item.objects.get(organization=a)
    theirs = Link.objects.create(organization=b, item=item)

    with pytest.raises(CommandError):
        call_command("prune_organizations", "--only", "a", "--yes")

    assert Link.objects.filter(pk=theirs.pk).exists()
    assert Item.objects.filter(pk=item.pk).exists()
    assert Note.objects.filter(organization=a).count() == 0, "what is not held on to goes"
    assert "another organization" in capsys.readouterr().err


def test_a_row_without_an_organization_is_its_cascade_parents(capsys: pytest.CaptureFixture[str]) -> None:
    """Their pin on our item is theirs through its board: it is not taken along."""
    a, b = org("a"), org("b")
    seed(a)
    item = Item.objects.get(organization=a)
    ours = Pin.objects.create(board=Board.objects.create(organization=a), item=item)

    call_command("prune_organizations", "--only", "a", "--yes")
    assert not Pin.objects.filter(pk=ours.pk).exists(), "our pin on our item goes"

    seed(a)
    item = Item.objects.get(organization=a)
    theirs = Pin.objects.create(board=Board.objects.create(organization=b), item=item)
    with pytest.raises(CommandError):
        call_command("prune_organizations", "--only", "a", "--yes")

    assert Pin.objects.filter(pk=theirs.pk).exists() and Item.objects.filter(pk=item.pk).exists()
    assert f"test_org_app.Pin {theirs.pk}, of another organization (through board)" in capsys.readouterr().err


def test_pruning_both_organizations_lets_the_cascade_cross() -> None:
    a, b = org("a"), org("b")
    seed(a)
    Link.objects.create(organization=b, item=Item.objects.get(organization=a))

    call_command("prune_organizations", "--only", "a", "b", "--yes")

    assert owned(a) == owned(b) == 0


def test_a_big_cascade_is_split_into_transactions(capsys: pytest.CaptureFixture[str]) -> None:
    a = org("a")
    space = Space.objects.create(organization=a)
    for _ in range(8):
        item = Item.objects.create(organization=a, space=space)
        Child.objects.bulk_create([Child(item=item) for _ in range(3)])

    call_command("prune_organizations", "--only", "a", "--yes", "--max-cascade", "10")

    assert owned(a) == 0 and not Child.objects.exists()
    assert "past --max-cascade" not in capsys.readouterr().out, "split until each fits"


def test_a_single_row_past_the_cap_still_goes_and_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    a = org("a")
    seed(a, notes=0, children=30)

    call_command("prune_organizations", "--only", "a", "--yes", "--max-cascade", "10")

    assert owned(a) == 0
    assert "past --max-cascade: test_org_app.Item" in capsys.readouterr().out


def test_receivers_see_the_rows_before_they_go() -> None:
    a = org("a")
    seed(a, notes=3)
    seen: list[int] = []

    def record(sender: type, rows, organization: Organization, **kwargs: object) -> None:
        assert organization == a
        seen.extend(rows.values_list("pk", flat=True))

    pruning.connect(record, sender=Note)
    try:
        call_command("prune_organizations", "--only", "a", "--yes")
    finally:
        pruning.disconnect(record, sender=Note)

    assert len(seen) == 3


def test_a_name_must_match_exactly_one_organization() -> None:
    org("a")
    org("a", issuer="https://other")
    seed(Organization.objects.get(identifier="https://lok#a"))

    with pytest.raises(CommandError, match="names 2 organizations"):
        call_command("prune_organizations", "--keep", "a", "--yes")
    with pytest.raises(CommandError, match="No organization 'b'"):
        call_command("prune_organizations", "--keep", "b", "--yes")
    with pytest.raises(CommandError):
        call_command("prune_organizations", "--yes")

    assert owned(Organization.objects.get(identifier="https://lok#a")) == 5, "a refused run deletes nothing"
