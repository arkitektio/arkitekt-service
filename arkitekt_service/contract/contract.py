"""A service's declaration: what it says of itself, and how it writes its config."""

from __future__ import annotations

import dataclasses
import importlib
import os
from collections.abc import Callable, Mapping

from pydantic_settings import BaseSettings

from arkitekt_service.contract import description as described
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


#: The job every service has: its database brought to the release (``arkitekt-service migrate``).
MIGRATE = "migrate"
#: What that job would apply, listed and not applied (``migrate --plan``).
PLAN = "plan"
#: What a release does to its data between two versions, for a service that ships it.
UPGRADE = "upgrade"
#: The jobs that are every service's own, and so cannot be declared by one.
RESERVED = (MIGRATE, PLAN, UPGRADE)


@dataclasses.dataclass(frozen=True)
class Job:
    """One of the service's ``manage.py`` commands, offered by name.

    An installer runs it in a container of its own (``arkitekt-service job <name>``),
    and so can an operator; the ones named in :attr:`Contract.setup` are also run as part of
    ``migrate``, in its process. It has to be safe to run again.
    """

    manage: tuple[str, ...]
    """The ``manage.py`` command and its arguments: ``("ensureadmin",)``."""
    summary: str = ""
    """What it does, in a line an operator reads."""


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
    jobs: Mapping[str, Job] = dataclasses.field(default_factory=lambda: dict[str, Job]())
    """What can be run in the image beside its start, by name: ``{"ensureadmin": Job(("ensureadmin",),
    "Create the operator account")}``."""
    setup: tuple[str, ...] = ()
    """What else the service's database needs before the service starts on it: the names of
    the :attr:`jobs` that ``migrate`` runs after the migrations, in order."""

    def __post_init__(self) -> None:
        """A setup that names a job nobody declared is refused where it is written."""
        taken = [name for name in RESERVED if name in self.jobs]
        if taken:
            raise ValueError(f"{', '.join(taken)} is every service's own job: it cannot be declared again")
        unknown = [name for name in self.setup if name not in self.jobs]
        if unknown:
            raise ValueError(f"setup names {', '.join(unknown)}, which {self.description.name} does not declare as a job")

    def said(self) -> Description:
        """The description an installer reads: the service's own, with what can be run in its image.

        The jobs are not written into the description by hand: they are the ones declared here,
        each as the command that runs it, with ``migrate`` — which runs the setup among them —
        as the one that prepares the service.
        """
        runner = ["arkitekt-service"]
        jobs = {
            MIGRATE: described.Job(
                command=[*runner, MIGRATE],
                summary="Wait for the database, apply this release's migrations, then run the service's setup.",
                includes=list(self.setup),
            ),
            PLAN: described.Job(command=[*runner, MIGRATE, "--plan"], summary="List the migrations `migrate` would apply, and apply nothing."),
            **{name: described.Job(command=[*runner, "job", name], summary=job.summary) for name, job in self.jobs.items()},
        }
        if self.upgrades:
            jobs[UPGRADE] = described.Job(command=[*runner, UPGRADE], summary="What this release does to its data between two versions: `--from A --to B`.")
        return self.description.model_copy(update={"jobs": jobs, "prepare": MIGRATE})


def load() -> Contract:
    """The contract of the service this image is, as ``ARKITEKT_SERVICE`` names its module."""
    module = os.environ.get(ENVIRONMENT)
    if not module:
        raise LookupError(f"{ENVIRONMENT} is not set: this image does not say where its contract is")
    declared: object = getattr(importlib.import_module(module), "contract", None)
    if not isinstance(declared, Contract):
        raise TypeError(f"{module} has no `contract`")
    return declared
