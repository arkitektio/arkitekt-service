"""What a service tells a hub about itself: what it needs, what it offers, what it runs beside,
and what to run to prepare it.

Printed by ``describe``, before the service has any config. An installer reads it instead of
knowing the service: which buckets to make, whether to mint it a key, what to tell the
coordination server about it, which of its endpoints other services are wired to.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Said(BaseModel):
    """A closed block of a description."""

    model_config = ConfigDict(extra="forbid")


class Scope(Said):
    """A permission the service asks the coordination server to define."""

    key: str
    description: str


class Needs(Said):
    """What the service needs a hub to provide."""

    database: bool = True
    redis: bool = True
    storage: list[str] = Field(default_factory=list, description="A bucket for each of these purposes (media, zarr, parquet, bigfile, …).")
    instance_key: bool = Field(default=False, description="A key of its own, vouched for by the hub: it signs or verifies requests between services.")
    admin: bool = Field(default=True, description="It creates an operator account from the hub's admin.")
    peers: list[str] = Field(default_factory=list, description="Parts of the hub it uses when they are there (rekuest, ollama, livekit).")
    secrets: list[str] = Field(default_factory=list, description="Key files it needs mounted, by name (fernet).")
    scopes: list[Scope] = Field(default_factory=list)
    roles: list[Scope] = Field(default_factory=list)


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
    """A process the service does not run without, released with it as an image of its own."""

    name: str = Field(description="What a hub calls it beside the service: `takt` runs as `<service>-takt`.")
    image: str = Field(description="Its image, from the service's own: `{repository}` and `{tag}` stand for the parts of the image this description came from (`{repository}-takt:{tag}`).")
    summary: str = ""


class Description(Said):
    """A service, as its image describes it."""

    contract: Literal[2] = Field(default=2, description="The version of this contract.")
    name: str = Field(description="The service's name: what a hub calls it by default.")
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
    requires: dict[str, str] = Field(default_factory=dict, description="Peers this release only works beside in certain versions, as version specifiers (rekuest: '>=6').")
    upgrade_from: str | None = Field(default=None, description="The oldest version a deployment can be moved to this release from directly. Older ones have to stop at a release in between.")
