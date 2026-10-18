"""What a service's GraphQL schema says about sharing: which share reaches each type and field.

Once its schema is built, a service that shares says::

    from arkitekt_service.graphql import annotate_schema

    schema = kante.Schema(query=Query, mutation=Mutation, ...)
    annotate_schema(schema)

and its SDL carries authentikate's ``@shared(by: [...])``, in grant notation:

- on each type of a model a share reveals: the grants that do, ``@mikro/scene`` (the object
  itself, and what is part of it) or ``@mikro/arraydataset#derived``;
- on each list field of a shared structure's own type: the least reaches of the parent's share
  that reveal its rows, ``#self`` or ``#derived``, and ``[]`` when none does.

A to-one field comes with its row and is left unsaid. Mutations say which shares permit them
themselves (``AuthExtension(shared_by=...)``). Needs strawberry and authentikate >= 4.4.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from arkitekt_service.service.structures import SELF_REACH, Structure

DEFINITION = "strawberry-definition"


def _model_of(graphql_type: Any) -> Any:
    """The Django model a strawberry-django type is made from, if any."""
    definition = (getattr(graphql_type, "extensions", None) or {}).get(DEFINITION)
    django = getattr(getattr(definition, "origin", None), "__strawberry_django_definition__", None)
    return getattr(django, "model", None)


def _says_shared(directives: Iterable[Any] | None, shared: type) -> bool:
    return any(isinstance(directive, shared) for directive in directives or ())


def grants_revealing(model: Any, structures: Iterable[Structure]) -> list[str]:
    """The grants, ``<structure>[#<reach>]``, whose share shows ``model``'s rows."""
    found: list[str] = []
    for structure in structures:
        for reach in structure.revealing(model):
            found.append(structure.identifier if reach == SELF_REACH else f"{structure.identifier}#{reach}")
    return found


def annotate_schema(schema: Any, structures: Iterable[Structure] | None = None, *, directive: Any = None) -> Any:
    """Add ``@shared`` to the types and list fields of ``schema`` that a share reaches. Returns it.

    ``structures`` defaults to every structure of a service that shares (``Service.sharing``);
    ``directive`` to authentikate's ``Shared`` (a schema directive taking ``by``).
    """
    from graphql import GraphQLList, GraphQLNonNull, GraphQLObjectType, get_named_type

    if directive is None:
        from authentikate.strawberry.directives import Shared as directive
    Shared = directive

    from arkitekt_service.sharing import shared_structures

    declared = list(structures) if structures is not None else list(shared_structures().values())
    if not declared:
        return schema

    for graphql_type in schema._schema.type_map.values():
        if not isinstance(graphql_type, GraphQLObjectType) or graphql_type.name.startswith("__"):
            continue
        model = _model_of(graphql_type)
        definition = (graphql_type.extensions or {}).get(DEFINITION)
        if model is None or definition is None:
            continue

        by = grants_revealing(model, declared)
        if by and not _says_shared(definition.directives, Shared):
            definition.directives = [*(definition.directives or ()), Shared(by=by)]

        roots = [s for s in declared if isinstance(s.model, type) and issubclass(model, s.model)]
        if not roots:
            continue
        for field in graphql_type.fields.values():
            outer = field.type.of_type if isinstance(field.type, GraphQLNonNull) else field.type
            target = _model_of(get_named_type(field.type))
            strawberry_field = (field.extensions or {}).get(DEFINITION)
            if not isinstance(outer, GraphQLList) or target is None or strawberry_field is None:
                continue
            if _says_shared(strawberry_field.directives, Shared):
                continue
            reaches = sorted({f"#{reach}" for root in roots for reach in root.revealing(target)})
            strawberry_field.directives.append(Shared(by=reaches))
    return schema


__all__ = ["annotate_schema", "grants_revealing"]
