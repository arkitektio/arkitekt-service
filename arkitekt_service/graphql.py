"""What a service's GraphQL schema says of its structures, and the gate that holds a guest to their collaborations.

Once its schema is built, a service says::

    from arkitekt_service.graphql import CollaborationGate, annotate_schema

    schema = kante.Schema(query=Query, mutation=Mutation, extensions=[..., AuthentikateExtension, CollaborationGate])
    annotate_schema(schema, service)

and its SDL carries ``@identified(by: "@mikro/scene")`` on each type of a hosted structure's model.
Behind the gate (:class:`CollaborationGate`), a guest (a member granted collaborations only,
:mod:`arkitekt_service.collaborations`):

- enters only at a root field that returns objects (a model's type, or an interface or union of
  them): one returning a value computed from many (a lineage graph) is refused before anything
  runs (``COLLABORATION_DOES_NOT_REACH``), unless it opted in (``AuthExtension(allow_scoped=True)``);
- is handed only what is in their collaborations: an object that is not becomes null, and is dropped from
  a list (a non-null field errors at its path). Who made, runs or owns an object they are handed
  (authentikate's users, clients and organizations, :data:`IDENTITIES`) comes with it, though it is
  in no collaboration; at a root field it is not handed out.

``annotate_schema`` says the service shares (``Service.share``) when the schema is behind the
gate: its guests are let in. Needs strawberry and authentikate >= 5.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from inspect import isawaitable
from typing import Any

import strawberry
from django.db.models import Model, QuerySet
from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLObjectType,
    GraphQLSchema,
    InlineFragmentNode,
    OperationDefinitionNode,
    OperationType,
    SelectionSetNode,
    get_named_type,
    is_leaf_type,
)
from strawberry.extensions import SchemaExtension
from strawberry.schema_directive import Location

DEFINITION = "strawberry-definition"

#: The reason a root field a guest may not enter at is refused with.
COLLABORATION_DOES_NOT_REACH = "COLLABORATION_DOES_NOT_REACH"


@strawberry.schema_directive(locations=[Location.OBJECT], name="identified")
class Identified:
    """The structure this type's objects are known by across the hub: ``@mikro/scene``."""

    by: str


def _model_of(graphql_type: Any) -> Any:
    """The Django model a strawberry-django type is made from, if any."""
    definition = (getattr(graphql_type, "extensions", None) or {}).get(DEFINITION)
    django = getattr(getattr(definition, "origin", None), "__strawberry_django_definition__", None)
    return getattr(django, "model", None)


def _declare_catch_alls() -> None:
    """Tell authentikate which of the contract's scopes and roles stand for all of this service."""
    from authentikate.catch_alls import declare_catch_alls

    from arkitekt_service.contract.contract import load

    try:
        said = load().description
    except LookupError:
        # A process that is no service of a hub's contract (a test project): nothing stands for all.
        return
    declare_catch_alls(scope=said.all_scope, role=said.admin_role)


def scope_gaps(schema: Any) -> list[str]:
    """The root fields of ``schema`` that ask for no scope, or for one the contract does not declare.

    Every query, mutation and subscription says which scope an app needs for it
    (``AuthExtension(scopes=...)``): that is what ``@auth(required_scopes: ...)`` publishes, and what a
    generated client gathers into the scopes its apps ask for. Empty when each does.
    """
    from authentikate.strawberry.directives import Auth

    from arkitekt_service.contract.contract import load

    declared = {scope.key for scope in load().description.needs.scopes}
    core: GraphQLSchema = schema._schema
    gaps: list[str] = []
    for root in (core.query_type, core.mutation_type, core.subscription_type):
        if root is None:
            continue
        for name, field in root.fields.items():
            if name.startswith("_"):
                continue
            definition = (field.extensions or {}).get(DEFINITION)
            auths = [d for d in getattr(definition, "directives", None) or () if isinstance(d, Auth)]
            required = [scope for auth in auths for scope in (auth.required_scopes or ())]
            if not required:
                gaps.append(f"{root.name}.{name} asks for no scope")
            gaps.extend(f"{root.name}.{name} asks for `{scope}`, which the contract does not declare" for scope in required if scope not in declared)
    return gaps


def annotate_schema(schema: Any, service: Any) -> Any:
    """Say on ``schema`` which structure each type is (``@identified``); behind the gate, say
    ``service`` lets guests in; tell authentikate the contract's catch-all scope and role.
    Returns the schema."""
    _declare_catch_alls()
    core: GraphQLSchema = schema._schema
    for graphql_type in core.type_map.values():
        if not isinstance(graphql_type, GraphQLObjectType) or graphql_type.name.startswith("__"):
            continue
        model, definition = _model_of(graphql_type), (graphql_type.extensions or {}).get(DEFINITION)
        if model is None or definition is None:
            continue
        structure = service.structure_for(model)
        if structure is None or any(isinstance(d, Identified) for d in definition.directives or ()):
            continue
        definition.directives = [*(definition.directives or ()), Identified(by=structure.identifier)]
    extensions = getattr(schema, "extensions", ()) or ()
    if any(e is CollaborationGate or (isinstance(e, type) and issubclass(e, CollaborationGate)) or isinstance(e, CollaborationGate) for e in extensions):
        service.share()
    return schema


# --- the gate ----------------------------------------------------------------------------------


def _relates(schema: GraphQLSchema, graphql_type: Any) -> bool:
    """Whether a field returning ``graphql_type`` hands out objects: a model's type, or an interface or union of one."""
    if is_leaf_type(graphql_type):
        return False
    if isinstance(graphql_type, GraphQLObjectType):
        return _model_of(graphql_type) is not None
    return any(_model_of(possible) is not None for possible in schema.get_possible_types(graphql_type))


def _opted_in(field: Any) -> bool:
    definition = (field.extensions or {}).get(DEFINITION)
    return any(getattr(e, "allow_scoped", False) for e in getattr(definition, "extensions", None) or ())


def _root_fields(selection_set: SelectionSetNode, fragments: dict[str, FragmentDefinitionNode], seen: frozenset[str] = frozenset()) -> Iterator[FieldNode]:
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            yield selection
        elif isinstance(selection, InlineFragmentNode):
            yield from _root_fields(selection.selection_set, fragments, seen)
        elif isinstance(selection, FragmentSpreadNode) and selection.name.value not in seen:
            fragment = fragments.get(selection.name.value)
            if fragment is not None:
                yield from _root_fields(fragment.selection_set, fragments, seen | {selection.name.value})


def closed_roots(schema: GraphQLSchema, document: Any, operation_name: str | None) -> list[str]:
    """The root fields of a query a guest may not enter at: those handing out no objects, not opted in."""
    operation = next(
        (d for d in document.definitions if isinstance(d, OperationDefinitionNode) and (operation_name is None or (d.name is not None and d.name.value == operation_name))),
        None,
    )
    if operation is None or operation.operation != OperationType.QUERY or schema.query_type is None:
        return []
    fragments = {d.name.value: d for d in document.definitions if isinstance(d, FragmentDefinitionNode)}
    closed = []
    for node in _root_fields(operation.selection_set, fragments):
        name = node.name.value
        field = schema.query_type.fields.get(name)
        if name.startswith("__") or name in ("_service", "_entities") or field is None:
            continue
        if not _relates(schema, get_named_type(field.type)) and not _opted_in(field):
            closed.append(name)
    return closed


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


#: authentikate's records of who someone is. Never in a collaboration (put in, an organization
#: would bring all of it, a user all they made), they are shown where what they are attached to is.
IDENTITIES = frozenset({"user", "client", "organization"})


def _identity(item: Any) -> bool:
    meta = type(item)._meta
    return meta.app_label == "authentikate" and meta.model_name in IDENTITIES


def _shown(item: Model, guest: Any, parent: Any) -> bool:
    """Whether the guest is handed ``item``: in their collaborations, or who made, runs or owns what is
    (a room's creator, an agent's user). An identity is never handed out at a root field, nor through
    another identity: a creator's active organization is theirs, not the collaboration's."""
    if not _identity(item):
        return guest.sees(item)
    return isinstance(parent, Model) and not _identity(parent) and guest.sees(parent)


def _held(value: Any, guest: Any, parent: Any = None) -> Any:
    """``value`` with what is not in the guest's collaborations taken out. Reads the database: call it sync."""
    if isinstance(value, Model):
        return value if _shown(value, guest, parent) else None
    if isinstance(value, QuerySet):
        value = list(value)
    if isinstance(value, (list, tuple)):
        return [item for item in value if not isinstance(item, Model) or _shown(item, guest, parent)]
    return value


def _hands_out_objects(value: Any) -> bool:
    if isinstance(value, (Model, QuerySet)):
        return True
    return isinstance(value, (list, tuple)) and any(isinstance(item, Model) for item in value)


def _known(value: Any, guest: Any, parent: Any = None) -> bool:
    """Whether holding ``value`` reads nothing: every model in it (or, for an identity, what it is attached to) was read for the guest already."""
    if isinstance(value, QuerySet):
        return False
    items = [item for item in (value if isinstance(value, (list, tuple)) else [value]) if isinstance(item, Model)]
    if any(_identity(item) for item in items) and isinstance(parent, Model) and not _identity(parent) and not guest.knows(type(parent)):
        return False
    return all(guest.knows(type(item)) for item in items if not _identity(item))


def _after_a_write(info: Any, guest: Any) -> None:
    """What a mutation wrote may be part of a collaboration now (an annotation drawn in it): read again."""
    raw = getattr(info, "_raw_info", info)
    if guest is not None and getattr(raw, "parent_type", None) is getattr(getattr(raw, "schema", None), "mutation_type", False):
        guest.forget()


class CollaborationGate(SchemaExtension):
    """Hold a guest's operation to their collaborations; a full member's passes untouched.

    After authentikate's extension (it reads the request's token). A root field a guest may not
    enter at is refused as validation errors, as authentikate's own refusals are.
    """

    def on_operation(self) -> Iterator[None]:
        from arkitekt_service.collaborations.guests import current_token, serve, served

        reset = serve(current_token())
        try:
            yield
        finally:
            served(reset)

    def on_validate(self) -> Iterator[None]:
        from arkitekt_service.collaborations.guests import current_token

        context = self.execution_context
        token = current_token()
        if token is not None and token.is_scoped and context.graphql_document is not None:
            closed = closed_roots(context.schema._schema, context.graphql_document, context.operation_name)
            if closed:
                from authentikate.strawberry.errors import denied

                context.pre_execution_errors = [
                    *(context.pre_execution_errors or []),
                    *(denied(f"Nothing in your collaborations is reached through {name}", COLLABORATION_DOES_NOT_REACH, field=name) for name in closed),
                ]
        yield

    def resolve(self, _next: Any, root: Any, info: Any, *args: Any, **kwargs: Any) -> Any:
        result = _next(root, info, *args, **kwargs)
        from arkitekt_service.collaborations.guests import current_token, guest_of

        token = current_token()
        if token is None or not token.is_scoped:
            return result
        if isawaitable(result):
            return self._aheld(result, root, info, token)
        if not _hands_out_objects(result):
            return result
        guest = guest_of(info, token)
        _after_a_write(info, guest)
        if _in_loop() and not _known(result, guest, root):
            return self._aheld(result, root, info, token)
        return _held(result, guest, root)

    async def _aheld(self, result: Any, root: Any, info: Any, token: Any) -> Any:
        from asgiref.sync import sync_to_async

        from arkitekt_service.collaborations.guests import guest_of

        value = await result if isawaitable(result) else result
        if not _hands_out_objects(value):
            return value
        guest = guest_of(info, token)
        _after_a_write(info, guest)
        if _known(value, guest, root):
            return _held(value, guest, root)
        return await sync_to_async(lambda: _held(value, guest, root))()


__all__ = ["COLLABORATION_DOES_NOT_REACH", "IDENTITIES", "CollaborationGate", "Identified", "annotate_schema", "closed_roots"]
