"""A service's declaration: what it says of itself, and how it writes its config."""

from __future__ import annotations

import dataclasses
import importlib
import os
from collections.abc import Callable

from pydantic_settings import BaseSettings

from arkitekt_service.contract.description import Description
from arkitekt_service.contract.facts import Facts
from arkitekt_service.contract.json_types import JSON

#: Names the module holding a service's ``contract``, in its image.
ENVIRONMENT = "ARKITEKT_SERVICE"


class Refused(Exception):
    """A hub's facts this release cannot be configured from, said in words an operator reads.

    Raised by a service's ``render`` for what no schema says: a peer it cannot run without, a
    bucket it was not given.
    """


@dataclasses.dataclass(frozen=True)
class Contract:
    """One service, as its image declares it."""

    description: Description
    settings: type[BaseSettings]
    """The service's settings: what a rendered config is read by, and judged against."""
    render: Callable[[Facts], dict[str, JSON]]
    """This release's config, from a hub's facts."""
    upgrades: bool = False
    """Whether the release ships ``manage.py upgrade``."""
    setup: tuple[tuple[str, ...], ...] = ()
    """What else the service's database needs before the service starts on it, as ``manage.py``
    commands run after the migrations, in order: ``(("ensureadmin",), ("ensurerepos",))``.
    Each has to be safe to run again."""


def load() -> Contract:
    """The contract of the service this image is, as ``ARKITEKT_SERVICE`` names its module."""
    module = os.environ.get(ENVIRONMENT)
    if not module:
        raise LookupError(f"{ENVIRONMENT} is not set: this image does not say where its contract is")
    declared: object = getattr(importlib.import_module(module), "contract", None)
    if not isinstance(declared, Contract):
        raise TypeError(f"{module} has no `contract`")
    return declared
