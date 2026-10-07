"""Validate the service's configuration and print it as this release reads it, secrets masked.

    python manage.py validate_settings [--strict]

Exits 1 on a configuration the release cannot be started with. With ``--strict`` it also exits
78 when the file sets a key the release does not read: what an installer asks before it moves a
hub to a release.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.management.base import BaseCommand, CommandParser
from pydantic import ValidationError

from arkitekt_service.contract import contract
from arkitekt_service.contract.unread import unread
from arkitekt_service.server.settings import config_path, written

#: ``--strict``'s no: the configuration sets a key this release does not read (sysexits' EX_CONFIG).
NOT_READ = 78

#: Leaf keys whose values are secrets and are never printed in the clear.
SECRET_HINTS = ("password", "secret_key", "secret", "private_key", "access_key")


def is_secret(key: object) -> bool:
    name = str(key).lower()
    return any(hint in name for hint in SECRET_HINTS)


def masked(value: object) -> str:
    return f"**** (len={len(value)})" if isinstance(value, str) else "****"


def lines(data: object, *, indent: int = 1, secret: bool = False) -> list[str]:
    """``data`` (a ``model_dump()``) as an indented tree, one line a key.

    ``secret`` masks every leaf below a secret-named block; a leaf whose own key looks secret is
    masked by itself.
    """
    pad = "  " * indent
    if isinstance(data, dict):
        out: list[str] = []
        for key, value in data.items():  # pyright: ignore[reportUnknownVariableType]
            hidden = secret or is_secret(key)
            if isinstance(value, (dict, list)):
                out += [f"{pad}{key}:", *lines(value, indent=indent + 1, secret=hidden)]
            else:
                out.append(f"{pad}{key}: {masked(value) if hidden else repr(value)}")
        return out
    if isinstance(data, list):
        out = []
        for index, item in enumerate(data):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            if isinstance(item, (dict, list)):
                out += [f"{pad}- item {index}:", *lines(item, indent=indent + 1, secret=secret)]
            else:
                out.append(f"{pad}- {masked(item) if secret else repr(item)}")
        return out
    return [f"{pad}{masked(data) if secret else repr(data)}"]


class Command(BaseCommand):
    help = "Validate the service configuration (YAML + env) and print the resolved, redacted settings."
    # A config validator needs no model, URL or database checks (and they may fail for reasons
    # that are not the config's): only the configuration is exercised.
    requires_system_checks: ClassVar[Any] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """``--strict``: a key that is not read as written fails the command."""
        parser.add_argument("--strict", action="store_true", help="Also fail on keys this release does not read as written.")

    def handle(self, *args: Any, **options: Any) -> None:
        declared = contract.load().settings
        path = config_path()
        try:
            settings = declared()
        except ValidationError as error:
            self.stderr.write(self.style.ERROR(f"Invalid configuration (source: {path})"))
            for problem in error.errors():
                self.stderr.write(f"  {'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}")
            raise SystemExit(1) from None

        self.stdout.write(self.style.SUCCESS(f"Configuration valid (source: {path})"))
        for line in lines(settings.model_dump()):
            self.stdout.write(line)

        found = unread(declared, written())
        for key in found.unknown:
            self.stdout.write(self.style.WARNING(f"not read: {key}"))
        for key, now in found.renamed:
            self.stdout.write(self.style.WARNING(f"renamed: {key} is now {now}"))
        if found.unknown and options["strict"]:
            self.stderr.write(self.style.ERROR(f"{path} sets keys this release does not read"))
            raise SystemExit(NOT_READ)
