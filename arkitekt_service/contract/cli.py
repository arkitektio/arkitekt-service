"""The verbs an installer runs in a service's image: ``python -m arkitekt_service <verb>``.

``describe``
    Prints the service's :class:`~arkitekt_service.contract.description.Description` as JSON. Needs no
    config. An image that cannot answer this has no contract, and an installer treats it as it
    treated images before there was one.

``render [--facts FILE] [--overrides FILE]``
    Prints this release's config, as YAML, written from a hub's facts (``/hub/facts.yaml``)
    with what the hub's operator set laid over it (``/hub/overrides.yaml``, if there). The
    result is loaded the way the service loads it at start before it is printed, so what comes
    out is a config this release starts on.

``check [--config FILE]``
    Judges a config as it stands — the file the service would start on.

``job NAME [ARGS...]``
    Runs one of the jobs the service declares (``describe`` lists them): a ``manage.py`` command
    by the name the service gave it. An installer runs these for an operator who asks for one
    again; the ones a service names as its setup also run as part of ``migrate``.

``migrate [--plan]``
    Everything the service's database needs before the service starts on it: waits for the
    database, applies the release's migrations (``manage.py migrate``), then runs the jobs the
    service named as its setup (an admin account, seeded rows), all in one process. An installer runs it once
    per build, before the first start and before an update's — which is why a service's own
    start does nothing but serve. ``--plan`` lists the migrations that would run, and runs
    nothing.

``upgrade --from A --to B``
    What the release does to its data between two versions (``manage.py upgrade``), if it
    ships anything of the kind.

Exit codes: ``0`` done. ``78`` (``EX_CONFIG``) is this release's own no — facts it cannot be
configured from, an override it does not read, an invalid config — with the reason on stderr,
and nothing on stdout. Anything else is the command failing.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
import tempfile
import typing
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from arkitekt_service.contract.contract import Contract, Refused, load
from arkitekt_service.contract.facts import Facts
from arkitekt_service.contract.json_types import JSON
from arkitekt_service.contract.merge import merge
from arkitekt_service.contract.unread import unread

#: The release's own no (sysexits' EX_CONFIG).
REFUSED = 78

FACTS = "/hub/facts.yaml"
OVERRIDES = "/hub/overrides.yaml"
#: What every service reads its config file's path from.
CONFIG_FILE = "ARKITEKT_CONFIG_FILE"


class No(Exception):
    """This release's refusal, as the lines to say."""

    def __init__(self, headline: str, reasons: Sequence[str]) -> None:
        """What is refused, and each reason."""
        super().__init__(headline)
        self.headline = headline
        self.reasons = list(reasons)


def _document(path: Path, what: str) -> dict[str, JSON]:
    """A YAML file that holds a mapping."""
    try:
        loaded = typing.cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as error:
        raise No(f"{what} could not be read", [f"{path}: {error}"]) from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise No(f"{what} is not a mapping", [str(path)])
    return typing.cast("dict[str, JSON]", loaded)


def _errors(error: ValidationError) -> list[str]:
    return [f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}" for problem in error.errors()]


def judge(contract: Contract, document: dict[str, JSON]) -> None:
    """Refuse a config this release does not read as written, or does not start on.

    Loaded exactly as at start: from a file named by ``ARKITEKT_CONFIG_FILE``, with the
    environment over it.
    """
    found = unread(contract.settings, document)
    if found.unknown:
        raise No("this release does not read", found.unknown)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as file:
        yaml.safe_dump(document, file, sort_keys=False)
        file.flush()
        before = os.environ.get(CONFIG_FILE)
        os.environ[CONFIG_FILE] = file.name
        try:
            contract.settings()
        except ValidationError as error:
            raise No("this release cannot start on that config", _errors(error)) from error
        finally:
            if before is None:
                del os.environ[CONFIG_FILE]
            else:
                os.environ[CONFIG_FILE] = before


def render(contract: Contract, facts: Path, overrides: Path) -> dict[str, JSON]:
    """This release's config from the facts at ``facts``, with ``overrides`` laid over if there."""
    try:
        said = Facts.model_validate(_document(facts, "the hub's facts"))
    except ValidationError as error:
        raise No("this release does not understand the hub's facts", _errors(error)) from error
    try:
        document = contract.render(said)
    except Refused as error:
        raise No("this release cannot be configured for this hub", [str(error)]) from error
    if overrides.is_file():
        document = merge(document, _document(overrides, "what the operator set"))
    judge(contract, document)
    return document


def _manage(*arguments: str) -> int:
    """Hand over to the service's own ``manage.py``: its exit code is the answer."""
    os.execvp(sys.executable, [sys.executable, "manage.py", *arguments])


def manage(*arguments: str) -> int:
    """Run one of the service's ``manage.py`` commands in this process; its exit code.

    In this process, because the steps of a preparation are one job: each would otherwise
    start Python and load the whole service again before doing its own little.
    """
    before = sys.argv
    sys.argv = ["manage.py", *arguments]
    try:
        runpy.run_path("manage.py", run_name="__main__")
    except SystemExit as stopped:
        code = stopped.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        sys.argv = before
    return 0


def prepare(contract: Contract) -> int:
    """Bring the database to this release: wait for it, migrate, then the service's setup.

    One job, run by whoever starts the service, once per build: a service's own start does
    none of it. It waits until the database takes a connection and no longer. Stops at the
    first step that fails, with that step's exit code.
    """
    steps: list[tuple[str, ...]] = [("wait_for_database", "-s", "0"), ("migrate", "--noinput"), *(contract.jobs[name].manage for name in contract.setup)]
    for step in steps:
        print(f"=> manage.py {' '.join(step)}", flush=True)
        code = manage(*step)
        if code != 0:
            return code
    return 0


def job(contract: Contract, name: str, extra: Sequence[str]) -> int:
    """Run one of the service's declared jobs, with whatever was passed after its name."""
    declared = contract.jobs.get(name)
    if declared is None:
        offered = ", ".join(sorted(contract.jobs)) or "none"
        raise No(f"there is no job `{name}`", [f"this release offers: {offered}"])
    return manage(*declared.manage, *extra)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one verb; the exit code is its answer."""
    parser = argparse.ArgumentParser(prog="python -m arkitekt_service", description="What this service's image answers a hub's installer.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("describe", help="What the service needs from a hub and offers to it, as JSON.")
    rendering = verbs.add_parser("render", help="This release's config, from a hub's facts.")
    rendering.add_argument("--facts", type=Path, default=Path(FACTS))
    rendering.add_argument("--overrides", type=Path, default=Path(OVERRIDES))
    checking = verbs.add_parser("check", help="Whether this release reads a config as written.")
    checking.add_argument("--config", type=Path, default=None)
    migrating = verbs.add_parser("migrate", help="The release's database migrations.")
    migrating.add_argument("--plan", action="store_true", help="List what would run, and run nothing.")
    running = verbs.add_parser("job", help="One of the jobs the service declares, by name.")
    running.add_argument("name")
    running.add_argument("extra", nargs=argparse.REMAINDER, help="Passed on to the job.")
    upgrading = verbs.add_parser("upgrade", help="What the release does to its data between two versions.")
    upgrading.add_argument("--from", dest="left", required=True)
    upgrading.add_argument("--to", dest="reached", required=True)
    asked = parser.parse_args(arguments)

    contract = load()
    verb: str = asked.verb  # pyright: ignore[reportAny]  argparse's namespace
    try:
        if verb == "describe":
            print(contract.said().model_dump_json(indent=2))
        elif verb == "render":
            facts: Path = asked.facts  # pyright: ignore[reportAny]
            overrides: Path = asked.overrides  # pyright: ignore[reportAny]
            print(yaml.safe_dump(render(contract, facts, overrides), sort_keys=False), end="")
        elif verb == "check":
            given: Path | None = asked.config  # pyright: ignore[reportAny]
            path = given or Path(os.environ.get(CONFIG_FILE, "config.yaml"))
            judge(contract, _document(path, "the config"))
        elif verb == "migrate":
            plan: bool = asked.plan  # pyright: ignore[reportAny]
            return _manage("migrate", "--plan") if plan else prepare(contract)
        elif verb == "job":
            name: str = asked.name  # pyright: ignore[reportAny]
            extra: list[str] = asked.extra  # pyright: ignore[reportAny]
            return job(contract, name, extra)
        elif verb == "upgrade":
            left: str = asked.left  # pyright: ignore[reportAny]
            reached: str = asked.reached  # pyright: ignore[reportAny]
            if contract.upgrades:
                return _manage("upgrade", "--from", left, "--to", reached)
            print(f"Nothing to upgrade between {left} and {reached}.")
    except No as refusal:
        print(f"{contract.description.name}: {refusal.headline}:", file=sys.stderr)
        for reason in refusal.reasons:
            print(f"  {reason}", file=sys.stderr)
        return REFUSED
    return 0
