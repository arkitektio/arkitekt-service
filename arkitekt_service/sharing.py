"""Narrowing a scoped member's reads to the objects shared with them.

A member of an organization either acts for it, or was shared a few of its objects and nothing
else: a *scoped* member, whose token lists them by hub (``objects``, read by authentikate). Every
read such a request makes goes through one call, at the service's scoping chokepoint, after its
organization filter::

    from arkitekt_service.sharing import object_scope

    def scope_queryset(queryset, info):
        return object_scope(queryset.filter(organization=info.context.request.organization))

For a full member it is the queryset itself. For a scoped one it keeps the rows a shared object
covers — the object, and what its reach's closures say (``Service.structure(..., closures=...)``)
— and nothing of a model no closure covers. A service on kante's scoper says it once instead:
``KANTE_OBJECT_SCOPE = "arkitekt_service.sharing.kante_narrow"``.

Binding a closure is what tells authentikate this process narrows (``enforce_scoped``): until a
service does, its scoped tokens are rejected. Bind them once the querysets go through here.

The hub a token's grants are read for is ``AUTHENTIKATE.HUB``, else the one this instance's key
names in the trust bundle (:func:`arkitekt_service.trust.known_hub`). Needs authentikate >= 4.3
(the ``authentikate`` extra) to read the request's token.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Protocol

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import ForeignKey, Q, QuerySet

from arkitekt_service.service.structures import Structure
from arkitekt_service.trust import hub, known_hub

logger = logging.getLogger(__name__)


class Grant(Protocol):
    """One shared object, as authentikate parses it (``ObjectGrant``)."""

    structure: str
    object_id: str
    reach: str


class Token(Protocol):
    """What is read of a request's token (``authentikate.JWTToken``)."""

    @property
    def is_scoped(self) -> bool: ...

    def grants_for(self, hub: str) -> tuple[Grant, ...]: ...


def enforce() -> None:
    """Tell authentikate this process narrows a scoped member's reads, so it serves their tokens.

    The hub its grants are read for, unless ``AUTHENTIKATE.HUB`` says, is the one this instance's
    key names in the trust bundle. That is looked up here, off the request path: until it is
    known, scoped members are shut out, never let in unnarrowed.
    """
    try:
        from authentikate.scoped import enforce_scoped
    except ImportError:
        logger.warning("Structures are bound with closures, but authentikate>=4.3 is not installed: scoped members stay shut out")
        return
    enforce_scoped(hub_resolver=known_hub)
    if known_hub() is None:
        threading.Thread(target=_learn_hub, name="arkitekt-service-hub", daemon=True).start()


def _learn_hub() -> None:
    try:
        if hub() is None:
            logger.info("The hub's trust bundle names no hub for this instance's key: scoped members need AUTHENTIKATE.HUB")
    except Exception as error:  # noqa: BLE001  a coord outage only delays sharing
        logger.warning("Could not learn this instance's hub from the trust bundle: %s", error)


def _authentikate() -> bool:
    """Whether this process authenticates with authentikate (installed as a Django app)."""
    from django.apps import apps

    return apps.is_installed("authentikate")


def current_token() -> Token | None:
    """The token the request being served was authenticated with, if authentikate holds one."""
    if not _authentikate():
        return None
    from authentikate.vars import get_token

    return get_token()


def current_hub() -> str | None:
    """The hub whose grants this service reads: ``AUTHENTIKATE.HUB``, else its key's in the trust bundle."""
    if not _authentikate():
        return known_hub()
    try:
        from authentikate.scoped import current_hub as configured
    except ImportError:  # authentikate before 4.3: it reads no grants either
        return known_hub()
    from authentikate.settings import get_settings

    return configured(get_settings())


def shared_structures() -> dict[str, Structure]:
    """Every structure of a service that narrows reads (``Service.sharing``), by identifier."""
    from arkitekt_service.service.service import Service

    return {identifier: structure for service in Service.sharing for identifier, structure in service.structures.items()}


def target_field(model: Any, lookup: str) -> Any:
    """The field ``lookup`` ends at, from ``model``: for a relation, the field it is compared by.

    Raises ``FieldDoesNotExist`` for a path that does not resolve.
    """
    meta = model._meta
    parts = lookup.split("__")
    field: Any = None
    for index, part in enumerate(parts):
        field = meta.pk if part == "pk" else meta.get_field(part)
        if index < len(parts) - 1:
            if not field.is_relation or field.related_model is None:
                raise FieldDoesNotExist(f"{lookup}: {part} of {meta.label} is no relation")
            meta = field.related_model._meta
    if isinstance(field, ForeignKey):
        return field.target_field
    if field.is_relation:
        return field.related_model._meta.pk
    return field


def _value(model: Any, lookup: str, object_id: str) -> Any:
    """``object_id`` as the field ``lookup`` ends at holds it; None for one it could not hold."""
    try:
        return target_field(model, lookup).to_python(object_id)
    except (FieldDoesNotExist, ValidationError, ValueError, TypeError):
        return None


def shared_ids(model: Any, grants: Iterable[Grant]) -> dict[str, set[Any]]:
    """For each lookup of ``model``'s rows, the shared objects' ids it is to match."""
    structures = shared_structures()
    ids: dict[str, set[Any]] = defaultdict(set)
    for grant in grants:
        structure = structures.get(grant.structure)
        if structure is None:
            continue  # another service's structure: nothing of it is here
        for lookup in structure.lookups(model, grant.reach):
            value = _value(model, lookup, grant.object_id)
            if value is not None:
                ids[lookup].add(value)
    return ids


def object_scope(queryset: QuerySet[Any], token: Token | None = None, *, hub: str | None = None) -> QuerySet[Any]:
    """``queryset``, narrowed to what was shared with the request's member when it is a scoped one.

    ``token`` defaults to the request's, ``hub`` to :func:`current_hub`. A full member's (or no
    token: work outside a request) is returned as it is. A scoped member's keeps the rows its
    grants cover on this hub; with no hub, or nothing covered, none.
    """
    token = current_token() if token is None else token
    if token is None or not token.is_scoped:
        return queryset
    hub = current_hub() if hub is None else hub
    if hub is None:
        return queryset.none()
    ids = shared_ids(queryset.model, token.grants_for(hub))
    if not ids:
        return queryset.none()
    condition = Q()
    for lookup, values in sorted(ids.items()):
        condition |= Q(**{f"{lookup}__in": sorted(values, key=str)})
    return queryset.filter(condition)


def is_shared(structure: str, object_id: Any, token: Token | None = None, *, hub: str | None = None) -> bool:
    """Whether the request's member may act on this object as shared: a full member always, a scoped
    one when it was shared with them by itself (any reach). For a resolver handing one object out."""
    token = current_token() if token is None else token
    if token is None or not token.is_scoped:
        return True
    hub = current_hub() if hub is None else hub
    if hub is None:
        return False
    return any(g.structure == structure and g.object_id == str(object_id) for g in token.grants_for(hub))


def kante_narrow(queryset: QuerySet[Any], info: Any) -> QuerySet[Any]:
    """:func:`object_scope` as kante's narrowing step (``KANTE_OBJECT_SCOPE``)."""
    try:
        token = info.context.request.get_extension("token")
    except (AttributeError, KeyError, ValueError):
        token = None
    return object_scope(queryset, token)


__all__ = [
    "current_hub",
    "current_token",
    "enforce",
    "is_shared",
    "kante_narrow",
    "object_scope",
    "shared_ids",
    "shared_structures",
    "target_field",
]
