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
from arkitekt_service.contract.upgrades import Upgrade

#: Names the module holding a service's ``contract``, in its image.
ENVIRONMENT = "ARKITEKT_SERVICE"
#: What the image's build says of where its code came from: the repository, and the commit.
#: Set from build arguments, so that no contract carries a commit by hand.
SOURCE_REPOSITORY = "ARKITEKT_SOURCE_REPOSITORY"
SOURCE_REVISION = "ARKITEKT_SOURCE_REVISION"


class Refused(Exception):
    """A hub's facts this release cannot be configured from, said in words an operator reads.

    Raised by a service's ``render`` for what no schema says: a peer it cannot run without, a
    bucket it was not given.
    """


#: The job every service has: its database brought to the release (``arkitekt-service run migrate``).
MIGRATE = "migrate"
#: What that job would apply, listed and not applied.
PLAN = "plan"
#: What a release does to its data between two versions, for a service that ships it.
UPGRADE = "upgrade"
#: An account that may sign in to the service's admin, made from the environment
#: (``DJANGO_SUPERUSER_USERNAME``, ``_PASSWORD``, ``_EMAIL``): never from the command line,
#: where a password is visible to every process on the machine.
SUPERUSER = "superuser"
#: The jobs that are every service's own, and so cannot be declared by one.
RESERVED = (MIGRATE, PLAN, UPGRADE, SUPERUSER)


@dataclasses.dataclass(frozen=True)
class Job:
    """One of the service's ``manage.py`` commands, offered by name.

    An installer runs it in a container of its own (``arkitekt-service run <name>``),
    and so can an operator; the ones named in :attr:`Contract.setup` are also run as part of
    ``migrate``, in its process. It has to be safe to run again.
    """

    manage: tuple[str, ...]
    """The ``manage.py`` command and its arguments: ``("ensureadmin",)``."""
    summary: str = ""
    """What it does, in a line an operator reads."""


@dataclasses.dataclass(frozen=True)
class Start:
    """How the service is started: the process a container of its image becomes.

    Declared here and nowhere else — there is no script beside it. ``arkitekt-service serve``
    and ``arkitekt-service debug`` replace themselves with it, so it receives the container's
    signals as if it had been started directly.
    """

    command: tuple[str, ...]
    """The program and its arguments: ``("daphne", "-b", "0.0.0.0", "-p", "80", "mikro_server.asgi:application")``."""
    environment: Mapping[str, str] = dataclasses.field(default_factory=lambda: dict[str, str]())
    """What it is started with beside the container's own environment."""


@dataclasses.dataclass(frozen=True)
class Contract:
    """One service, as its image declares it."""

    description: Description
    settings: type[BaseSettings]
    """The service's settings: what a rendered config is read by, and judged against."""
    render: Callable[[Facts], dict[str, JSON]]
    """This release's config, from a hub's facts."""
    serve: Start
    """What serves, and does nothing else (``arkitekt-service serve``)."""
    debug: Start
    """The same for development: the server that reloads on a change (``arkitekt-service debug``)."""
    upgrades: Mapping[int, Upgrade] = dataclasses.field(default_factory=lambda: dict[int, Upgrade]())
    """What the release does to its data on the way into a major, by that major:
    ``{6: upgrades.into_six}`` (see :mod:`arkitekt_service.contract.upgrades`). A release that
    declares any offers the ``upgrade`` job; one that declares none is not stopped for it."""
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

    def _source(self) -> described.Source | None:
        return _built_from(self.description.source)

    def said(self) -> Description:
        """The description an installer reads: the service's own, with every command to run in its image.

        None of them is written into the description by hand. Each is this command with the
        name of what it runs — how the service is started, what writes its config, and its jobs,
        with ``migrate`` (which runs the setup among them) as the one that prepares it — so an
        installer runs exactly what is declared here, and knows none of it.
        """
        runner = ["arkitekt-service"]
        jobs = {
            MIGRATE: described.Job(
                command=[*runner, "run", MIGRATE],
                summary="Wait for the database, apply this release's migrations, then run the service's setup.",
                includes=list(self.setup),
            ),
            PLAN: described.Job(command=[*runner, "run", PLAN], summary="List the migrations `migrate` would apply, and apply nothing."),
            SUPERUSER: described.Job(
                command=[*runner, "run", SUPERUSER],
                summary="Create an account for the service's admin, from DJANGO_SUPERUSER_USERNAME, _PASSWORD and _EMAIL.",
            ),
            **{name: described.Job(command=[*runner, "run", name], summary=job.summary) for name, job in self.jobs.items()},
        }
        if self.upgrades:
            jobs[UPGRADE] = described.Job(command=[*runner, "run", UPGRADE], summary="What this release does to its data between two versions: `--from A --to B`.")
        return self.description.model_copy(
            update={
                "source": self._source(),
                "render": [*runner, "render"],
                "serve": [*runner, "serve"],
                "debug": [*runner, "debug"],
                "jobs": jobs,
                "prepare": MIGRATE,
            }
        )


def _built_from(declared: described.Source | None) -> described.Source | None:
    """The source as the build says it, over what the contract declares for a build that says nothing."""
    repository = os.environ.get(SOURCE_REPOSITORY) or (declared.repository if declared else None)
    if not repository:
        return None
    revision = os.environ.get(SOURCE_REVISION) or (declared.revision if declared else None)
    return described.Source(repository=repository, revision=revision or None, **({"path": declared.path} if declared else {}))


def load() -> Contract:
    """The contract of the service this image is, as ``ARKITEKT_SERVICE`` names its module."""
    module = os.environ.get(ENVIRONMENT)
    if not module:
        raise LookupError(f"{ENVIRONMENT} is not set: this image does not say where its contract is")
    declared: object = getattr(importlib.import_module(module), "contract", None)
    if not isinstance(declared, Contract):
        raise TypeError(f"{module} has no `contract`")
    return declared
