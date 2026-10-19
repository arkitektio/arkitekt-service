"""Putting objects in a collaboration, and taking them out.

``add(collaboration, scene)`` puts the scene in, and what its structure says it needs to be seen
(``depends``), recursively, each brought ``because_of`` it. What is part of any of them is in the
collaboration by itself (:mod:`.guests`), and is never written here.

What is part of another object (a message of its room, a layer of its scene) is not put in alone:
:func:`add` refuses it unless its whole is in (:class:`SharedWithItsWhole`). Alone, it would be
handed out without what it cannot be read without.
"""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from arkitekt_service.collaborations.guests import wholes
from arkitekt_service.collaborations.models import Collaboration, CollaborationItem


def content_type_of(obj: Any) -> ContentType:
    return ContentType.objects.get_for_model(type(obj))


def _key(obj: Any) -> tuple[type, str]:
    return (type(obj), str(obj.pk))


def _bring(collaboration: Collaboration, item: CollaborationItem, obj: Any, by: Any, seen: set[tuple[type, str]]) -> None:
    """Put in what ``obj`` needs to be seen, and what that needs in turn, brought by ``item``."""
    from arkitekt_service.service.service import Service

    structure = Service.hosting(obj)
    for needed in structure.dependencies(obj) if structure is not None else ():
        if _key(needed) in seen:
            continue
        seen.add(_key(needed))
        brought, created = CollaborationItem.objects.get_or_create(
            collaboration=collaboration, content_type=content_type_of(needed), object_id=str(needed.pk), defaults={"because_of": item, "added_by": by}
        )
        if created:
            _bring(collaboration, brought, needed, by, seen)


class SharedWithItsWhole(ValueError):
    """``obj`` is part of another object, which is not in the collaboration: that one is to be put in."""

    def __init__(self, obj: Any, whole: Any) -> None:
        self.obj, self.whole = obj, whole
        super().__init__(f"{_name(obj)} {obj.pk} is part of {_name(whole)} {whole.pk}, and is shared with it: put that in the collaboration")


def _name(obj: Any) -> str:
    from arkitekt_service.service.service import Service

    structure = Service.hosting(obj)
    return structure.identifier if structure is not None else type(obj).__name__


def whole_of(obj: Any) -> Any:
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


def holds(collaboration: Collaboration, obj: Any) -> bool:
    """Whether ``obj`` is in ``collaboration``: put in, or part of something that is."""
    if CollaborationItem.objects.filter(collaboration=collaboration, content_type=content_type_of(obj), object_id=str(obj.pk)).exists():
        return True
    whole = whole_of(obj)
    return whole is not None and holds(collaboration, whole)


def add(collaboration: Collaboration, obj: Any, *, by: Any = None, because_of: CollaborationItem | None = None) -> CollaborationItem:
    """Put ``obj`` in ``collaboration``, with what it needs to be seen. Put in again by hand, it stays
    when whatever brought it is taken out. Put in by hand, a part of another object is refused unless
    that one is in (:class:`SharedWithItsWhole`)."""
    if because_of is None:
        whole = whole_of(obj)
        if whole is not None and not holds(collaboration, whole):
            raise SharedWithItsWhole(obj, whole)
    with transaction.atomic():
        item, created = CollaborationItem.objects.get_or_create(
            collaboration=collaboration, content_type=content_type_of(obj), object_id=str(obj.pk), defaults={"because_of": because_of, "added_by": by}
        )
        if created:
            _bring(collaboration, item, obj, by, {_key(obj)})
        elif because_of is None and item.because_of_id is not None:
            item.because_of = None
            item.save(update_fields=["because_of"])
    return item


def _objects(items: Any) -> list[tuple[CollaborationItem, Any]]:
    """Each item with its object; an item whose object is gone is left out."""
    found = []
    for item in items:
        model = item.content_type.model_class()
        obj = model._base_manager.filter(pk=item.object_id).first() if model is not None else None
        if obj is not None:
            found.append((item, obj))
    return found


def remove(collaboration: Collaboration, obj: Any) -> bool:
    """Take ``obj`` out of ``collaboration``, with what it brought, unless something still there needs it.
    Returns whether it was there."""
    with transaction.atomic():
        deleted, _ = CollaborationItem.objects.filter(collaboration=collaboration, content_type=content_type_of(obj), object_id=str(obj.pk)).delete()
        if not deleted:
            return False
        # What another object still in the collaboration needs comes back.
        for item, kept in _objects(collaboration.items.filter(because_of__isnull=True).select_related("content_type")):
            _bring(collaboration, item, kept, item.added_by, {_key(kept)})
    return True


def refresh(obj: Any) -> None:
    """Put what ``obj`` needs now in every collaboration it is in: after a part was added that needs more
    (a layer, drawing a dataset, added to a scene in a collaboration)."""
    for item in CollaborationItem.objects.filter(content_type=content_type_of(obj), object_id=str(obj.pk)).select_related("collaboration"):
        _bring(item.collaboration, item, obj, item.added_by, {_key(obj)})


def include(obj: Any, *, alongside: Any, by: Any = None) -> None:
    """Put ``obj`` in every collaboration ``alongside`` is in, brought by it: a collection minted for a scene."""
    for item in CollaborationItem.objects.filter(content_type=content_type_of(alongside), object_id=str(alongside.pk)).select_related("collaboration"):
        add(item.collaboration, obj, by=by, because_of=item)


__all__ = ["SharedWithItsWhole", "add", "content_type_of", "holds", "include", "refresh", "remove", "whole_of"]
