"""What a service hosts: its structures, and the descriptors of each.

A structure is a model the service holds, known to the hub by its identifier
(``@mikro/arraydataset``). Its declaration is the one place that says which descriptors the
objects carry (``descriptors``) and how to compute them (``describe``): the manifest rekuest
reads, the service's own ``descriptors`` GraphQL field and any signal declared for the structure
all read it.

Descriptors are stored on the object (:class:`~arkitekt_service.service.described.Described`),
computed by ``describe`` whenever it is saved or restored, and read from there by everything
else: a ``requires`` filter (:mod:`arkitekt_service.service.requires`), a signal, the
``descriptors`` field. Nothing is computed only when asked.

A structure only HAS descriptors, and declaring one announces nothing: what is signalled about it
is a separate declaration (``Service.model_signal``). What an action requires of an input or
provides with an output is said on that action's ports, in terms of these keys.

A structure's objects can be shared one at a time, with members who see nothing else of the
organization. How far a share goes is a *reach*: ``self`` (the object alone, which every
structure has) or one the structure declares (``derived``: also what was computed from it).
What a reach covers is its *closure*: for each model, the lookup from a row to the id of the
shared object (``{DataArray: "dataset_id"}``). :mod:`arkitekt_service.sharing` narrows reads by
them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

IDENTIFIER = re.compile(r"^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: What a descriptor's value is; ``ANY`` when the service does not say.
DESCRIPTOR_TYPES = ("ANY", "INT", "FLOAT", "STRING", "BOOL", "LIST")


def check_identifier(identifier: str) -> str:
    if not IDENTIFIER.match(identifier):
        raise ValueError(f"A structure identifier looks like @package/key, not {identifier!r}")
    return identifier


@dataclass(frozen=True)
class Descriptor:
    """One descriptor of a structure's objects, e.g. ``Descriptor("@mikro/n_channels", "INT")``."""

    key: str
    type: str = "ANY"
    description: str | None = None

    def __post_init__(self) -> None:
        if self.type not in DESCRIPTOR_TYPES:
            raise ValueError(f"A descriptor type is one of {DESCRIPTOR_TYPES}, not {self.type!r}")

    def manifest(self) -> dict[str, Any]:
        return {"key": self.key, "type": self.type, "description": self.description}


#: The reach every structure has: the shared object itself.
SELF_REACH = "self"


@dataclass(frozen=True)
class Reach:
    """How far a share of one object goes: ``Reach("derived", includes=("self",))``."""

    key: str
    label: str | None = None
    description: str | None = None
    includes: tuple[str, ...] = ()

    def manifest(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "description": self.description, "includes": list(self.includes)}


#: For each reach, the lookup from each covered model's rows to the shared object's id.
Closures = Mapping[str, Mapping[Any, str]]


@dataclass(frozen=True)
class Structure:
    """A hosted structure: a model, its identifier, the descriptors of its objects, and how far a share of one reaches."""

    identifier: str
    model: Any
    label: str | None
    description: str | None
    descriptors: tuple[Descriptor, ...]
    describer: Callable[[Any], dict[str, Any]] | None
    #: Its reaches as declared. ``self`` is always one, declared or not.
    reaches: tuple[Reach, ...] = ()
    #: What each reach covers beyond the shared row itself, which ``self`` always does.
    closures: Closures = field(default_factory=dict, compare=False)

    @property
    def reach_keys(self) -> tuple[str, ...]:
        """Every reach of the structure, ``self`` first."""
        return (SELF_REACH, *(reach.key for reach in self.reaches if reach.key != SELF_REACH))

    def covered(self, reach: str) -> set[str]:
        """The reaches a share ``#reach`` covers: it, what it includes, and ``self``. An unknown reach is ``self``."""
        includes = {SELF_REACH: (), **{r.key: r.includes for r in self.reaches}}
        if reach not in includes:
            return {SELF_REACH}
        covered, frontier = set(), [reach]
        while frontier:
            key = frontier.pop()
            if key not in covered:
                covered.add(key)
                frontier.extend(includes.get(key, ()))
        return covered | {SELF_REACH}

    def lookups(self, model: Any, reach: str) -> list[str]:
        """The lookups from ``model``'s rows to the id of an object of this structure shared ``#reach``."""
        found = []
        if isinstance(model, type) and isinstance(self.model, type) and issubclass(model, self.model):
            found.append("pk")
        for key in sorted(self.covered(reach)):
            for covered, lookup in self.closures.get(key, {}).items():
                if isinstance(model, type) and issubclass(model, covered) and lookup not in found:
                    found.append(lookup)
        return found

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.descriptors)

    def compute(self, obj: Any) -> dict[str, Any]:
        """Run the describer on ``obj``: what is stored on it (``stored_descriptors``) when it is saved or restored."""
        return dict(self.describer(obj)) if self.describer is not None else {}

    def describe(self, obj: Any) -> dict[str, Any]:
        """The descriptors of ``obj`` as they are stored on it — what signals for it carry and its ``descriptors`` field returns."""
        return dict(getattr(obj, "stored_descriptors", None) or {})

    def manifest(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "label": self.label,
            "description": self.description,
            "descriptors": [d.manifest() for d in self.descriptors],
            "reaches": [r.manifest() for r in self.reaches],
        }
