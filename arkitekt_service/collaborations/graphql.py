"""The collaborations of a service, over its GraphQL: types, and resolvers it mounts with its own rules.

::

    from arkitekt_service.collaborations import graphql as collaboration_types

    api = collaboration_types.resolvers(fetch=lambda model, id, info: get_for_org(model, info, id=id))

    @kante.type
    class Query:
        collaborations: list[collaboration_types.Collaboration] = kante.django_field(resolver=api.collaborations, extensions=[AuthExtension()])
        collaborations_stats: collaboration_types.CollaborationStats = kante.django_field(resolver=api.collaborations_stats, extensions=[AuthExtension(allow_scoped=True)])

    @kante.type
    class Mutation:
        add_to_collaboration: collaboration_types.Collaboration = kante.django_mutation(resolver=api.add_to_collaboration, extensions=[...])
        share_workflow: collaboration_types.WorkflowShare = kante.django_mutation(resolver=api.share_workflow, extensions=[...])

A collaboration is lok's, named by its key across the hub (lok makes it, and grants it to guests):
this service holds what was put in it here, in a row made the first time something is. An object is
named by its structure's identifier and its id (``@mikro/scene``, ``7``); ``fetch`` loads it, held
to the request's organization. A guest sees the collaborations granted to them and what is
in them (the gate holds them to it); making and changing collaborations is for full members, whom the
service's own ``AuthExtension`` on each mutation lets in.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import strawberry
import strawberry_django
from django.db import transaction
from django.db.models import Count, Model, Q, QuerySet
from strawberry_django.pagination import OffsetPaginationInput

from arkitekt_service.collaborations import items as collaboration_items
from arkitekt_service.collaborations import models
from arkitekt_service.collaborations.filters import collaboration_rows
from arkitekt_service.collaborations.guests import object_scope

#: The annotations :func:`resolvers`' list puts on each collaboration, so a list of cards counts
#: what is in them in the one query that lists them.
_ITEMS = "_arkitekt_item_count"
_PUT = "_arkitekt_put_count"


def _page(
    queryset: QuerySet[Any], pagination: OffsetPaginationInput | None
) -> QuerySet[Any]:
    """One page of ``queryset``: ``offset`` rows skipped, at most ``limit`` kept (none or negative: all)."""
    if pagination is None:
        return queryset
    offset = pagination.offset or 0
    if pagination.limit is None or pagination.limit < 0:
        return queryset[offset:]
    return queryset[offset : offset + pagination.limit]


def _brought_q(brought: bool | None) -> Q:
    """The items put in by hand (``False``), brought along by another (``True``), or all (``None``)."""
    if brought is None:
        return Q()
    return Q(because_of__isnull=not brought)


def _identifier(model: object) -> str | None:
    from arkitekt_service.service.service import Service

    structure = Service.hosting(model) if model is not None else None
    return structure.identifier if structure is not None else None


@strawberry_django.type(
    models.CollaborationItem,
    description="One object in a collaboration: put there, or brought along with one that needs it to be seen.",
)
class CollaborationItem:
    id: strawberry.ID
    object_id: str = strawberry_django.field(
        description="The object's id, in its structure"
    )
    added_at: strawberry.auto

    @strawberry_django.field(
        description="The structure the object is known by across the hub: @mikro/scene"
    )
    def identifier(self) -> str | None:
        return _identifier(self.content_type.model_class())  # type: ignore[attr-defined]

    @strawberry_django.field(
        description="Whether it is in the collaboration only as what another object in it needs to be seen"
    )
    def brought(self) -> bool:
        return self.because_of_id is not None  # type: ignore[attr-defined]


@strawberry_django.type(
    models.Collaboration,
    description="What a guest granted it sees of the organization: the objects put in it, and what is part of them.",
)
class Collaboration:
    id: strawberry.ID
    name: str

    @strawberry_django.field(
        description="The collaboration's key across the hub: what lok grants, and what names it in every service"
    )
    def key(self) -> strawberry.ID:
        return strawberry.ID(str(self.key))  # type: ignore[attr-defined]

    description: str | None
    created_at: strawberry.auto

    @strawberry_django.field(
        description="What is in the collaboration, oldest first; what is part of these is in it too. `brought` keeps what was put in by hand (false) or only what came along with it (true)"
    )
    def items(
        self,
        brought: bool | None = None,
        pagination: OffsetPaginationInput | None = None,
    ) -> list[CollaborationItem]:
        items = (
            models.CollaborationItem.objects.filter(
                _brought_q(brought), collaboration=self
            )
            .select_related("content_type")
            .order_by("added_at", "pk")
        )
        return list(_page(items, pagination))  # type: ignore[return-value]

    @strawberry_django.field(
        description="How many objects are in the collaboration: all of them, or (`brought`) only those put in by hand (false) or only those that came along with one (true)"
    )
    def item_count(self, brought: bool | None = None) -> int:
        counted: dict[str, int] = self.__dict__  # type: ignore[assignment]
        if _ITEMS in counted and _PUT in counted:
            total, put = counted[_ITEMS], counted[_PUT]
            return total if brought is None else (total - put if brought else put)
        return models.CollaborationItem.objects.filter(
            _brought_q(brought), collaboration=self
        ).count()


@strawberry.type(
    description="How many collaborations the organization has (a guest: theirs), and how much is in them"
)
class CollaborationStats:
    count: int = strawberry.field(description="How many collaborations")
    item_count: int = strawberry.field(
        description="How many objects are in them altogether, brought along ones included"
    )


@strawberry.input(description="An object to put in, or take out of, a collaboration")
class CollaborationObjectInput:
    collaboration: strawberry.ID = strawberry.field(
        description="The collaboration's key (lok's)"
    )
    identifier: str = strawberry.field(
        description="The structure the object is known by: @mikro/scene"
    )
    object: strawberry.ID = strawberry.field(description="The object's id")
    name: str | None = strawberry.field(
        default=None,
        description="The collaboration's name, kept here when this is the first thing put in",
    )


@strawberry.input(description="An object, by its structure and id")
class CollaborationObjectRef:
    identifier: str = strawberry.field(
        description="The structure the object is known by: @mikro/scene"
    )
    object: strawberry.ID = strawberry.field(description="The object's id")


@strawberry.input(
    description="Objects to put in a collaboration at once: all of them, or none"
)
class AddManyToCollaborationInput:
    collaboration: strawberry.ID = strawberry.field(
        description="The collaboration's key (lok's)"
    )
    objects: list[CollaborationObjectRef] = strawberry.field(
        description="What to put in; a whole before its parts"
    )
    name: str | None = strawberry.field(
        default=None,
        description="The collaboration's name, kept here when these are the first things put in",
    )


@strawberry.input(
    description="Put what a workflow created in a collaboration, as it is now"
)
class ShareWorkflowInput:
    collaboration: strawberry.ID = strawberry.field(
        description="The collaboration's key (lok's)"
    )
    root_task: strawberry.ID = strawberry.field(
        description="The workflow: the id of the task a person started, the root of every task it caused"
    )
    dry_run: bool = strawberry.field(
        default=False,
        description="Only say what would be put in, and what left out",
    )
    name: str | None = strawberry.field(
        default=None,
        description="The collaboration's name, kept here when this puts the first things in",
    )


@strawberry.type(description="An object, by its structure and id")
class SharedObject:
    identifier: str | None = strawberry.field(
        description="The structure the object is known by: @mikro/scene"
    )
    object: strawberry.ID = strawberry.field(description="The object's id")


@strawberry.type(
    description="Created in the workflow, but part of something made before it that is not shared: left out"
)
class SkippedObject:
    identifier: str | None = strawberry.field(
        description="The structure the object is known by"
    )
    object: strawberry.ID = strawberry.field(description="The object's id")
    whole_identifier: str | None = strawberry.field(
        description="The structure of what it is part of"
    )
    whole: strawberry.ID = strawberry.field(
        description="The id of what it is part of: put that in, and share the workflow again, to bring it"
    )


@strawberry.type(
    description="What sharing a workflow put in a collaboration here (or, tried, would put in)"
)
class WorkflowShare:
    collaboration: strawberry.ID = strawberry.field(
        description="The collaboration's key"
    )
    dry_run: bool = strawberry.field(description="Whether nothing was written")
    added: list[SharedObject] = strawberry.field(
        description="Put in, with what each needs to be seen"
    )
    covered: int = strawberry.field(
        description="How many parts came along with their whole, without being put in themselves"
    )
    skipped: list[SkippedObject] = strawberry.field(
        description="Left out: parts of something made before the workflow and not shared"
    )
    unknown: list[str] = strawberry.field(
        description="Structures here that cannot say what created their objects: not looked at"
    )


def _ref(obj: Model) -> SharedObject:
    return SharedObject(
        identifier=_identifier(type(obj)), object=strawberry.ID(str(obj.pk))
    )


def _report(
    key: object, dry_run: bool, share: collaboration_items.WorkflowShare
) -> WorkflowShare:
    return WorkflowShare(
        collaboration=strawberry.ID(str(key)),
        dry_run=dry_run,
        added=[_ref(obj) for obj in share.added],
        covered=len(share.covered),
        skipped=[
            SkippedObject(
                identifier=_identifier(type(s.obj)),
                object=strawberry.ID(str(s.obj.pk)),
                whole_identifier=_identifier(type(s.whole)),
                whole=strawberry.ID(str(s.whole.pk)),
            )
            for s in share.skipped
        ],
        unknown=share.unknown,
    )


Fetch = Callable[[Any, Any, Any], Any]


@dataclass(frozen=True)
class Resolvers:
    """The resolvers of a service's collaborations, each to be mounted with the service's own extensions."""

    collaborations: Callable[..., list[Collaboration]]
    collaboration: Callable[..., Collaboration]
    collaborations_stats: Callable[..., CollaborationStats]
    add_to_collaboration: Callable[..., Collaboration]
    add_many_to_collaboration: Callable[..., Collaboration]
    share_workflow: Callable[..., WorkflowShare]
    remove_from_collaboration: Callable[..., Collaboration]
    delete_collaboration: Callable[..., strawberry.ID]


def resolvers(fetch: Fetch) -> Resolvers:
    """Resolvers for a service's collaborations; ``fetch(model, id, info)`` loads an object held to the request's organization."""

    def _organization(info: strawberry.Info) -> object:
        return info.context.request.organization

    def _collaboration(info: strawberry.Info, key: object) -> models.Collaboration:
        found = collaboration_rows(key, _organization(info)).first()
        if not isinstance(found, models.Collaboration):
            raise models.Collaboration.DoesNotExist(
                f"Nothing was put in collaboration {key} here"
            )
        return found

    def _ensure(
        info: strawberry.Info, key: object, name: str | None
    ) -> models.Collaboration:
        try:
            parsed = uuid.UUID(str(key))
        except ValueError as e:
            raise ValueError(f"{key!r} is no collaboration key") from e
        return models.ensure(
            parsed, _organization(info), name=name, by=info.context.request.user
        )

    def _object(info: strawberry.Info, identifier: str, object_id: object) -> Model:
        from arkitekt_service.service.service import Service

        structure = Service.hosting(identifier)
        if structure is None:
            raise ValueError(f"No structure {identifier!r} is hosted here")
        return fetch(structure.model, object_id, info)

    def _visible(info: strawberry.Info) -> QuerySet[models.Collaboration]:
        # Narrowed for a guest *before* any page is cut: the gate drops what a guest may not see
        # only after the resolver ran, which would leave a page short.
        return object_scope(
            models.Collaboration.objects.filter(organization=_organization(info)),
            info=info,
        )

    def collaborations(
        info: strawberry.Info, pagination: OffsetPaginationInput | None = None
    ) -> list[Collaboration]:
        """The organization's collaborations (a guest: theirs), oldest first, each counting what is in it."""
        listed = (
            _visible(info)
            .annotate(
                **{
                    _ITEMS: Count("items"),
                    _PUT: Count("items", filter=Q(items__because_of__isnull=True)),
                }
            )
            .order_by("pk")
        )
        return list(_page(listed, pagination))  # type: ignore[return-value]

    def collaborations_stats(info: strawberry.Info) -> CollaborationStats:
        """How many collaborations there are, and objects in them, over what ``collaborations`` lists."""
        visible = _visible(info)
        return CollaborationStats(
            count=visible.count(),
            item_count=models.CollaborationItem.objects.filter(
                collaboration__in=visible.values("pk")
            ).count(),
        )

    def collaboration(info: strawberry.Info, key: strawberry.ID) -> Collaboration:
        """One collaboration of the organization, by its key: what was put in it here."""
        return object_scope(  # type: ignore[return-value]
            collaboration_rows(key, _organization(info)), info=info
        ).get()

    def add_to_collaboration(
        info: strawberry.Info, input: CollaborationObjectInput
    ) -> Collaboration:
        """Put an object in a collaboration, with what it needs to be seen."""
        with transaction.atomic():
            target = _ensure(info, input.collaboration, input.name)
            collaboration_items.add(
                target,
                _object(info, input.identifier, input.object),
                by=info.context.request.user,
            )
        return target  # type: ignore[return-value]

    def add_many_to_collaboration(
        info: strawberry.Info, input: AddManyToCollaborationInput
    ) -> Collaboration:
        """Put several objects in a collaboration at once, in the order given (a whole before its
        parts), each with what it needs to be seen. All of them, or none."""
        seen: set[tuple[str, str]] = set()
        refs = []
        for ref in input.objects:
            if (ref.identifier, str(ref.object)) not in seen:
                seen.add((ref.identifier, str(ref.object)))
                refs.append(ref)
        with transaction.atomic():
            target = _ensure(info, input.collaboration, input.name)
            for ref in refs:
                collaboration_items.add(
                    target,
                    _object(info, ref.identifier, ref.object),
                    by=info.context.request.user,
                )
        return target  # type: ignore[return-value]

    def share_workflow(
        info: strawberry.Info, input: ShareWorkflowInput
    ) -> WorkflowShare:
        """Put what a workflow created here in a collaboration, as it is now: what it makes later
        is not. A part of something made before the workflow, and not shared, is left out."""
        if input.dry_run:
            found = collaboration_rows(input.collaboration, _organization(info)).first()
            target = (
                found
                if isinstance(found, models.Collaboration)
                else models.Collaboration(
                    key=uuid.UUID(str(input.collaboration)),
                    organization=_organization(info),
                )
            )
            share = collaboration_items.share_workflow(
                target, str(input.root_task), dry_run=True
            )
            return _report(input.collaboration, True, share)
        with transaction.atomic():
            target = _ensure(info, input.collaboration, input.name)
            share = collaboration_items.share_workflow(
                target, str(input.root_task), by=info.context.request.user
            )
        return _report(target.key, False, share)

    def remove_from_collaboration(
        info: strawberry.Info, input: CollaborationObjectInput
    ) -> Collaboration:
        """Take an object out of a collaboration, with what it brought that nothing else there needs."""
        target = _collaboration(info, input.collaboration)
        collaboration_items.remove(
            target, _object(info, input.identifier, input.object)
        )
        return target  # type: ignore[return-value]

    def delete_collaboration(
        info: strawberry.Info, key: strawberry.ID
    ) -> strawberry.ID:
        """Take everything this service put in a collaboration out of it: whoever was granted it
        sees nothing of it here any more (lok's collaboration, and the grants, stay)."""
        _collaboration(info, key).delete()
        return key

    return Resolvers(
        collaborations,
        collaboration,
        collaborations_stats,
        add_to_collaboration,
        add_many_to_collaboration,
        share_workflow,
        remove_from_collaboration,
        delete_collaboration,
    )


__all__ = [
    "AddManyToCollaborationInput",
    "Collaboration",
    "CollaborationItem",
    "CollaborationObjectInput",
    "CollaborationObjectRef",
    "CollaborationStats",
    "Resolvers",
    "ShareWorkflowInput",
    "SharedObject",
    "SkippedObject",
    "WorkflowShare",
    "resolvers",
]
