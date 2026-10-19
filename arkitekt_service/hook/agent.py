"""A HookAgent: an agent rekuest reaches over HTTP, and the actions it offers.

    from arkitekt_service.hook import HookAgent

    agent = HookAgent("mikro", description="mikro's housekeeping")

    @agent.action
    def reembed_stale(organization: str) -> dict:
        '''Re-embed stale rows.'''
        return {"reembedded": reembed_all(MODELS, organization)}

and in ``urls.py``: ``urlpatterns = [..., *agent.urls]``.

An agent says what can be done; that is all it says. It hosts nothing and announces nothing, and
it needs no service: any Django process with an instance key the hub trusts can be one. That it
often sits in the process of a service is where it runs, not what it is.

rekuest reads the agent's manifest (``GET <hook_url>/manifest``), gives every organization of the
hub the agent and registers its actions as real actions. So an action runs for one organization:
a function that takes ``organization`` is given its slug and does that organization's share of
the work, nothing else.

An action may take a structure: ``@agent.action(args=[StructurePort("metric", "@mikro/metric")])``
declares a STRUCTURE port, so a trigger on that structure's signals can run it, and the function
is handed a :class:`StructureRef` (or None, when a nullable port was left out — a schedule's run).

An agent only OFFERS actions. When one runs — on a schedule, on a signal, by hand — is not the
agent's to say: nothing here wires an action to a schedule or a trigger. That is the
organization's automation, set up by its users.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings

#: 1: ``agent``, ``identifier``, ``description`` and ``actions``.
#: 2: each action's ``args``, its STRUCTURE ports.
MANIFEST_VERSION = 2

#: The argument rekuest's Assign does not carry: the agent fills it from the run's organization.
ORGANIZATION = "organization"


@dataclass(frozen=True)
class StructurePort:
    """An argument that takes one object of a structure (``@mikro/metric``), as rekuest spells a STRUCTURE port."""

    key: str
    identifier: str
    description: str | None = None
    nullable: bool = False

    def manifest(self) -> dict[str, Any]:
        return {"key": self.key, "kind": "STRUCTURE", "identifier": self.identifier, "nullable": self.nullable, "description": self.description}


@dataclass(frozen=True)
class StructureRef:
    """The object a run was given for a STRUCTURE port: the structure's identifier and the object's id."""

    identifier: str
    object: str


@dataclass(frozen=True)
class Action:
    interface: str
    function: Callable[..., Any]
    name: str
    description: str | None
    args: tuple[StructurePort, ...] = ()

    @property
    def takes_organization(self) -> bool:
        """Whether the function asks for the ``organization`` (slug) the run is for."""
        return ORGANIZATION in inspect.signature(self.function).parameters

    def manifest(self) -> dict[str, Any]:
        return {"interface": self.interface, "name": self.name, "description": self.description, "args": [port.manifest() for port in self.args]}

    def arguments(self, given: dict[str, Any], organization: str | None) -> dict[str, Any]:
        """What the function is called with: each port's object as a :class:`StructureRef`, and the organization.

        Only declared ports are passed on. A left-out nullable port is None; a left-out required
        one, or an object of another structure, is refused (``ValueError``) — rekuest validated
        the Assign, so either means it was not built for this action.
        """
        arguments: dict[str, Any] = {}
        for port in self.args:
            value = given.get(port.key)
            if value is None:
                if not port.nullable:
                    raise ValueError(f"The action {self.interface!r} needs {port.key!r}, a {port.identifier}")
                arguments[port.key] = None
                continue
            if not isinstance(value, dict) or value.get("__identifier") != port.identifier or value.get("object") is None:
                raise ValueError(f"{port.key!r} of {self.interface!r} takes a {port.identifier}, not {value!r}")
            arguments[port.key] = StructureRef(identifier=port.identifier, object=str(value["object"]))
        if self.takes_organization:
            arguments[ORGANIZATION] = organization
        return arguments


def _docstring_parts(function: Callable[..., Any]) -> tuple[str | None, str | None]:
    """``(first line, rest)`` of a docstring — the name and description arkitekt derives too."""
    doc = inspect.getdoc(function)
    if not doc:
        return None, None
    first, _, rest = doc.partition("\n")
    return first.strip() or None, rest.strip() or None


def _check_ports(interface: str, function: Callable[..., Any], ports: Sequence[StructurePort]) -> None:
    """Every port is a parameter of ``function``, named once, and none is ``organization``."""
    parameters = inspect.signature(function).parameters
    keys = [port.key for port in ports]
    if len(set(keys)) != len(keys):
        raise ValueError(f"The action {interface!r} declares a port twice: {keys}")
    for key in keys:
        if key == ORGANIZATION:
            raise ValueError(f"The action {interface!r} cannot take {ORGANIZATION!r} as a port: the agent fills it in")
        if key not in parameters:
            raise ValueError(f"The action {interface!r} declares the port {key!r}, which its function does not take")


class HookAgent:
    """An agent reached over HTTP: its name, its signing identity and its actions."""

    def __init__(self, name: str, *, identifier: str | None = None, description: str | None = None, key: Any = None) -> None:
        self.name = name
        #: The key this agent signs with; None (the rule) = this instance's key
        #: (``settings.INSTANCE``). Set when one process plays several instances (tests).
        self.key = key
        #: The name of the service the agent runs in (``iss``) — what the hub's trust bundle
        #: lists its key under. Defaults to ``name``; an agent named differently from the
        #: service it runs in must say whose key it signs with.
        self.identifier = identifier or name
        self.description = description
        self._actions: dict[str, Action] = {}

    # --- declaring -----------------------------------------------------------------------

    def action(
        self,
        function: Callable[..., Any] | None = None,
        /,
        *,
        interface: str | None = None,
        name: str | None = None,
        description: str | None = None,
        args: Sequence[StructurePort] = (),
    ) -> Any:
        """Offer ``function`` (sync or async) as an action; ``@agent.action`` or ``@agent.action(...)``.

        The function takes the ``args`` declared (each a :class:`StructurePort`, handed over as a
        :class:`StructureRef`) and, if it asks for it, ``organization``. The interface defaults to
        the function's name; name and description to its docstring's first line and the rest. It
        should return a small JSON-able dict, reported as the run's result, and must be safe to run
        twice — a lost report may get it redelivered.
        """

        def register(target: Callable[..., Any]) -> Callable[..., Any]:
            key = interface or target.__name__
            _check_ports(key, target, args)
            doc_name, doc_description = _docstring_parts(target)
            declared = Action(key, target, name or doc_name or key, description if description is not None else doc_description, tuple(args))
            existing = self._actions.get(key)
            if existing is not None and existing.function is not target:
                raise ValueError(f"The action {key!r} is registered twice")
            self._actions[key] = declared
            return target

        return register(function) if function is not None else register

    # --- what rekuest reads --------------------------------------------------------------

    @property
    def actions(self) -> dict[str, Action]:
        return dict(self._actions)

    def manifest(self) -> dict[str, Any]:
        return {
            "agent": self.agent_name(),
            "identifier": self.signing_identifier(),
            "description": self.description,
            "actions": [a.manifest() for a in self._actions.values()],
            "manifest_version": MANIFEST_VERSION,
        }

    @property
    def urls(self) -> list:
        """The ``_rekuest/hook`` endpoints (inbox and manifest), bound to this agent. Mount them in ``urls.py``."""
        from arkitekt_service.hook.views import urlpatterns_for

        return urlpatterns_for(self)

    # --- configuration -------------------------------------------------------------------

    def config(self) -> dict[str, Any] | None:
        """``settings.REKUEST_HOOK`` when it can reach rekuest — its URL set and an instance key
        configured (``settings.INSTANCE``) — else None, and the endpoints answer 503."""
        config = getattr(settings, "REKUEST_HOOK", None)
        if not config or not config.get("REKUEST_URL") or self.signing_key() is None:
            return None
        return config

    def signing_key(self) -> Any:
        """What this agent signs with: its own ``key``, else this instance's key."""
        from arkitekt_service.trust import instance_key

        return self.key if self.key is not None else instance_key()

    def signing_identifier(self) -> str:
        """What this agent signs as: ``settings.REKUEST_HOOK["IDENTIFIER"]``, else the declared identifier."""
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        return config.get("IDENTIFIER") or self.identifier

    @staticmethod
    def rekuest_identifier() -> str:
        """Whom rekuest's requests must come from (``settings.REKUEST_HOOK["REKUEST_IDENTIFIER"]``)."""
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        return config.get("REKUEST_IDENTIFIER") or "rekuest"

    def agent_name(self) -> str:
        """What rekuest knows the agent by (``rekuest.hook_agents[].name``); ``settings.REKUEST_HOOK["AGENT"]`` overrides the declared name."""
        config = getattr(settings, "REKUEST_HOOK", None) or {}
        return config.get("AGENT") or self.name

    def __repr__(self) -> str:
        return f"HookAgent({self.name!r}, actions={list(self._actions)})"
