"""Store the descriptors every row of every hosted structure owes.

    python manage.py restore_descriptors [--all]

A setup job: a row owes its descriptors when it misses a key its structure declares -- a row
written before the column or the key existed, or by a bulk write nobody stamped. Those are
described and stored; everything else is left alone, so a build where nothing is owed reads one
query per structure. ``--all`` describes every row again, for an operator after a describer
changed what a key means.

It announces nothing: the service is stopped while it runs, and rekuest need not be up.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Q

from arkitekt_service.service.service import Service


class Command(BaseCommand):
    help = "Store the descriptors of every row that misses one its structure declares (--all: of every row)."
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--all", action="store_true", help="Describe every row again, not only those missing a key")

    def handle(self, *args: Any, **options: Any) -> None:
        # The services this process serves are the ones its URLconf mounts.
        import_module(settings.ROOT_URLCONF)
        for service in Service.mounted:
            for structure in service.structures.values():
                if not structure.descriptors or structure.describer is None:
                    continue
                rows = structure.model._default_manager.all()
                if not options["all"]:
                    rows = rows.filter(~Q(stored_descriptors__has_keys=list(structure.keys)))
                changed = service.restore(rows.order_by("pk"), emit=False)
                self.stdout.write(f"{structure.identifier}: {changed} restored")
