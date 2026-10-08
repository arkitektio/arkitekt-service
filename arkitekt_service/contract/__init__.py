"""What a service image answers a hub's installer.

A hub is a set of service images run together by an installer. The installer knows the hub —
where the database is, which other services run, which keys they trust — and should know
nothing about a service beyond what the service's own image tells it. This package is how an
image tells it: one entry point (``arkitekt-service <verb>``), the same in every image.

==============  ================================================================
``describe``    what the service needs from a hub and offers to it
``render``      this release's config, written from the hub's facts
``check``       whether this release reads a config as written
``migrate``     the release's database migrations, as a step with an answer
``upgrade``     what the release does to its data between two versions
==============  ================================================================

A service declares itself once, in a module named by ``ARKITEKT_SERVICE`` (set in its image)::

    contract = Contract(description=..., settings=Settings, render=render)

See :mod:`arkitekt_service.contract.facts` for what a hub says, :mod:`arkitekt_service.contract.description` for what a
service says, and :mod:`arkitekt_service.contract.cli` for the verbs and their exit codes.
"""

from arkitekt_service.contract import blocks
from arkitekt_service.contract.contract import Contract, Job, Refused, Start
from arkitekt_service.contract.description import Description, Descriptor, Hosts, Needs, Offers, Scope, Sidecar, Signal, Source, Structure
from arkitekt_service.contract.facts import Facts, Peer
from arkitekt_service.contract.json_types import JSON
from arkitekt_service.contract.upgrades import Upgrade

__all__ = [
    "JSON",
    "Contract",
    "Description",
    "Descriptor",
    "Facts",
    "Hosts",
    "Job",
    "Needs",
    "Offers",
    "Peer",
    "Refused",
    "Scope",
    "Sidecar",
    "Signal",
    "Source",
    "Start",
    "Structure",
    "Upgrade",
    "blocks",
]
