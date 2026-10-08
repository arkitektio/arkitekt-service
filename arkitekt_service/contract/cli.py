"""The verbs an installer runs in a service's image: ``arkitekt-service <verb>``.

``describe``
    Prints the service's :class:`~arkitekt_service.contract.description.Description` as JSON. Needs no
    config. It is the image's own command: an installer asks by running the image with none, and
    knows nothing else of what is inside. Whoever starts the image by hand gets the same, with a
    note on stderr saying what to run instead. An image that cannot answer this has no contract, and an installer treats it as it
    treated images before there was one.

``render [--facts FILE] [--overrides FILE]``
    Prints this release's config, as YAML, written from a hub's facts (``/hub/facts.yaml``)
    with what the hub's operator set laid over it (``/hub/overrides.yaml``, if there). The
    result is loaded the way the service loads it at start before it is printed, so what comes
    out is a config this release starts on.

``check [--config FILE]``
    Judges a config as it stands — the file the service would start on.

``serve`` / ``debug``
    Becomes the service: the process the service declared as how it is started, for production
    or for development. Nothing is prepared first — that is ``run migrate``, a job of its own.

``run JOB [ARGS...]``
    Runs one of the service's jobs by name (``describe`` lists them). ``migrate`` is every
    service's: it waits for the database, applies the release's migrations, then runs the jobs
    the service named as its setup (an admin account, seeded rows), all in one process. An
    installer runs it once per build, before the first start and before an update's — which
    is why a service's own start does nothing but serve. ``plan`` lists the migrations that
    would run and runs nothing; ``superuser`` creates an account for the service's admin from
    ``DJANGO_SUPERUSER_USERNAME``, ``_PASSWORD`` and ``_EMAIL``; the rest are the
    service's own.

``standalone [--debug]``
    ``run migrate``, then ``serve`` (or ``debug``): the whole of it, for whoever runs one image
    on its own. An installer never does this: it prepares once, and starts as often as it
    likes.

Exit codes: ``0`` done. ``78`` (``EX_CONFIG``) is this release's own no — facts it cannot be
configured from, an override it does not read, an invalid config — with the reason on stderr,
and nothing on stdout. Anything else is the command failing.
"""

from __future__ import annotations

import argparse
import os
import runpy
import shlex
import sys
import tempfile
import typing
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from arkitekt_service.contract.contract import MIGRATE, PLAN, SUPERUSER, Contract, Refused, Start, load
from arkitekt_service.contract.description import Description
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


def run(contract: Contract, name: str, extra: Sequence[str]) -> int:
    """Run one of the service's jobs by name, with whatever was passed after it."""
    if name == MIGRATE:
        return prepare(contract)
    if name == PLAN:
        return manage("migrate", "--plan", *extra)
    if name == SUPERUSER:
        # From the environment, which is what `--noinput` reads: a password on a command
        # line is visible to every process on the machine for as long as it runs.
        return manage("createsuperuser", "--noinput", *extra)
    declared = contract.jobs.get(name)
    if declared is None:
        offered = ", ".join(contract.said().jobs)
        raise No(f"there is no job `{name}`", [f"this release offers: {offered}"])
    return manage(*declared.manage, *extra)


def become(start: Start) -> int:
    """Replace this process with the service's own: what a container of the image then is."""
    os.environ.update(start.environment)
    os.execvp(start.command[0], list(start.command))


def standalone(contract: Contract, debug: bool) -> int:
    """Prepare the database, then serve: nothing is served on a database that was not prepared."""
    code = prepare(contract)
    if code != 0:
        return code
    return become(contract.debug if debug else contract.serve)


def notice(said: Description) -> None:
    """Say, beside the description, what it is for and what to run instead.

    ``describe`` is the image's own command, so it is what somebody who simply starts the image
    gets: a page of JSON, and a container that stops. This goes to stderr — an installer reads
    stdout, and reads nothing but the description there — and says what they wanted to run.
    """
    lines = [
        "",
        f"This is the {said.name} service of an Arkitekt hub. Started with no command, its image only",
        "describes itself (above) and stops: that is how an installer such as konstruktor asks",
        "what it is, before it writes anything. Nothing is being served.",
        "",
        "To run it yourself:",
        "",
        f"  {'arkitekt-service standalone':<36}  prepare its database, then serve",
        f"  {'arkitekt-service standalone --debug':<36}  the same, with the development server",
        "",
        "Or one step at a time, as an installer does:",
        "",
    ]
    lines += [f"  {shlex.join(job.command):<36}  {job.summary}".rstrip() for job in said.jobs.values()]
    lines.append(f"  {shlex.join(said.serve):<36}  Serve, and nothing else.")
    lines.append(f"  {shlex.join(said.debug):<36}  The same, with the development server.")
    lines += ["", "Each needs the service's config at /workspace/config.yaml, which an installer writes too.", ""]
    print("\n".join(lines), file=sys.stderr)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one verb; the exit code is its answer."""
    parser = argparse.ArgumentParser(prog="arkitekt-service", description="What this service's image answers a hub's installer.")
    verbs = parser.add_subparsers(dest="verb")
    verbs.add_parser("describe", help="What the service is, needs and offers, and what to run in its image, as JSON. The default.")
    rendering = verbs.add_parser("render", help="This release's config, from a hub's facts.")
    rendering.add_argument("--facts", type=Path, default=Path(FACTS))
    rendering.add_argument("--overrides", type=Path, default=Path(OVERRIDES))
    checking = verbs.add_parser("check", help="Whether this release reads a config as written.")
    checking.add_argument("--config", type=Path, default=None)
    verbs.add_parser("serve", help="Serve, and nothing else.")
    verbs.add_parser("debug", help="Serve with the development server, and nothing else.")
    running = verbs.add_parser("run", help="One of the service's jobs, by name: migrate, plan, and its own.")
    running.add_argument("name")
    running.add_argument("extra", nargs=argparse.REMAINDER, help="Passed on to the job.")
    alone = verbs.add_parser("standalone", help="Prepare the database, then serve: one image, run on its own.")
    alone.add_argument("--debug", action="store_true", help="Serve with the development server.")
    asked = parser.parse_args(arguments)

    # The service is the code in the working directory: its contract's module and its
    # `manage.py` are found from there. `python -m` looks there by itself; a command does not.
    here = os.getcwd()
    if here not in sys.path:
        sys.path.insert(0, here)
    contract = load()
    verb: str = asked.verb or "describe"  # pyright: ignore[reportAny]  argparse's namespace
    try:
        if verb == "describe":
            said = contract.said()
            print(said.model_dump_json(indent=2))
            notice(said)
        elif verb == "render":
            facts: Path = asked.facts  # pyright: ignore[reportAny]
            overrides: Path = asked.overrides  # pyright: ignore[reportAny]
            print(yaml.safe_dump(render(contract, facts, overrides), sort_keys=False), end="")
        elif verb == "check":
            given: Path | None = asked.config  # pyright: ignore[reportAny]
            path = given or Path(os.environ.get(CONFIG_FILE, "config.yaml"))
            judge(contract, _document(path, "the config"))
        elif verb == "serve":
            return become(contract.serve)
        elif verb == "debug":
            return become(contract.debug)
        elif verb == "run":
            name: str = asked.name  # pyright: ignore[reportAny]
            extra: list[str] = asked.extra  # pyright: ignore[reportAny]
            return run(contract, name, extra)
        elif verb == "standalone":
            debug: bool = asked.debug  # pyright: ignore[reportAny]
            return standalone(contract, debug)
    except No as refusal:
        print(f"{contract.description.name}: {refusal.headline}:", file=sys.stderr)
        for reason in refusal.reasons:
            print(f"  {reason}", file=sys.stderr)
        return REFUSED
    return 0
