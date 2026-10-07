"""System checks: a config file that says something this release does not read.

``manage.py migrate`` runs them, so the warnings are in the log of the job that prepares the
database, before anything serves.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core.checks import CheckMessage, Warning, register

from arkitekt_service.contract import contract
from arkitekt_service.contract.unread import unread
from arkitekt_service.server.settings import written


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
