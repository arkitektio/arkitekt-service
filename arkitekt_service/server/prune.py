"""Delete everything organizations own, and keep the organizations.

An organization's data is every row of every model with a foreign key of its own to
``authentikate.Organization`` -- whatever a service declares, found by walking the installed
models -- and whatever deleting those rows cascades to. The identity is not data: the
organization, its memberships and its users stay (the ``authentikate`` app is never touched).

Nothing here knows a service's models, so nothing here knows the order they can be deleted
in. A service links its rows with PROTECT and RESTRICT (a dataset protects its coordinate
system, a correction protects what it supersedes), and Django refuses those even when the
protecting row goes in the same delete. So the rows go in passes:

- each pass takes, model by model (referrers before what they protect), the rows nothing
  protects any more, in chunks; a pass that deletes nothing is followed by one that tries
  every row left, for what is protected only through a cascade;
- each chunk is one :class:`~django.db.models.deletion.Collector` in one transaction, so
  cascades, ``SET_NULL`` and every ``post_delete`` receiver run as they would for any delete;
- a chunk the collector refuses, that would take another organization's row along (a
  CASCADE does not care whose a row is; a row without an organization of its own is its
  CASCADE parents', see :func:`_owners`), or that cascades into more than ``max_cascade`` rows
  is split in two and tried again; a single row that is refused is *blocked*.

A transaction per chunk, not per organization: one long transaction would hold its locks
for minutes and the change feed of the whole database cluster behind it. A prune cut short
is not undone, and running it again goes on where it stopped. What is blocked when nothing
moves any more is protected from outside what is pruned, and is named, never forced.

The change feed (``arkitekt_service.feed``) is left alone: it is how a reader learns what was
deleted. A service whose deletes announce themselves only through its own writers (mikro's
metrics write their DELETED entries) does so from :data:`pruning`, which is sent inside each
chunk's transaction before anything is deleted.
"""

from __future__ import annotations

import graphlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, models, router, transaction
from django.db.models.deletion import CASCADE, PROTECT, RESTRICT, Collector, ProtectedError, RestrictedError
from django.dispatch import Signal

IDENTITY_APP = "authentikate"

pruning = Signal()
"""Sent per model, inside a chunk's transaction, before its rows are deleted.

``sender`` is the model, ``rows`` a queryset of exactly the rows that go (cascaded ones
included), ``organization`` the organization pruned. A receiver writes what a delete of those
rows owes elsewhere -- a DELETED feed entry -- and must not delete anything itself.
"""


class PruneRefused(Exception):
    """The selection names no organization, or one this service does not know."""


def organization_model() -> type[models.Model]:
    if not apps.is_installed(IDENTITY_APP):
        raise ImproperlyConfigured("This service has no organizations (authentikate is not installed)")
    return apps.get_model(IDENTITY_APP, "Organization")


def resolve(names: Sequence[str]) -> list[models.Model]:
    """The organizations ``names`` names: each an ``<iss>#<org>`` identifier, or the bare ``<org>`` of exactly one."""
    Organization = organization_model()
    found: list[models.Model] = []
    for name in names:
        if "#" in name:
            matches = list(Organization._base_manager.filter(identifier=name))
        else:
            matches = list(Organization._base_manager.filter(identifier__endswith=f"#{name}"))
        if not matches:
            raise PruneRefused(f"No organization {name!r} on this service")
        if len(matches) > 1:
            raise PruneRefused(f"{name!r} names {len(matches)} organizations ({', '.join(str(m) for m in matches)}): give the whole identifier")
        if matches[0] not in found:
            found.append(matches[0])
    return found


def select(*, only: Sequence[str] | None = None, keep: Sequence[str] | None = None) -> list[models.Model]:
    """The organizations to prune: those ``only`` names, or every one ``keep`` does not."""
    if (only is None) == (keep is None):
        raise PruneRefused("Name the organizations to prune (only) or the ones to keep (keep), not both")
    if only is not None:
        if not only:
            raise PruneRefused("only names no organization")
        return resolve(only)
    kept = {org.pk for org in resolve(keep or [])}
    return list(organization_model()._base_manager.exclude(pk__in=kept).order_by("pk"))


def _org_fields(model: type[models.Model], *, local: bool) -> list[models.ForeignKey]:
    Organization = organization_model()
    fields = model._meta.local_concrete_fields if local else model._meta.concrete_fields
    return [f for f in fields if isinstance(f, models.ForeignKey) and f.remote_field.model is Organization]


def scope_models() -> list[type[models.Model]]:
    """Every model whose rows an organization owns: one with a foreign key of its own to it."""
    found = []
    for model in apps.get_models():
        if model._meta.app_label == IDENTITY_APP or model._meta.proxy:
            continue
        if _org_fields(model, local=True):
            found.append(model)
    return found


def _protectors() -> dict[type[models.Model], list[models.ForeignKey]]:
    """For each model, the foreign keys (of any model) that PROTECT or RESTRICT its rows."""
    found: dict[type[models.Model], list[models.ForeignKey]] = defaultdict(list)
    for model in apps.get_models(include_auto_created=True):
        for f in model._meta.local_concrete_fields:
            if isinstance(f, models.ForeignKey) and f.remote_field.on_delete in (PROTECT, RESTRICT):
                found[f.remote_field.model._meta.concrete_model].append(f)
    return found


def _ordered(scoped: list[type[models.Model]], protectors: dict[type[models.Model], list[models.ForeignKey]]) -> list[type[models.Model]]:
    """Referrers before what they protect, where the protections among scoped models allow an order."""
    graph: dict[type[models.Model], set[type[models.Model]]] = {model: set() for model in scoped}
    for model in scoped:
        for f in protectors.get(model, []):
            referrer = f.model._meta.concrete_model
            if referrer in graph and referrer is not model:
                graph[model].add(referrer)
    try:
        return list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError:
        return scoped


@dataclass
class Blocked:
    label: str
    pk: object
    organization: str
    reason: str

    def __str__(self) -> str:
        return f"{self.organization}: {self.label} {self.pk} -- {self.reason}"


@dataclass
class PruneReport:
    organizations: list[str]
    deleted: dict[str, dict[str, int]] = field(default_factory=dict)
    """Per organization identifier, the rows deleted per model label (cascades included)."""
    blocked: list[Blocked] = field(default_factory=list)
    oversized: list[str] = field(default_factory=list)
    """Single rows deleted although they cascade into more than ``max_cascade`` rows."""

    @property
    def total(self) -> int:
        return sum(n for per_model in self.deleted.values() for n in per_model.values())


def in_scope(organizations: Iterable[models.Model]) -> dict[str, dict[str, int]]:
    """Per organization, the rows it owns directly per model: what a prune starts from (cascades not counted)."""
    found: dict[str, dict[str, int]] = {}
    for org in organizations:
        counts = {}
        for model in scope_models():
            n = _owned(model, org).count()
            if n:
                counts[model._meta.label] = n
        found[str(org)] = counts
    return found


def _rows(model: type[models.Model]) -> models.QuerySet:
    """Every row of ``model``, as rows of ``model``.

    A polymorphic manager (django-polymorphic) hands out each row as its subclass, and a
    collector keys a batch by its first row's class: a base model's rows are read plain.
    """
    rows = model._base_manager.all()
    non_polymorphic = getattr(rows, "non_polymorphic", None)
    return non_polymorphic() if callable(non_polymorphic) else rows


def _owned(model: type[models.Model], org: models.Model) -> models.QuerySet:
    query = models.Q()
    for f in _org_fields(model, local=True):
        query |= models.Q(**{f.attname: org.pk})
    return _rows(model).filter(query)


class _Refused(Exception):
    pass


class _Pruner:
    def __init__(self, organizations: list[models.Model], *, batch_size: int, max_cascade: int) -> None:
        self.organizations = organizations
        self.pruned = {org.pk for org in organizations}
        self.batch_size = batch_size
        self.max_cascade = max_cascade
        self.scoped = scope_models()
        self.protectors = _protectors()
        self.order = _ordered(self.scoped, self.protectors)
        self.report = PruneReport(organizations=[str(org) for org in organizations])

    def run(self) -> PruneReport:
        filtered = True
        while True:
            self.report.blocked = []
            if self._pass(filtered=filtered):
                filtered = True
            elif filtered:
                filtered = False  # everything left is protected, or protected only through a cascade: try every row
            else:
                return self.report

    def _pass(self, *, filtered: bool) -> int:
        progress = 0
        for org in self.organizations:
            for model in self.order:
                rows = _owned(model, org)
                if filtered:
                    rows = self._unprotected(model, rows)
                pks = list(rows.order_by("pk").values_list("pk", flat=True))
                for start in range(0, len(pks), self.batch_size):
                    progress += self._chunk(model, org, pks[start : start + self.batch_size])
        return progress

    def _unprotected(self, model: type[models.Model], rows: models.QuerySet) -> models.QuerySet:
        """The rows no PROTECT or RESTRICT foreign key points at any more."""
        for f in self.protectors.get(model._meta.concrete_model, []):
            target = f.target_field.attname
            referring = _rows(f.model).filter(**{f"{f.attname}__isnull": False}).values(f.attname)
            rows = rows.exclude(**{f"{target}__in": referring})
        return rows

    def _chunk(self, model: type[models.Model], org: models.Model, pks: list[object]) -> int:
        using = router.db_for_write(model) or DEFAULT_DB_ALIAS
        try:
            with transaction.atomic(using=using):
                chunk = _rows(model).filter(pk__in=pks)
                collector = Collector(using=using, origin=chunk)
                collector.collect(chunk)
                self._check(collector, single=len(pks) == 1, label=f"{model._meta.label} {pks[0]}")
                for collected, rows in _going(collector):
                    pruning.send(sender=collected, rows=rows, organization=org)
                _, per_model = collector.delete()
        except ProtectedError as error:
            reason = "protected by " + _sample(error.protected_objects)
        except RestrictedError as error:
            reason = "restricted by " + _sample(error.restricted_objects)
        except _Refused as refused:
            reason = str(refused)
        else:
            counts = self.report.deleted.setdefault(str(org), {})
            for label, n in per_model.items():
                if n:
                    counts[label] = counts.get(label, 0) + n
            return sum(per_model.values())
        if len(pks) == 1:
            self.report.blocked.append(Blocked(model._meta.label, pks[0], str(org), reason))
            return 0
        half = len(pks) // 2
        return self._chunk(model, org, pks[:half]) + self._chunk(model, org, pks[half:])

    def _check(self, collector: Collector, *, single: bool, label: str) -> None:
        size = 0
        for collected, rows in _going(collector):
            size += rows.count()
            for lookup in _owners(collected):
                foreign = rows.filter(**{f"{lookup}__isnull": False}).exclude(**{f"{lookup}__in": self.pruned}).values_list("pk", flat=True).first()
                if foreign is not None:
                    through = "" if "__" not in lookup else f" (through {lookup.rsplit('__', 1)[0]})"
                    raise _Refused(f"would take {collected._meta.label} {foreign}, of another organization{through}, along")
        if size > self.max_cascade:
            if not single:
                raise _Refused(f"cascades into {size} rows")
            self.report.oversized.append(f"{label} ({size} rows)")


def _owners(model: type[models.Model], depth: int = 2) -> list[str]:
    """The lookups to the organizations a row of ``model`` belongs to.

    Its own, when it has one. A row without one (a layer, the through row of a many-to-many)
    belongs to whatever it cascades from: the organizations of its CASCADE parents, two hops
    deep. A reference that only sets null on delete (a creator, a task) owns nothing. That a
    row cascades from another organization's row is enough to leave it: generic code cannot
    tell a scene owning its layer from a lens the layer only draws through.
    """
    own = _org_fields(model, local=False)
    if own:
        return [f.attname for f in own]
    found = []
    for f in model._meta.concrete_fields:
        if not isinstance(f, models.ForeignKey) or f.remote_field.on_delete is not CASCADE or f.remote_field.parent_link:
            continue
        parent = f.remote_field.model
        if parent._meta.app_label == IDENTITY_APP:
            continue
        parents = _org_fields(parent, local=False)
        if parents:
            found += [f"{f.name}__{o.name}" for o in parents]
        elif depth > 1:
            found += [f"{f.name}__{lookup}" for lookup in _owners(parent, depth - 1)]
    return found


def _going(collector: Collector) -> Iterable[tuple[type[models.Model], models.QuerySet]]:
    """What a collector is about to delete, per model, as querysets (read before the delete)."""
    for collected, instances in collector.data.items():
        yield collected, _rows(collected).filter(pk__in=[i.pk for i in instances])
    for qs in collector.fast_deletes:
        yield qs.model, qs


def _sample(objects: Iterable[models.Model]) -> str:
    listed = [f"{o._meta.label} {o.pk}" for o in list(objects)[:3]]
    return ", ".join(listed) or "something"


def prune(organizations: list[models.Model], *, batch_size: int = 500, max_cascade: int = 20_000) -> PruneReport:
    """Delete every row ``organizations`` own; see the module for how. Running it again finishes what is left."""
    return _Pruner(organizations, batch_size=batch_size, max_cascade=max_cascade).run()
