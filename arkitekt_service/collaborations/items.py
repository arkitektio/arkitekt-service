"""Putting objects in a collaboration, and taking them out.

``add(collaboration, scene)`` puts the scene in, and what its structure says it needs to be seen
(``depends``), recursively, each brought ``because_of`` it. What is part of any of them is in the
collaboration by itself (:mod:`.guests`), and is never written here.

What is part of another object (a message of its room, a layer of its scene) is not put in alone:
:func:`add` refuses it unless its whole is in (:class:`SharedWithItsWhole`). Alone, it would be
handed out without what it cannot be read without.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Model

from arkitekt_service.collaborations.guests import wholes
from arkitekt_service.collaborations.models import Collaboration, CollaborationItem


def content_type_of(obj: Model) -> ContentType:
    return ContentType.objects.get_for_model(type(obj))


def _key(obj: Model) -> tuple[type, str]:
    return (type(obj), str(obj.pk))


def _bring(
    collaboration: Collaboration,
    item: CollaborationItem,
    obj: Model,
    by: object,
    seen: set[tuple[type, str]],
) -> None:
    """Put in what ``obj`` needs to be seen, and what that needs in turn, brought by ``item``."""
    from arkitekt_service.service.service import Service

    structure = Service.hosting(obj)
    for needed in structure.dependencies(obj) if structure is not None else ():
        if _key(needed) in seen:
            continue
        seen.add(_key(needed))
        brought, created = CollaborationItem.objects.get_or_create(
            collaboration=collaboration,
            content_type=content_type_of(needed),
            object_id=str(needed.pk),
            defaults={"because_of": item, "added_by": by},
        )
        if created:
            _bring(collaboration, brought, needed, by, seen)


class SharedWithItsWhole(ValueError):
    """``obj`` is part of another object, which is not in the collaboration: that one is to be put in."""

    def __init__(self, obj: Model, whole: Model) -> None:
        self.obj, self.whole = obj, whole
        super().__init__(
            f"{_name(obj)} {obj.pk} is part of {_name(whole)} {whole.pk}, and is shared with it: put that in the collaboration"
        )


def _name(obj: Model) -> str:
    from arkitekt_service.service.service import Service

    structure = Service.hosting(obj)
    return structure.identifier if structure is not None else type(obj).__name__


def whole_of(obj: Model) -> Model | None:
    """The object ``obj`` is shared with, if it is part of one: what its structure names (``whole``),
    else the nearest hosted object a required, cascading key leads to. None for a whole."""
    from arkitekt_service.service.service import Service

    structure = Service.hosting(obj)
    if structure is not None and structure.whole is not None:
        return structure.whole(obj)
    for model, path in wholes(type(obj)):
        if Service.hosting(model) is None:
            continue
        found = obj
        for name in path.split("__"):
            found = getattr(found, name)
        return found
    return None


def holds(collaboration: Collaboration, obj: Model) -> bool:
    """Whether ``obj`` is in ``collaboration``: put in, or part of something that is."""
    if collaboration.pk is None:
        return False  # a collaboration nothing was put in here yet holds nothing here
    if CollaborationItem.objects.filter(
        collaboration=collaboration,
        content_type=content_type_of(obj),
        object_id=str(obj.pk),
    ).exists():
        return True
    whole = whole_of(obj)
    return whole is not None and holds(collaboration, whole)


def add(
    collaboration: Collaboration,
    obj: Model,
    *,
    by: object = None,
    because_of: CollaborationItem | None = None,
) -> CollaborationItem:
    """Put ``obj`` in ``collaboration``, with what it needs to be seen. Put in again by hand, it stays
    when whatever brought it is taken out. Put in by hand, a part of another object is refused unless
    that one is in (:class:`SharedWithItsWhole`)."""
    if because_of is None:
        whole = whole_of(obj)
        if whole is not None and not holds(collaboration, whole):
            raise SharedWithItsWhole(obj, whole)
    with transaction.atomic():
        item, created = CollaborationItem.objects.get_or_create(
            collaboration=collaboration,
            content_type=content_type_of(obj),
            object_id=str(obj.pk),
            defaults={"because_of": because_of, "added_by": by},
        )
        if created:
            _bring(collaboration, item, obj, by, {_key(obj)})
        elif because_of is None and item.because_of_id is not None:
            item.because_of = None
            item.save(update_fields=["because_of"])
    return item


def _objects(
    items: Iterable[Any],
) -> list[tuple[CollaborationItem, Model]]:
    """Each item with its object; an item whose object is gone is left out."""
    found = []
    for item in items:
        model = item.content_type.model_class()
        obj = (
            model._base_manager.filter(pk=item.object_id).first()
            if model is not None
            else None
        )
        if obj is not None:
            found.append((item, obj))
    return found


def remove(collaboration: Collaboration, obj: Model) -> bool:
    """Take ``obj`` out of ``collaboration``, with what it brought, unless something still there needs it.
    Returns whether it was there."""
    with transaction.atomic():
        deleted, _ = CollaborationItem.objects.filter(
            collaboration=collaboration,
            content_type=content_type_of(obj),
            object_id=str(obj.pk),
        ).delete()
        if not deleted:
            return False
        # What another object still in the collaboration needs comes back.
        for item, kept in _objects(
            collaboration.items.filter(because_of__isnull=True).select_related(
                "content_type"
            )
        ):
            _bring(collaboration, item, kept, item.added_by, {_key(kept)})
    return True


def refresh(obj: Model) -> None:
    """Put what ``obj`` needs now in every collaboration it is in: after a part was added that needs more
    (a layer, drawing a dataset, added to a scene in a collaboration)."""
    for item in CollaborationItem.objects.filter(
        content_type=content_type_of(obj), object_id=str(obj.pk)
    ).select_related("collaboration"):
        _bring(item.collaboration, item, obj, item.added_by, {_key(obj)})


def include(obj: Model, *, alongside: Model, by: object = None) -> None:
    """Put ``obj`` in every collaboration ``alongside`` is in, brought by it: a collection minted for a scene."""
    for item in CollaborationItem.objects.filter(
        content_type=content_type_of(alongside), object_id=str(alongside.pk)
    ).select_related("collaboration"):
        add(item.collaboration, obj, by=by, because_of=item)


# --- a workflow ---------------------------------------------------------------------------------


def _row(obj: Model) -> tuple[type, str]:
    """``obj``'s row, however it is read: as itself, or as a parent (multi-table inheritance)."""
    parents = type(obj)._meta.get_parent_list()
    return (parents[-1] if parents else type(obj), str(obj.pk))


@dataclass(frozen=True)
class Skipped:
    """Created in the workflow, but part of ``whole``, which was made before it and is not shared."""

    obj: Model
    whole: Model


@dataclass
class WorkflowShare:
    """What sharing a workflow put in a collaboration (or, tried, would put in)."""

    #: Put in by hand (or kept, if they were in already).
    added: list[Model] = field(default_factory=list)
    #: Parts of what was put in: in the collaboration with their whole, never written.
    covered: list[Model] = field(default_factory=list)
    #: Parts of a whole made before the workflow, and not in the collaboration: left out.
    skipped: list[Skipped] = field(default_factory=list)
    #: Structures that cannot say what created their objects: nothing of them was looked at.
    unknown: list[str] = field(default_factory=list)


def created_in(
    root_task_id: str, organization: object
) -> tuple[list[Model], list[str]]:
    """Every hosted object the workflow ``root_task_id`` created in ``organization``, and the
    structures that cannot say."""
    from arkitekt_service.service.service import Service

    found: dict[tuple[type, str], Model] = {}
    unknown: list[str] = []
    for service in Service.sharing:
        for structure in service.structures.values():
            condition = structure.made_in(root_task_id, organization)
            if condition is None:
                unknown.append(structure.identifier)
                continue
            for obj in structure.model._base_manager.filter(condition):
                found.setdefault(_row(obj), obj)
    return list(found.values()), unknown


def share_workflow(
    collaboration: Collaboration,
    root_task_id: str,
    *,
    by: object = None,
    dry_run: bool = False,
) -> WorkflowShare:
    """Put what the workflow ``root_task_id`` created in ``collaboration``, as it is now.

    A whole goes in by hand; a part comes along with its whole (its required, cascading key) and
    is not written, or goes in after it (a declared ``whole``). A part of a whole made before the
    workflow and not in the collaboration is left out (:class:`Skipped`): the workflow does not
    hand out what it did not make. With ``dry_run``, nothing is written: the report says what would be.
    """
    from arkitekt_service.service.service import Service

    objects, unknown = created_in(root_task_id, collaboration.organization)
    report = WorkflowShare(unknown=unknown)
    made = {_row(obj) for obj in objects}
    placed: set[tuple[type, str]] = set()
    pending = list(objects)
    with transaction.atomic():
        while pending:
            waiting: list[Model] = []
            for obj in pending:
                whole = whole_of(obj)
                if whole is not None and _row(whole) not in placed:
                    if _row(whole) in made:
                        waiting.append(
                            obj
                        )  # its whole is the workflow's, and not placed yet
                        continue
                    if not holds(collaboration, whole):
                        report.skipped.append(Skipped(obj, whole))
                        continue
                structure = Service.hosting(obj)
                if whole is not None and (structure is None or structure.whole is None):
                    report.covered.append(obj)  # part of its whole: in with it, live
                else:
                    report.added.append(obj)
                    if not dry_run:
                        add(collaboration, obj, by=by)
                placed.add(_row(obj))
            if len(waiting) == len(pending):
                # Their wholes were made in the workflow, but are themselves left out.
                for obj in waiting:
                    whole = whole_of(obj)
                    assert whole is not None
                    report.skipped.append(Skipped(obj, whole))
                break
            pending = waiting
    return report


__all__ = [
    "SharedWithItsWhole",
    "Skipped",
    "WorkflowShare",
    "add",
    "content_type_of",
    "created_in",
    "holds",
    "include",
    "refresh",
    "remove",
    "share_workflow",
    "whole_of",
]
