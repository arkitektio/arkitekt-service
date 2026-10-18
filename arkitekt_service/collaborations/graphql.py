"""The collaborations of a service, over its GraphQL: types, and resolvers it mounts with its own rules.

::

    from arkitekt_service.collaborations import graphql as collaboration_types

    api = collaboration_types.resolvers(fetch=lambda model, id, info: get_for_org(model, info, id=id))

    @kante.type
    class Query:
        collaborations: list[collaboration_types.Collaboration] = kante.django_field(resolver=api.collaborations, extensions=[AuthExtension()])

    @kante.type
    class Mutation:
        create_collaboration: collaboration_types.Collaboration = kante.django_mutation(resolver=api.create_collaboration, extensions=[AuthExtension(any_role_of=["admin", "editor"])])
        add_to_collaboration: collaboration_types.Collaboration = kante.django_mutation(resolver=api.add_to_collaboration, extensions=[...])

An object is named by its structure's identifier and its id (``@mikro/scene``, ``7``); ``fetch``
loads it, held to the request's organization. A guest sees the collaborations granted to them and what is
in them (the gate holds them to it); making and changing collaborations is for full members, whom the
service's own ``AuthExtension`` on each mutation lets in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import strawberry
import strawberry_django

from arkitekt_service.collaborations import items as collaboration_items
from arkitekt_service.collaborations import models


def _identifier(model: Any) -> str | None:
    from arkitekt_service.service.service import Service

    structure = Service.hosting(model) if model is not None else None
    return structure.identifier if structure is not None else None


@strawberry_django.type(models.CollaborationItem, description="One object in a collaboration: put there, or brought along with one that needs it to be seen.")
class CollaborationItem:
    id: strawberry.ID
    object_id: str = strawberry_django.field(description="The object's id, in its structure")
    added_at: strawberry.auto

    @strawberry_django.field(description="The structure the object is known by across the hub: @mikro/scene")
    def identifier(self) -> str | None:
        return _identifier(self.content_type.model_class())  # type: ignore[attr-defined]

    @strawberry_django.field(description="Whether it is in the collaboration only as what another object in it needs to be seen")
    def brought(self) -> bool:
        return self.because_of_id is not None  # type: ignore[attr-defined]


@strawberry_django.type(models.Collaboration, description="What a guest granted it sees of the organization: the objects put in it, and what is part of them.")
class Collaboration:
    id: strawberry.ID
    name: str
    description: str | None
    created_at: strawberry.auto

    @strawberry_django.field(description="What is in the collaboration; what is part of these is in it too")
    def items(self) -> list[CollaborationItem]:
        return list(models.CollaborationItem.objects.filter(collaboration=self).select_related("content_type").order_by("added_at", "pk"))  # type: ignore[return-value]


@strawberry.input(description="An object to put in, or take out of, a collaboration")
class CollaborationObjectInput:
    collaboration: strawberry.ID
    identifier: str = strawberry.field(description="The structure the object is known by: @mikro/scene")
    object: strawberry.ID = strawberry.field(description="The object's id")


@strawberry.input(description="A new collaboration")
class CreateCollaborationInput:
    name: str
    description: str | None = None


Fetch = Callable[[Any, Any, Any], Any]


@dataclass(frozen=True)
class Resolvers:
    """The resolvers of a service's collaborations, each to be mounted with the service's own extensions."""

    collaborations: Callable[..., list[Collaboration]]
    collaboration: Callable[..., Collaboration]
    create_collaboration: Callable[..., Collaboration]
    add_to_collaboration: Callable[..., Collaboration]
    remove_from_collaboration: Callable[..., Collaboration]
    delete_collaboration: Callable[..., strawberry.ID]


def resolvers(fetch: Fetch) -> Resolvers:
    """Resolvers for a service's collaborations; ``fetch(model, id, info)`` loads an object held to the request's organization."""

    def _organization(info: Any) -> Any:
        return info.context.request.organization

    def _collaboration(info: Any, id: Any) -> models.Collaboration:
        return models.Collaboration.objects.get(pk=id, organization=_organization(info))

    def _object(info: Any, identifier: str, object_id: Any) -> Any:
        from arkitekt_service.service.service import Service

        structure = Service.hosting(identifier)
        if structure is None:
            raise ValueError(f"No structure {identifier!r} is hosted here")
        return fetch(structure.model, object_id, info)

    def collaborations(info: strawberry.Info) -> list[Collaboration]:
        """The organization's collaborations (a guest: theirs)."""
        return list(models.Collaboration.objects.filter(organization=_organization(info)).order_by("pk"))  # type: ignore[return-value]

    def collaboration(info: strawberry.Info, id: strawberry.ID) -> Collaboration:
        """One collaboration of the organization."""
        return _collaboration(info, id)  # type: ignore[return-value]

    def create_collaboration(info: strawberry.Info, input: CreateCollaborationInput) -> Collaboration:
        """Make an empty collaboration."""
        return models.Collaboration.objects.create(organization=_organization(info), name=input.name, description=input.description, created_by=info.context.request.user)  # type: ignore[return-value]

    def add_to_collaboration(info: strawberry.Info, input: CollaborationObjectInput) -> Collaboration:
        """Put an object in a collaboration, with what it needs to be seen."""
        target = _collaboration(info, input.collaboration)
        collaboration_items.add(target, _object(info, input.identifier, input.object), by=info.context.request.user)
        return target  # type: ignore[return-value]

    def remove_from_collaboration(info: strawberry.Info, input: CollaborationObjectInput) -> Collaboration:
        """Take an object out of a collaboration, with what it brought that nothing else there needs."""
        target = _collaboration(info, input.collaboration)
        collaboration_items.remove(target, _object(info, input.identifier, input.object))
        return target  # type: ignore[return-value]

    def delete_collaboration(info: strawberry.Info, id: strawberry.ID) -> strawberry.ID:
        """Delete a collaboration: whoever was granted it sees nothing of it any more."""
        _collaboration(info, id).delete()
        return id

    return Resolvers(collaborations, collaboration, create_collaboration, add_to_collaboration, remove_from_collaboration, delete_collaboration)


__all__ = ["Collaboration", "CollaborationItem", "CollaborationObjectInput", "CreateCollaborationInput", "Resolvers", "resolvers"]
