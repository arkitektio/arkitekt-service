"""What a service tells a hub about itself: what it needs, what it offers, what it runs beside,
and what to run to prepare it.

Printed by ``describe``, before the service has any config. An installer reads it instead of
knowing the service: which buckets to make, whether to mint it a key, what to tell the
coordination server about it, which of its endpoints other services are wired to.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: A name Postgres takes as it is written: a lowercase letter, then lowercase letters, digits
#: and underscores. Nothing that would have to be quoted, so that whoever creates a database
#: or a role can say its name anywhere without escaping it. A service's name and the names
#: of its databases are both held to it, because a hub puts the two together.
PLAIN_NAME = re.compile(r"[a-z][a-z0-9_]*")

#: The database every service has unless it says otherwise.
MAIN_DATABASE = "main"

#: How long a Postgres name may be (``NAMEDATALEN`` - 1). A hub calls a service's database
#: ``<service>_<name>``, so the name a service picks has to leave room for its own.
POSTGRES_NAME_LENGTH = 63


class Said(BaseModel):
    """A closed block of a description."""

    model_config = ConfigDict(extra="forbid")


class Scope(Said):
    """A permission the service asks the coordination server to define."""

    key: str
    description: str


class Needs(Said):
    """What the service needs a hub to provide."""

    databases: list[str] = Field(
        default_factory=lambda: [MAIN_DATABASE],
        description="A database for each of these names, in the hub's Postgres. The hub calls each `<service>_<name>`; the service is handed them by the name it gave. Empty for a service that keeps nothing in Postgres.",
    )
    redis: bool = True
    storage: list[str] = Field(default_factory=list, description="A bucket for each of these purposes (media, zarr, parquet, bigfile, …).")
    instance_key: bool = Field(default=False, description="A key of its own, vouched for by the hub: it signs or verifies requests between services.")
    admin: bool = Field(default=True, description="It creates an operator account from the hub's admin.")
    peers: list[str] = Field(default_factory=list, description="Parts of the hub it uses when they are there (rekuest, ollama, livekit).")
    secrets: list[str] = Field(default_factory=list, description="Key files it needs mounted, by name (fernet).")
    scopes: list[Scope] = Field(default_factory=list)
    roles: list[Scope] = Field(default_factory=list)

    @field_validator("databases")
    @classmethod
    def _databases_are_named_as_postgres_names_them(cls, names: list[str]) -> list[str]:
        for name in names:
            if not PLAIN_NAME.fullmatch(name) or len(name) > POSTGRES_NAME_LENGTH:
                raise ValueError(f"`{name}` is not a name Postgres takes unquoted: a lowercase letter, then lowercase letters, digits and underscores, {POSTGRES_NAME_LENGTH} characters at most")
        if len(set(names)) != len(names):
            raise ValueError(f"a database is named twice: {', '.join(sorted({n for n in names if names.count(n) > 1}))}")
        return names


class Offers(Said):
    """What the service offers a hub."""

    health: str = Field(default="ht", description="The path, under the service's own, that answers whether it works.")
    endpoints: dict[str, str] = Field(default_factory=dict, description="Endpoints other services are wired to, by kind, as paths under the service's own (rekuest_service: _rekuest/service).")


class Job(Said):
    """Something an installer can run in the service's image, as a container of its own.

    A job is named, so that the same thing is asked for the same way everywhere: by an
    installer preparing a hub (``prepare``), and by an operator who wants one of them run
    again (``konstruktor job run <service> <job>``).
    """

    command: list[str] = Field(description="What to run, in a container of the image, with the service's config.")
    summary: str = Field(default="", description="What it does, in a line an operator reads.")
    includes: list[str] = Field(default_factory=list, description="Other jobs this one runs as part of itself, in order: `migrate` includes the service's setup.")


class Sidecar(Said):
    """A process that runs beside the service, as an image of its own.

    Either one the service does not run without (rekuest's takt), or one it drives when a hub
    has the use for it (``optional``: Lok's mesh control server, on a hub with a mesh).
    """

    name: str = Field(description="What a hub calls it beside the service: `takt` runs as `<service>-takt`.")
    image: str = Field(description="Its image, from the service's own: `{repository}` and `{tag}` stand for the parts of the image this description came from (`{repository}-takt:{tag}`).")
    summary: str = ""
    optional: bool = Field(default=False, description="Whether the service runs without it: an installer starts an optional one only on a hub that asked for what it brings.")


#: What a structure is known by across a hub: ``@mikro/arraydataset``.
STRUCTURE_IDENTIFIER = re.compile(r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

#: What a descriptor's value is; ``ANY`` when the service does not say.
DescriptorType = Literal["ANY", "INT", "FLOAT", "STRING", "BOOL", "LIST"]

#: What can be announced about an object.
SignalKind = Literal["CREATED", "UPDATED", "DELETED"]


class Descriptor(Said):
    """One descriptor of a structure's objects: ``Descriptor(key="@mikro/n_channels", type="INT")``."""

    key: str
    type: DescriptorType = "ANY"
    description: str | None = None


class Structure(Said):
    """A kind of object the service holds, known to the hub by its identifier."""

    identifier: str = Field(description="`@package/key`: what an action's port asks for, and what a signal is about.")
    label: str | None = None
    description: str | None = None
    descriptors: list[Descriptor] = Field(default_factory=list, description="What is known of each of its objects, by key: what an action may require of an input, and a trigger test.")

    @field_validator("identifier")
    @classmethod
    def _the_identifier_is_one(cls, identifier: str) -> str:
        if not STRUCTURE_IDENTIFIER.fullmatch(identifier):
            raise ValueError(f"a structure identifier looks like @package/key, not {identifier!r}")
        return identifier

    @field_validator("descriptors")
    @classmethod
    def _a_key_is_said_once(cls, descriptors: list[Descriptor]) -> list[Descriptor]:
        keys = [descriptor.key for descriptor in descriptors]
        if len(set(keys)) != len(keys):
            raise ValueError("a descriptor key is declared twice")
        return descriptors


class Signal(Said):
    """What the service announces about a structure's objects, and with which descriptors."""

    identifier: str
    kinds: list[SignalKind] = Field(default_factory=lambda: ["CREATED"], min_length=1)
    descriptors: list[str] = Field(default_factory=list, description="The descriptor keys an announcement carries: what a trigger can test.")
    description: str | None = None


class Hosts(Said):
    """What exists on a hub because the service is there: the structures it holds and the
    signals it sends about them.

    Said by the image, so that a hub knows it without asking the running service: an installer
    hands it to the hub's rekuest, which catalogues it. Hosting and announcing are two
    declarations: a structure with no signal is hosted silently.
    """

    structures: list[Structure] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _each_is_said_once(self) -> Hosts:
        for what, identifiers in (("structure", [s.identifier for s in self.structures]), ("signal", [s.identifier for s in self.signals])):
            twice = sorted({identifier for identifier in identifiers if identifiers.count(identifier) > 1})
            if twice:
                raise ValueError(f"the {what} {', '.join(twice)} is declared twice")
        return self

    def structure(self, identifier: str) -> Structure | None:
        return next((structure for structure in self.structures if structure.identifier == identifier), None)

    def signal(self, identifier: str) -> Signal | None:
        return next((signal for signal in self.signals if signal.identifier == identifier), None)


class Source(Said):
    """Where the code in the image came from, and where it sits in it.

    What an installer needs to run the service from a checkout instead: the repository to
    clone, the commit the image was built from, and the folder a checkout is mounted over.
    """

    repository: str = Field(description="What to clone: `https://github.com/arkitektio/mikro-server-next`.")
    revision: str | None = Field(default=None, description="The commit the image was built from, when the build said.")
    path: str = Field(default="/workspace", description="Where the service's code sits in the image: a checkout is mounted here.")


class Description(Said):
    """A service, as its image describes it."""

    contract: Literal[2] = Field(default=2, description="The version of this contract.")
    name: str = Field(
        description="The service's name: what a hub calls it, its path at the gateway, and the first half of its databases' names. Lowercase letters, digits and underscores, starting with a letter: no hyphen."
    )
    identifier: str = Field(description="What the service is registered as at the coordination server, and what a client asks for: `live.arkitekt.mikro`.")
    summary: str = ""
    needs: Needs = Field(default_factory=Needs)
    offers: Offers = Field(default_factory=Offers)
    render: list[str] = Field(
        default_factory=lambda: ["arkitekt-service", "render"],
        description="What writes this release's config: run in the image with the hub's facts at `/hub/facts.yaml` (and what the operator set at `/hub/overrides.yaml`), it prints the config, or exits 78 with its reason.",
    )
    serve: list[str] = Field(
        default_factory=lambda: ["arkitekt-service", "serve"],
        description="What a container of the image runs to serve, and nothing else: an installer writes this as the service's command. Also the image's own `CMD`.",
    )
    debug: list[str] = Field(
        default_factory=lambda: ["arkitekt-service", "debug"], description="The same for development: the server that reloads on a change. Serves, and nothing else, like `serve`."
    )
    jobs: dict[str, Job] = Field(
        default_factory=dict,
        description="What can be run in the image beside its start, by name. The start itself is the image's own command (its `CMD`): a container of the image serves, and does nothing else.",
    )
    prepare: str | None = Field(
        default=None,
        description="The job that brings the service's database to this release: run once per build, before the first start and before an update's. Null when there is nothing to prepare.",
    )
    sidecars: list[Sidecar] = Field(default_factory=list)
    hosts: Hosts = Field(default_factory=Hosts, description="The structures the service holds and the signals it sends about them.")
    source: Source | None = Field(default=None, description="Where the image's code came from. Null for an image that does not say: it cannot be run from a checkout without being told one.")
    requires: dict[str, str] = Field(default_factory=dict, description="Peers this release only works beside in certain versions, as version specifiers (rekuest: '>=6').")
    upgrade_from: str | None = Field(default=None, description="The oldest version a deployment can be moved to this release from directly. Older ones have to stop at a release in between.")

    @field_validator("name")
    @classmethod
    def _the_name_is_plain(cls, name: str) -> str:
        if not PLAIN_NAME.fullmatch(name):
            raise ValueError(
                f"`{name}` cannot be a service's name: a lowercase letter, then lowercase letters, digits "
                "and underscores. No hyphen: its databases are called after it, and Postgres would have to quote one"
            )
        return name

    @model_validator(mode="after")
    def _its_databases_names_fit(self) -> Description:
        for database in self.needs.databases:
            called = f"{self.name}_{database}"
            if len(called) > POSTGRES_NAME_LENGTH:
                raise ValueError(f"`{called}` is longer than the {POSTGRES_NAME_LENGTH} characters Postgres keeps of a name")
        return self
