"""Putting objects in a collaboration, and taking them out.

``add(collaboration, scene)`` puts the scene in, and what its structure says it needs to be seen
(``depends``), recursively, each brought ``because_of`` it. What is part of any of them is in the
collaboration by itself (:mod:`.guests`), and is never written here.
"""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

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


def add(collaboration: Collaboration, obj: Any, *, by: Any = None, because_of: CollaborationItem | None = None) -> CollaborationItem:
    """Put ``obj`` in ``collaboration``, with what it needs to be seen. Put in again by hand, it stays
    when whatever brought it is taken out."""
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


__all__ = ["add", "content_type_of", "include", "refresh", "remove"]
