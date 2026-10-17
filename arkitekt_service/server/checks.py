"""System checks: a config file that says something this release does not read, and sharing that
cannot work as bound.

``manage.py migrate`` runs them, so the warnings are in the log of the job that prepares the
database, before anything serves.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from django.apps import AppConfig
from django.core.checks import CheckMessage, Error, Warning, register

from arkitekt_service.contract import contract
from arkitekt_service.contract.unread import unread
from arkitekt_service.server.settings import written

logger = logging.getLogger(__name__)


@register()
def check_unread_configuration(app_configs: Sequence[AppConfig] | None, **kwargs: object) -> list[CheckMessage]:
    """``arkitekt.W001``: a key no setting claims. ``arkitekt.W002``: a key read under a former name."""
    try:
        settings = contract.load().settings
    except (LookupError, TypeError, ImportError):
        return []  # not a service with a contract: nothing to judge the file against
    found = unread(settings, written())
    return [
        *(Warning(f"The configuration sets `{key}`, which this release does not read.", hint="A misspelling, or a key of another release.", id="arkitekt.W001") for key in found.unknown),
        *(Warning(f"The configuration sets `{key}`, which is now `{now}`.", hint="Still read under its former name, for now.", id="arkitekt.W002") for key, now in found.renamed),
    ]


@register()
def check_sharing(app_configs: Sequence[AppConfig] | None, **kwargs: object) -> list[CheckMessage]:
    """``arkitekt.E010``: a closure's lookup that does not resolve. ``arkitekt.W010``: a structure the
    coordination server cannot grant. ``arkitekt.W011``: sharing bound, and no hub to read grants for."""
    from django.core.exceptions import FieldDoesNotExist

    from arkitekt_service.contract.description import GRANTABLE_STRUCTURE
    from arkitekt_service.service.service import Service
    from arkitekt_service.sharing import current_hub, target_field
    from arkitekt_service.trust import hub

    _import_urlconf()  # where the services are declared
    found: list[CheckMessage] = []
    for service in Service.sharing:
        for structure in service.structures.values():
            if not GRANTABLE_STRUCTURE.fullmatch(structure.identifier):
                found.append(
                    Warning(
                        f"{structure.identifier} cannot be shared: the coordination server grants only lowercase structures of bounded length.",
                        hint="Rename the structure, or leave it unshared.",
                        id="arkitekt.W010",
                    )
                )
            for reach, lookups in structure.closures.items():
                for model, lookup in lookups.items():
                    try:
                        target_field(model, lookup)
                    except (FieldDoesNotExist, AttributeError) as error:
                        found.append(
                            Error(
                                f"The reach {reach} of {structure.identifier} covers {getattr(model, '__name__', model)} by {lookup!r}, which does not resolve: {error}",
                                hint="A lookup is a field path from the model to the shared object's id.",
                                id="arkitekt.E010",
                            )
                        )
    if Service.sharing and current_hub() is None and hub() is None:
        found.append(
            Warning(
                "Structures are bound for sharing, but this service knows no hub: scoped members are shut out.",
                hint="Set AUTHENTIKATE.HUB, or give the instance a key the hub's trust bundle lists.",
                id="arkitekt.W011",
            )
        )
    return found


def _import_urlconf() -> None:
    from django.urls import get_resolver

    try:
        get_resolver().url_patterns  # noqa: B018  importing it is the point
    except Exception as error:  # noqa: BLE001  the urls check reports a broken urlconf
        logger.debug("Could not import the urlconf to find the services: %s", error)
