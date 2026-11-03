"""Delete all data of some organizations, and keep the organizations.

    python manage.py prune_organizations --only ORG [ORG ...] [--yes]
    python manage.py prune_organizations --keep ORG [ORG ...] [--yes]

``--only`` prunes exactly the organizations named, ``--keep`` every organization but those
named. An organization is named by its identifier (``<iss>#<org>``) or by the bare ``<org>``
when one organization has it; a name that matches none, or more than one, refuses the run, so
a mistyped ``--keep`` never widens it. Without ``--yes`` it is a dry run that counts what each
organization owns.

What goes is every row of every model with a foreign key to the organization, and what that
cascades to (``arkitekt_service.server.prune``); the organizations, their memberships and their
users stay, and so does the change feed. Stored objects go with their rows. A run cut short is
not undone, and running it again finishes it; a row another organization's data holds on to is
left, named, and the run fails. On a hub:

    konstruktor job run <service> prune_organizations -- --only <org> --yes
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from arkitekt_service.server import prune

SHOWN_BLOCKED = 50


class Command(BaseCommand):
    help = "Delete all data of the organizations named (--only) or not named (--keep); a dry run without --yes."
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser) -> None:
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--only", nargs="+", metavar="ORG", help="Prune exactly these organizations")
        which.add_argument("--keep", nargs="+", metavar="ORG", help="Prune every organization but these")
        parser.add_argument("--yes", action="store_true", help="Delete; without it, only count what would go")
        parser.add_argument("--batch-size", type=int, default=500, help="Rows of one model deleted per transaction (default 500)")
        parser.add_argument("--max-cascade", type=int, default=20_000, help="Rows one transaction may delete, cascades included, before it is split (default 20000)")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            organizations = prune.select(only=options["only"], keep=options["keep"])
        except prune.PruneRefused as refused:
            raise CommandError(str(refused)) from refused
        if not organizations:
            self.stdout.write("No organization to prune")
            return

        if not options["yes"]:
            for identifier, counts in prune.in_scope(organizations).items():
                owned = sum(counts.values())
                self.stdout.write(f"{identifier}: {owned} rows owned" + ("" if owned else " (nothing to prune)"))
                for label, n in sorted(counts.items()):
                    self.stdout.write(f"  {label}: {n}")
            self.stdout.write(f"Dry run for {len(organizations)} organization(s), cascades not counted; pass --yes to delete")
            return

        report = prune.prune(organizations, batch_size=options["batch_size"], max_cascade=options["max_cascade"])
        for identifier in report.organizations:
            counts = report.deleted.get(identifier, {})
            self.stdout.write(f"{identifier}: {sum(counts.values())} rows deleted")
            for label, n in sorted(counts.items()):
                self.stdout.write(f"  {label}: {n}")
        for line in report.oversized:
            self.stdout.write(self.style.WARNING(f"Deleted in one transaction although it cascades past --max-cascade: {line}"))
        if report.blocked:
            for blocked in report.blocked[:SHOWN_BLOCKED]:
                self.stderr.write(str(blocked))
            if len(report.blocked) > SHOWN_BLOCKED:
                self.stderr.write(f"... and {len(report.blocked) - SHOWN_BLOCKED} more")
            raise CommandError(f"{len(report.blocked)} row(s) left: data outside the pruned organizations holds on to them")
        if report.total:
            self.stdout.write(self.style.SUCCESS(f"Pruned {len(report.organizations)} organization(s): {report.total} rows deleted"))
        else:
            self.stdout.write("Nothing to prune")
