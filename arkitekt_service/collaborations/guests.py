"""A guest's request: the collaborations granted to them here, and what is in them.

A guest's token lists their collaborations by service instance, with the roles they hold in each
(authentikate's ``collabs``). Here that is a :class:`Guest`, one per request: which of those
collaborations are this organization's, and, per model, which rows are in which of them — put in
(:mod:`.items`), or part of something put in (:func:`~arkitekt_service.service.structures.part_of`,
live). Every read is held to it:

- at a root field (or the federation's ``_entities``), the queryset is narrowed to it
  (:func:`object_scope`, from the service's scoping chokepoint);
- below, whatever a resolver hands out that is not in a collaboration is dropped (the gate in
  :mod:`arkitekt_service.graphql`);
- a write checks its target (:func:`require_in_collaboration`), with the roles the guest holds in a
  collaboration that holds it.

The instance a token's collaborations are read for is this service's ``AUTHENTIKATE.audience``
(rendered by konstruktor): a service whose audience is ``*`` serves no guests.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from contextvars import ContextVar, Token
from functools import cache
from typing import Any

from django.db.models import CASCADE, BigIntegerField, ForeignKey, Model, Q, QuerySet
from django.db.models.functions import Cast

logger = logging.getLogger(__name__)

#: How many required foreign keys :func:`wholes` follows from a row to what it is part of.
PART_OF_DEPTH = 3

#: The guest of the operation being served (set by the gate, ``arkitekt_service.graphql.CollaborationGate``).
_operation: ContextVar[Guest | None] = ContextVar("arkitekt_guest", default=None)

#: Bumped whenever anything is put in or taken out of a collaboration: a guest reads again what it read before.
_changes = [0]


def changed(**_: Any) -> None:
    """Note that what is in some collaboration changed (connected to the saves and deletes of collaboration items)."""
    _changes[0] += 1


def enforce() -> None:
    """Tell authentikate this process holds a guest's reads to their collaborations, so it serves their tokens."""
    try:
        from authentikate.scoped import enforce_scoped
    except ImportError:
        logger.warning("A service lets guests in, but authentikate>=5 is not installed: they stay shut out")
        return
    enforce_scoped()


@cache
def _authentikate() -> bool:
    from django.apps import apps

    return apps.is_installed("authentikate")


def current_token() -> Any:
    """The token the request being served was authenticated with, if authentikate holds one."""
    if not _authentikate():
        return None
    from authentikate.vars import token_var

    return token_var.get()


def current_instance() -> str | None:
    """This service instance, whose entry of a guest's ``collabs`` is read."""
    from authentikate.scoped import current_instance as configured
    from authentikate.settings import get_settings

    return configured(get_settings())


# --- what is part of what --------------------------------------------------------------------


def _family(model: type) -> list[type]:
    """``model`` and the models it shares rows with (multi-table inheritance: its parents and children)."""
    found = [model, *(parent for parent in model._meta.get_parent_list())]
    frontier = list(model.__subclasses__())
    while frontier:
        child = frontier.pop()
        if getattr(child, "_meta", None) is not None and not child._meta.abstract and child not in found:
            found.append(child)
            frontier.extend(child.__subclasses__())
    return [m for m in found if not m._meta.abstract and not m._meta.proxy]


#: Cascading foreign keys that point at something shared rather than at a whole: ``(Layer, "lens")``.
_references: set[tuple[type, str]] = set()


def not_part_of(model: type, *fields: str) -> None:
    """Say that ``model``'s rows are not part of what these cascading foreign keys point at: a layer
    dies with the lens it draws through, but a lens is drawn by many scenes, and a layer is its scene's."""
    _references.update((model, name) for name in fields)
    wholes.cache_clear()


def _is_reference(model: type, name: str) -> bool:
    return any((kind, name) in _references for kind in (model, *model.__mro__))


def _cascades(field: ForeignKey) -> bool:
    """Whether deleting what ``field`` points at deletes its row (seen through django-polymorphic's guard)."""
    action = field.remote_field.on_delete
    return getattr(action, "action", action) is CASCADE


@cache
def wholes(model: type, depth: int = PART_OF_DEPTH) -> tuple[tuple[type, str], ...]:
    """What ``model``'s rows are part of: each model reached through required, cascading foreign
    keys, with the lookup to it (``(Scene, "scene")``, ``(Scene, "collection__scene")``)."""
    if depth == 0 or not hasattr(model, "_meta"):
        return ()
    found: list[tuple[type, str]] = []
    for field in model._meta.get_fields():
        if not isinstance(field, ForeignKey) or field.null or field.related_model in (None, model):
            continue
        if not _cascades(field) or _is_reference(model, field.name):
            continue
        related = field.related_model
        found.append((related, field.name))
        found.extend((whole, f"{field.name}__{path}") for whole, path in wholes(related, depth - 1))
    return tuple(found)


def _types_q(models: Iterable[type]) -> Q:
    condition = Q(pk__in=[])
    for model in models:
        condition |= Q(content_type__app_label=model._meta.app_label, content_type__model=model._meta.model_name)
    return condition


# --- a guest -----------------------------------------------------------------------------------


class Guest:
    """One request's guest: their collaborations here, the roles they hold in each, and what is in them."""

    def __init__(self, token: Any, organization: Any) -> None:
        self.token = token
        self.organization = organization
        granted = token.collabs_here(current_instance()) if token is not None else {}
        #: The roles held in each collaboration granted, by collaboration id (collaborations of another organization hold nothing).
        self.roles: dict[int, frozenset[str]] = {int(collaboration): frozenset(roles) for collaboration, roles in granted.items() if str(collaboration).isdigit()}
        self._rows: dict[type, dict[str, frozenset[int]]] = {}
        self._held: dict[type, dict[str, set[int]]] | None = None
        self._read_at = _changes[0]

    def _fresh(self) -> None:
        """Forget what was read when something was put in or taken out of a collaboration since."""
        if self._read_at != _changes[0]:
            self.forget()

    def forget(self) -> None:
        """Read again what was read: after a write (a mutation), what is part of a collaboration may have grown."""
        self._rows, self._held, self._read_at = {}, None, _changes[0]

    def _collaborations(self) -> Any:
        from arkitekt_service.collaborations.models import Collaboration

        return Collaboration.objects.filter(pk__in=list(self.roles), organization=self.organization)

    def _items(self, models: Iterable[type]) -> QuerySet[Any]:
        from arkitekt_service.collaborations.models import CollaborationItem

        return CollaborationItem.objects.filter(_types_q(models), collaboration__in=self._collaborations())

    def _ids(self, model: type) -> QuerySet[Any]:
        """The ids of ``model``'s rows put in the guest's collaborations, as a subquery (``object_id`` cast to the key)."""
        pk = model._meta.pk
        output = pk.target_field if isinstance(pk, ForeignKey) else pk
        if output.get_internal_type() in ("AutoField", "BigAutoField", "SmallAutoField"):
            output = BigIntegerField()
        return self._items(_family(model)).annotate(key=Cast("object_id", output_field=output)).values("key")

    def q(self, model: type) -> Q:
        """``model``'s rows in the guest's collaborations, as a condition: nothing is read until it is used."""
        from arkitekt_service.collaborations.models import Collaboration, CollaborationItem

        if issubclass(model, Collaboration):
            return Q(pk__in=self._collaborations().values("pk"))
        if issubclass(model, CollaborationItem):
            return Q(collaboration__in=self._collaborations())
        condition = Q(pk__in=self._ids(model))
        for whole, path in wholes(model):
            condition |= Q(**{f"{path}__in": self._ids(whole)})
        return condition

    def _all_items(self) -> dict[type, dict[str, set[int]]]:
        """What was put in the guest's collaborations, by model, then object id, with the collaborations holding it. Read once."""
        self._fresh()
        if self._held is None:
            from django.contrib.contenttypes.models import ContentType

            from arkitekt_service.collaborations.models import CollaborationItem

            held: dict[type, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
            for content_type, collaboration, object_id in CollaborationItem.objects.filter(collaboration__in=self._collaborations()).values_list("content_type_id", "collaboration_id", "object_id"):
                model = ContentType.objects.get_for_id(content_type).model_class()
                if model is not None:
                    held[model][object_id].add(collaboration)
            self._held = held
        return self._held

    def _put_in(self, models: Iterable[type]) -> dict[str, set[int]]:
        found: dict[str, set[int]] = defaultdict(set)
        for model in models:
            for object_id, collaborations in self._all_items().get(model, {}).items():
                found[object_id] |= collaborations
        return found

    def rows(self, model: type) -> dict[str, frozenset[int]]:
        """``model``'s rows in the guest's collaborations, by pk, with the collaborations holding each. Read once per model."""
        from arkitekt_service.collaborations.models import Collaboration, CollaborationItem

        self._fresh()
        if model in self._rows:
            return self._rows[model]
        held: dict[str, set[int]] = defaultdict(set)
        if issubclass(model, Collaboration):
            for pk in self._collaborations().values_list("pk", flat=True):
                held[str(pk)].add(pk)
        elif issubclass(model, CollaborationItem):
            for pk, collaboration in CollaborationItem.objects.filter(collaboration__in=self._collaborations()).values_list("pk", "collaboration_id"):
                held[str(pk)].add(collaboration)
        else:
            family = _family(model)
            for object_id, collaborations in self._put_in(family).items():
                held[object_id] |= collaborations
            # What is part of something in a collaboration: of ``model``, or of a kind of it (a lens of a dataset).
            for kind in (m for m in family if issubclass(m, model)):
                for whole, path in wholes(kind):
                    collaborations_of = self._put_in(_family(whole))
                    if not collaborations_of:
                        continue
                    for pk, root in kind._base_manager.filter(**{f"{path}__in": self._ids(whole)}).values_list("pk", path):
                        held[str(pk)] |= collaborations_of.get(str(root), set())
        self._rows[model] = {pk: frozenset(collaborations) for pk, collaborations in held.items()}
        return self._rows[model]

    def knows(self, model: type) -> bool:
        """Whether ``model``'s rows were read already: :meth:`sees` then reads nothing."""
        return self._read_at == _changes[0] and model in self._rows

    def sees(self, obj: Model) -> bool:
        """Whether ``obj`` is in one of the guest's collaborations."""
        return str(obj.pk) in self.rows(type(obj))

    def holds(self, obj: Model, *, roles: Iterable[str] = (), any_role_of: Iterable[str] = ()) -> bool:
        """Whether ``obj`` is in a collaboration of the guest's where they hold ``roles`` (all) and one of ``any_role_of``."""
        every, one_of = set(roles), set(any_role_of)
        for collaboration in self.rows(type(obj)).get(str(obj.pk), ()):
            held = self.roles.get(collaboration, frozenset())
            if every <= held and (not one_of or one_of & held):
                return True
        return False


def guest_of(info: Any = None, token: Any = None) -> Guest | None:
    """The guest of the operation being served (the gate keeps one per operation); None for a full
    member, or no token. Outside an operation the gate serves, a new one, reading afresh."""
    token = current_token() if token is None else token
    if token is None or not token.is_scoped:
        return None
    kept = _operation.get()
    if kept is not None and kept.token is token:
        return kept
    request = getattr(getattr(info, "context", None), "request", None)
    organization = getattr(request, "_organization", None) if request is not None else None
    if organization is None:
        from authentikate.vars import organization_var

        organization = organization_var.get()
    return Guest(token, organization)


def serve(token: Any) -> Token[Guest | None] | None:
    """Keep one guest for the operation about to be served with ``token``; :func:`served` ends it."""
    if token is None or not token.is_scoped:
        return None
    from authentikate.vars import organization_var

    return _operation.set(Guest(token, organization_var.get()))


def served(reset: Token[Guest | None] | None) -> None:
    """End the operation :func:`serve` began."""
    if reset is None:
        return
    try:
        _operation.reset(reset)
    except ValueError:  # closed from another context (a WebSocket gone): nothing of it is left here
        pass


# --- reading and writing -----------------------------------------------------------------------


def at_root(info: Any) -> bool:
    """Whether ``info`` is that of a field an operation enters at (a root field, ``_entities``),
    rather than one below it. A queryset strawberry-django prefetches for a nested field carries
    the root's resolve info, but the nested field (``Info._field``): that one is below."""
    raw = getattr(info, "_raw_info", info)
    parent, schema = getattr(raw, "parent_type", None), getattr(raw, "schema", None)
    if parent is None or schema is None:
        return True  # nothing says it is nested: narrow
    if parent not in (schema.query_type, schema.mutation_type, schema.subscription_type):
        return False
    field = getattr(info, "_field", None)
    entered = parent.fields.get(getattr(raw, "field_name", ""))
    definition = (entered.extensions or {}).get("strawberry-definition") if entered is not None else None
    return field is None or definition is None or definition is field


def object_scope(queryset: QuerySet[Any], token: Any = None, *, info: Any = None) -> QuerySet[Any]:
    """``queryset``, held to the request's guest's collaborations when it is a guest's.

    A full member's (or no token: work outside a request) is returned as it is. Given the resolve
    ``info``, only a root field's queryset is narrowed (:func:`at_root`): below one, what a resolver
    hands out is held by the gate, and the optimizer's plan stays a full member's. Reads nothing
    by itself: the condition is a subquery.
    """
    guest = guest_of(info, token)
    if guest is None:
        return queryset
    if info is not None and not at_root(info):
        return queryset
    return queryset.filter(guest.q(queryset.model))


def kante_narrow(queryset: QuerySet[Any], info: Any) -> QuerySet[Any]:
    """:func:`object_scope` as kante's narrowing step (``KANTE_OBJECT_SCOPE``)."""
    try:
        token = info.context.request.get_extension("token")
    except (AttributeError, KeyError, ValueError):
        token = None
    return object_scope(queryset, token, info=info)


def require_in_collaboration(obj: Model, *, any_role_of: Iterable[str] | None = None, roles: Iterable[str] | None = None, info: Any = None, token: Any = None) -> None:
    """Refuse a guest's write to ``obj`` unless it is in a collaboration of theirs where they hold the roles
    (``roles``, all; ``any_role_of``, one). A full member passes: their own roles were checked.
    Raises authentikate's ``ShareInsufficient`` (a 403)."""
    guest = guest_of(info, token)
    if guest is None:
        return
    if not guest.holds(obj, roles=roles or (), any_role_of=any_role_of or ()):
        from authentikate.errors import ShareInsufficient

        raise ShareInsufficient(f"{type(obj).__name__} {obj.pk} is in no collaboration of {getattr(guest.token, 'sub', '?')}'s that permits this")


async def arequire_in_collaboration(obj: Model, **kwargs: Any) -> None:
    """:func:`require_in_collaboration`, for an async resolver."""
    from asgiref.sync import sync_to_async

    await sync_to_async(require_in_collaboration)(obj, **kwargs)


__all__ = [
    "Guest",
    "arequire_in_collaboration",
    "at_root",
    "current_instance",
    "current_token",
    "enforce",
    "guest_of",
    "kante_narrow",
    "not_part_of",
    "object_scope",
    "require_in_collaboration",
    "serve",
    "served",
    "wholes",
]
