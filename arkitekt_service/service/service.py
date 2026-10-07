"""A service as the hub's rekuest sees it: the structures it hosts and the signals it emits.

    from arkitekt_service.service import Descriptor, Service, organization_of

    service = Service("mikro", description="Microscopy data")

    # A model the service hosts, and the descriptors of its objects:
    dataset = service.structure(ArrayDataset, "@mikro/arraydataset", descriptors=ARRAY_DESCRIPTORS, describe=array_descriptors)

    # Separately: what it announces. Every save and delete of a hosted model ...
    service.model_signal(dataset, organization=organization_of())

    # ... or an event that is not a model's save or delete:
    imported = service.signal("@mikro/arraydataset", kinds=["CREATED"], descriptors=ARRAY_DESCRIPTOR_KEYS)
    imported.emit(dataset.pk, organization=dataset.organization.slug, descriptors={...})

and in ``urls.py``: ``urlpatterns = [..., *service.urls]``. The endpoint is bound to THIS service,
so what the manifest lists is exactly what the declaration says. rekuest reads that manifest
(``GET <url>/manifest``) when it catalogues the service: structures become its catalog of what
this service hosts, signals become the declarations triggers are checked against.

Hosting and announcing are two declarations. A structure says what exists and how its objects are
described; a signal says what is announced about it. A structure with no signal is hosted
silently: nothing about hosting implies an announcement.

A service declares no actions and knows of no agent. Work rekuest can ask for is offered by
agents, which are something else entirely (a HookAgent is :mod:`arkitekt_service.hook`'s).

The name is what rekuest knows the service by (``rekuest.services[].name``); a ``SERVICE`` in
``settings.REKUEST_SERVICE`` overrides it, e.g. for a second instance of one service.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

from arkitekt_service.contract.description import Hosts
from arkitekt_service.service.structures import Descriptor, Structure, check_identifier

logger = logging.getLogger(__name__)

KINDS = ("CREATED", "UPDATED", "DELETED")

#: 3: structures and signals only; a service's manifest lists no actions (2 still did).
MANIFEST_VERSION = 3


@dataclass(frozen=True)
class SignalDeclaration:
    identifier: str
    kinds: tuple[str, ...]
    descriptors: tuple[str, ...]
    description: str | None

    def manifest(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "kinds": list(self.kinds), "descriptors": list(self.descriptors), "description": self.description}


def organization_of(path: str = "organization") -> Callable[[Any], str | None]:
    """An ``organization=`` for :meth:`Service.model_signal`: follow ``path`` (dotted, e.g.
    ``"room.organization"``) from the object to its organization and take its slug."""
    steps = path.split(".")

    def resolve(obj: Any) -> str | None:
        for step in steps:
            obj = getattr(obj, step, None)
            if obj is None:
                return None
        return getattr(obj, "slug", None)

    return resolve


class Signal:
    """A declared signal. ``emit`` announces one object; it is best-effort and never raises on delivery."""

    def __init__(self, service: Service, declaration: SignalDeclaration) -> None:
        self.service = service
        self.declaration = declaration
        self._warned_keys: set[str] = set()

    @property
    def identifier(self) -> str:
        return self.declaration.identifier

    def emit(self, object: Any, *, organization: str, descriptors: dict[str, Any] | None = None, kind: str | None = None) -> None:
        """Announce ``kind`` (default: the one declared kind) of ``object``, once the transaction commits.

        A kind outside the declaration is a programming error and raises. Descriptor keys the
        declaration does not name are sent anyway, with one warning: rekuest checks triggers
        against the declared keys, so an undeclared key is one no trigger can test.
        """
        kinds = self.declaration.kinds
        if kind is None:
            if len(kinds) != 1:
                raise ValueError(f"{self.identifier} declares {kinds}; say which kind this is")
            kind = kinds[0]
        if kind not in kinds:
            raise ValueError(f"{self.identifier} is declared for {kinds}, not {kind!r}")
        undeclared = set(descriptors or {}) - set(self.declaration.descriptors) - self._warned_keys
        if undeclared:
            self._warned_keys |= undeclared
            logger.warning("Signal %s carries undeclared descriptor(s) %s; no trigger can test them", self.identifier, ", ".join(sorted(undeclared)))
        self.service._emit(kind, self.identifier, object, organization=organization, descriptors=descriptors)

    def __repr__(self) -> str:
        return f"Signal({self.identifier!r}, kinds={self.declaration.kinds})"


class Service:
    """One service's declaration towards its hub's rekuest: what it hosts, what it announces.

    Structures and signals, hub-wide facts about its data, each declared on its own. No actions.
    """

    def __init__(self, name: str, *, identifier: str | None = None, description: str | None = None, key: Any = None, hosts: Hosts | None = None) -> None:
        self.name = name
        #: What the service's contract says it hosts (``contract.description.hosts``). With it,
        #: this object declares nothing: :meth:`structure` and :meth:`signal` bind what is said
        #: there to the models and the code, and refuse anything it does not say. Without it (a
        #: process that is no service of a hub's contract), they are the declaration.
        self.hosts = hosts
        #: The key this service signs with; None (the rule) = this instance's key
        #: (``settings.INSTANCE``). Set when one process plays several services (tests).
        self.key = key
        #: The fakts identifier this instance signs as (``iss``) — what the coord's trust bundle
        #: lists its key under. Defaults to ``live.arkitekt.<name>``.
        self.identifier = identifier or f"live.arkitekt.{name}"
        self.description = description
        self._signals: dict[str, Signal] = {}
        self._structures: dict[str, Structure] = {}

    # --- hosting -------------------------------------------------------------------------

    def structure(
        self,
        model: Any,
        identifier: str,
        *,
        descriptors: Iterable[Descriptor | str] | None = None,
        describe: Callable[[Any], dict[str, Any]] | None = None,
        label: str | None = None,
        description: str | None = None,
    ) -> Structure:
        """Declare that this service hosts ``model`` as the structure ``identifier``.

        ``descriptors`` lists the descriptors its objects carry (a bare key is an untyped
        :class:`Descriptor`); ``describe(obj)`` computes them, as a flat ``{key: value}``. Said
        here once: the manifest, :meth:`describe` and any signal declared for the structure
        (:meth:`model_signal`) read this declaration.

        Hosting announces nothing. Whether saves and deletes of the model are signalled is a
        separate declaration.

        For a service with a contract (``hosts``), the structure is declared there and this
        binds it: ``structure(model, identifier, describe=...)`` and nothing else.
        """
        check_identifier(identifier)
        if self.hosts is not None:
            said = self.hosts.structure(identifier)
            if said is None:
                raise ValueError(f"{self.name}'s contract hosts no structure {identifier!r}: declare it there (`hosts`), where the hub reads it")
            if descriptors is not None or label is not None or description is not None:
                raise ValueError(f"What the structure {identifier!r} is and carries is said in {self.name}'s contract, once: here it is only bound to its model")
            declared_descriptors = tuple(Descriptor(d.key, d.type, d.description) for d in said.descriptors)
            label, description = said.label, said.description
        else:
            declared_descriptors = tuple(d if isinstance(d, Descriptor) else Descriptor(d) for d in descriptors or ())
            if len({d.key for d in declared_descriptors}) != len(declared_descriptors):
                raise ValueError(f"The structure {identifier!r} declares a descriptor key twice")
            if label is None and hasattr(model, "_meta"):
                label = str(model._meta.verbose_name).title()

        declared = Structure(identifier, model, label, description, declared_descriptors, describe)
        existing = self._structures.get(identifier)
        if existing is not None:
            if existing.model is not model or existing.manifest() != declared.manifest():
                raise ValueError(f"The structure {identifier!r} is declared twice, differently")
            return existing
        other = next((s for s in self._structures.values() if s.model is model), None)
        if other is not None:
            raise ValueError(f"{model.__name__} is already hosted as {other.identifier!r}; a model is one structure")
        self._structures[identifier] = declared
        return declared

    @property
    def structures(self) -> dict[str, Structure]:
        return dict(self._structures)

    def structure_for(self, target: Any) -> Structure | None:
        """The structure declared under an identifier, or for a model (class or instance)."""
        if isinstance(target, str):
            return self._structures.get(target)
        cls = target if isinstance(target, type) else type(target)
        for declared in self._structures.values():
            if declared.model is cls:
                return declared
        return next((d for d in self._structures.values() if isinstance(d.model, type) and issubclass(cls, d.model)), None)

    def describe(self, obj: Any) -> dict[str, Any]:
        """The descriptors of ``obj`` as its structure declares them; ``{}`` for a model no structure hosts."""
        declared = self.structure_for(obj)
        return declared.describe(obj) if declared is not None else {}

    # --- announcing ----------------------------------------------------------------------

    def signal(self, identifier: str, *, kinds: Iterable[str] | None = None, descriptors: Iterable[str] | None = None, description: str | None = None) -> Signal:
        """Declare that this service emits ``kinds`` of ``identifier`` objects with these descriptor keys.

        The handle's ``emit`` sends one. For a hosted model whose every save and delete is to be
        announced, :meth:`model_signal` declares the signal and sends it by itself.
        """
        if self.hosts is not None:
            said = self.hosts.signal(identifier)
            if said is None:
                raise ValueError(f"{self.name}'s contract announces no signal {identifier!r}: declare it there (`hosts`), where the hub reads it")
            if kinds is not None or descriptors is not None or description is not None:
                raise ValueError(f"What the signal {identifier!r} announces is said in {self.name}'s contract, once: here it is only sent")
            kinds, descriptors, description = said.kinds, said.descriptors, said.description
        kinds = tuple(kinds if kinds is not None else ("CREATED",))
        descriptors = tuple(descriptors or ())
        bad = [k for k in kinds if k not in KINDS]
        if bad or not kinds:
            raise ValueError(f"A signal kind is one of {KINDS}, not {bad or 'nothing'}")
        declaration = SignalDeclaration(identifier, kinds, tuple(descriptors), description)
        existing = self._signals.get(identifier)
        if existing is not None:
            if existing.declaration != declaration:
                raise ValueError(f"The signal {identifier!r} is declared twice, differently")
            return existing
        handle = Signal(self, declaration)
        self._signals[identifier] = handle
        return handle

    def model_signal(
        self,
        structure: Structure | Any,
        *,
        organization: Callable[[Any], str | None],
        kinds: Iterable[str] | None = None,
        when: Callable[[Any, str], bool] | None = None,
        descriptors: Iterable[str] | None = None,
        description: str | None = None,
    ) -> Signal:
        """Declare that every save and delete of a hosted structure's model is signalled.

        ``structure`` is one this service hosts (what :meth:`structure` returned, or its model).
        No ``emit`` in the mutations: a save that creates the row is CREATED, any other save
        UPDATED (so ``update_or_create`` upserts are told apart for free), a delete DELETED;
        kinds not listed are not sent. ``organization(obj)`` names the organization (its slug);
        ``when(obj, kind)`` may veto (privacy, half-written rows).

        The signal carries the structure's descriptors, computed by its ``describe``.
        ``descriptors`` names further keys only a hand-made ``emit`` carries: facts about the
        event, not the object.

        For saves, ``organization`` and ``describe`` run after the transaction commits, so they
        see everything the request wrote after the row itself — provided it wrote them in one
        transaction: outside one, "after the commit" is right after that save. A mutation whose
        descriptors depend on rows written after the object belongs in ``transaction.atomic``.
        For deletes they run at delete time, while the row still exists. The provenance token is
        always read at save time. A raising callable costs one warning: saving never fails
        because of a signal. Bulk writes (``bulk_create``, ``update()``) send nothing — Django
        sends no ``post_save`` for them.
        """
        from django.db.models.signals import post_delete, post_save

        hosted = structure if isinstance(structure, Structure) else self.structure_for(structure)
        if hosted is None or self._structures.get(hosted.identifier) is not hosted:
            raise ValueError(f"{structure!r} is not a structure this service hosts; declare it with service.structure first")
        identifier, model, describe = hosted.identifier, hosted.model, hosted.describer
        if self.hosts is not None:
            # Said in the contract, with the keys it carries. It has to carry the structure's
            # own: they are what is computed for every save.
            handle = self.signal(identifier, kinds=kinds, descriptors=descriptors, description=description)
            missing = [key for key in hosted.keys if key not in handle.declaration.descriptors]
            if missing:
                raise ValueError(f"The signal {identifier!r} is sent with its structure's descriptors, and {self.name}'s contract leaves out {', '.join(missing)}")
        else:
            handle = self.signal(identifier, kinds=kinds if kinds is not None else KINDS, descriptors=(*hosted.keys, *(descriptors or ())), description=description)

        uid = f"rekuest_service:{self.name}:{identifier}"

        def on_save(sender: Any, instance: Any, created: bool = False, raw: bool = False, **_: Any) -> None:
            if not raw:  # fixtures being loaded are not events
                self._emit_for(handle, instance, "CREATED" if created else "UPDATED", organization, describe, when, lazy=True)

        def on_delete(sender: Any, instance: Any, **_: Any) -> None:
            self._emit_for(handle, instance, "DELETED", organization, describe, when, lazy=False)

        post_save.connect(on_save, sender=model, weak=False, dispatch_uid=f"{uid}:save")
        post_delete.connect(on_delete, sender=model, weak=False, dispatch_uid=f"{uid}:delete")
        return handle

    @property
    def signals(self) -> dict[str, Signal]:
        return dict(self._signals)

    # --- what rekuest reads --------------------------------------------------------------

    def unbound(self) -> list[str]:
        """What the contract says this service hosts or announces, and nothing here stands behind."""
        if self.hosts is None:
            return []
        return [
            *(f"structure {s.identifier}" for s in self.hosts.structures if s.identifier not in self._structures),
            *(f"signal {s.identifier}" for s in self.hosts.signals if s.identifier not in self._signals),
        ]

    def check(self) -> None:
        """Refuse to start as a service whose contract promises what its code does not hold.

        A hub is told what the image hosts before the service runs; a structure with no model
        behind it, or a signal nothing sends, would be a promise no object ever keeps.
        """
        unbound = self.unbound()
        if unbound:
            raise RuntimeError(f"{self.name}'s contract declares {', '.join(unbound)}, and its service binds none of it: bind it (service.structure, service.signal) or take it out of the contract")

    def manifest(self) -> dict[str, Any]:
        return {
            "service": self.service_name(),
            "identifier": self.signing_identifier(),
            "description": self.description,
            "structures": [s.manifest() for s in self._structures.values()],
            "signals": [s.declaration.manifest() for s in self._signals.values()],
            "manifest_version": MANIFEST_VERSION,
        }

    @property
    def urls(self) -> list:
        """The ``_rekuest/service`` endpoint (the manifest), bound to this service. Mount it in ``urls.py``."""
        from arkitekt_service.service.views import urlpatterns_for

        self.check()
        return urlpatterns_for(self)

    # --- configuration and sending -------------------------------------------------------

    def config(self) -> dict[str, Any] | None:
        """``settings.REKUEST_SERVICE`` when it can reach rekuest — its URL set and an instance key
        configured (``settings.INSTANCE``) — else None, and everything is then a no-op."""
        config = getattr(settings, "REKUEST_SERVICE", None)
        if not config or not config.get("REKUEST_URL") or self.signing_key() is None:
            return None
        return config

    def signing_key(self) -> Any:
        """What this service signs with: its own ``key``, else this instance's key."""
        from arkitekt_service.trust import instance_key

        return self.key if self.key is not None else instance_key()

    def signing_identifier(self) -> str:
        """What this service signs as: ``settings.REKUEST_SERVICE["IDENTIFIER"]``, else the declared identifier."""
        config = getattr(settings, "REKUEST_SERVICE", None) or {}
        return config.get("IDENTIFIER") or self.identifier

    @staticmethod
    def rekuest_identifier() -> str:
        """Whom rekuest's requests must come from (``settings.REKUEST_SERVICE["REKUEST_IDENTIFIER"]``)."""
        config = getattr(settings, "REKUEST_SERVICE", None) or {}
        return config.get("REKUEST_IDENTIFIER") or "live.arkitekt.rekuest"

    def service_name(self) -> str:
        config = getattr(settings, "REKUEST_SERVICE", None) or {}
        return config.get("SERVICE") or self.name

    def _emit_for(self, handle: Signal, instance: Any, kind: str, organization: Callable, descriptors: Callable | None, when: Callable | None, *, lazy: bool) -> None:
        if kind not in handle.declaration.kinds or instance.pk is None or self.config() is None:
            return  # not announced, or nowhere to announce it: a save costs nothing extra
        try:
            if when is not None and not when(instance, kind):
                return
        except Exception as error:  # noqa: BLE001
            logger.warning("Signal %s: `when` failed for %s: %s", handle.identifier, instance.pk, error)
            return
        if lazy:
            self._emit(kind, handle.identifier, instance.pk, organization=lambda: organization(instance), descriptors=(lambda: descriptors(instance)) if descriptors else None)
            return
        try:
            org = organization(instance)
            values = descriptors(instance) if descriptors else None
        except Exception as error:  # noqa: BLE001
            logger.warning("Signal %s: could not describe %s %s: %s", handle.identifier, kind, instance.pk, error)
            return
        self._emit(kind, handle.identifier, instance.pk, organization=org, descriptors=values)

    def _emit(self, kind: str, identifier: str, object: Any, *, organization: str | Callable[[], str | None], descriptors: dict[str, Any] | Callable[[], dict[str, Any]] | None) -> None:
        """Queue one signal for after the commit. ``organization``/``descriptors`` may be callables,
        evaluated then (see :meth:`model_signal`)."""
        from arkitekt_service.service.signals import current_provenance_token, enqueue

        config = self.config()
        if config is None:
            return
        service = self.service_name()
        base = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "identifier": identifier,
            "object": str(object),
            # Read now, while the request that caused the object is still the current context.
            "provenance": current_provenance_token(),
            "occurred_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        issuer = self.signing_identifier()
        audience = self.rekuest_identifier()
        key = self.signing_key()

        def dispatch() -> None:
            try:
                org = organization() if callable(organization) else organization
                values = descriptors() if callable(descriptors) else descriptors
            except Exception as error:  # noqa: BLE001  a signal never breaks the write it describes
                logger.warning("Signal %s: could not describe %s %s: %s", identifier, kind, object, error)
                return
            if not org:
                logger.debug("Signal %s %s %s has no organization; not sent", kind, identifier, object)
                return
            enqueue(config, service, issuer, audience, {**base, "organization": org, "descriptors": values or {}}, key)

        transaction.on_commit(dispatch)

    def __repr__(self) -> str:
        return f"Service({self.name!r}, structures={list(self._structures)}, signals={list(self._signals)})"
