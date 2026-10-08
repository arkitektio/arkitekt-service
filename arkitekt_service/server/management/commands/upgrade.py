"""Run what this release has to do to its data when a deployment moves to it from another.

    python manage.py upgrade --from 5.2.0 --to 6.0.0

For an installer (``arkitekt-service run upgrade``), between stopping the previous server and
starting this one. Exits 0 when the upgrades ran or there were none, non-zero when one failed —
the installer then starts the previous server again. The upgrades are the ones the service's
contract declares (see :mod:`arkitekt_service.contract.upgrades`).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser

from arkitekt_service.contract import contract, upgrades


class Command(BaseCommand):
    help = "Run what this release has to do to its data when moving to it from another version."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--from", dest="left", required=True, help="The version the deployment ran, e.g. 5.2.0.")
        parser.add_argument("--to", dest="reached", required=True, help="The version it moves to, e.g. 6.0.0.")

    def handle(self, *args: object, **options: object) -> None:
        left, reached = str(options["left"]), str(options["reached"])
        try:
            ran = upgrades.run(contract.load().upgrades, left, reached)
        except ValueError as error:
            raise CommandError(str(error)) from error
        if not ran:
            self.stdout.write(f"Nothing to upgrade between {left} and {reached}.")
            return
        self.stdout.write(f"Upgraded from {left} to {reached}: {', '.join(str(step) for step in ran)}.")
