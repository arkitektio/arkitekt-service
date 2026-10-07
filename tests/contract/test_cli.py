"""The verbs, asked the way an installer asks them: by the command line, judged by exit code."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from arkitekt_service.contract import cli

FACTS = {
    "me": {"name": "example", "path": "example", "url": "http://example:80/example", "identifier": "live.arkitekt.example", "secret_key": "s3cret"},
    "hub": {"auth": {"issuers": []}},
    "databases": {"main": {"host": "db", "name": "example_main", "username": "hub", "password": "pw"}},
    "peers": {"rekuest": {"url": "http://rekuest:80/rekuest", "offers": {"agent": "http://rekuest-takt:8080/rekuest"}}},
}


@pytest.fixture(autouse=True)
def the_example_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARKITEKT_SERVICE", "tests.contract.example")
    monkeypatch.delenv("ARKITEKT_CONFIG_FILE", raising=False)


def written(tmp_path: Path, name: str, document: object) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_describe_says_what_the_service_needs_before_it_has_any_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["describe"]) == 0

    said = json.loads(capsys.readouterr().out)
    assert said["contract"] == 2 and said["name"] == "example"
    assert said["identifier"] == "live.arkitekt.example"
    # What to run to prepare it is the image's to say.
    assert said["prepare"] == "migrate"
    assert said["jobs"]["migrate"]["command"] == ["arkitekt-service", "run", "migrate"]
    # How it is started is the image's to say too, for both of the ways it is run: through
    # this command, which becomes what the service declared.
    assert said["serve"] == ["arkitekt-service", "serve"] and said["debug"] == ["arkitekt-service", "debug"]
    assert said["sidecars"] == []
    assert said["needs"]["storage"] == ["media"] and said["needs"]["instance_key"] is True
    assert said["offers"]["endpoints"] == {"rekuest_hook": "_rekuest/hook"}
    assert said["requires"] == {"rekuest": ">=6"} and said["upgrade_from"] == "1.0.0"


def test_render_writes_the_releases_config_from_the_hubs_facts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    facts = written(tmp_path, "facts.yaml", FACTS)

    assert cli.main(["render", "--facts", str(facts), "--overrides", str(tmp_path / "none.yaml")]) == 0

    config = yaml.safe_load(capsys.readouterr().out)
    assert config["postgres"] == {"host": "db", "db_name": "example_main", "password": "pw"}
    assert config["django"]["force_script_name"] == "example"
    # Wired to a peer by what the peer offers, not by a name the installer knew.
    assert config["rekuest_hook"] == {"rekuest_url": "http://rekuest-takt:8080/rekuest"}


def test_what_the_operator_set_is_laid_over_and_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    facts = written(tmp_path, "facts.yaml", FACTS)
    overrides = written(tmp_path, "overrides.yaml", {"django": {"debug": True}, "postgres": {"sslmode": "require"}})

    assert cli.main(["render", "--facts", str(facts), "--overrides", str(overrides)]) == 0

    config = yaml.safe_load(capsys.readouterr().out)
    assert config["django"] == {"secret_key": "s3cret", "debug": True, "force_script_name": "example"}
    assert config["postgres"]["sslmode"] == "require" and config["postgres"]["host"] == "db"


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"django": {"debgu": True}}, "django.debgu"),
        ({"django": {"debug": "perhaps"}}, "django.debug"),
    ],
)
def test_an_override_this_release_does_not_read_is_refused_by_name(tmp_path: Path, capsys: pytest.CaptureFixture[str], overrides: dict[str, object], named: str) -> None:
    facts = written(tmp_path, "facts.yaml", FACTS)

    assert cli.main(["render", "--facts", str(facts), "--overrides", str(written(tmp_path, "overrides.yaml", overrides))]) == cli.REFUSED

    said = capsys.readouterr()
    assert said.out == "", "a refusal prints no config"
    assert named in said.err


def test_facts_the_release_cannot_be_configured_from_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    without_database = {key: value for key, value in FACTS.items() if key != "databases"}
    assert cli.main(["render", "--facts", str(written(tmp_path, "facts.yaml", without_database))]) == cli.REFUSED
    assert "it needs a database" in capsys.readouterr().err

    # A fact this contract does not know: an installer newer than the image.
    assert cli.main(["render", "--facts", str(written(tmp_path, "facts.yaml", {**FACTS, "mesh": {"node": "n"}}))]) == cli.REFUSED
    assert "mesh" in capsys.readouterr().err


def test_check_judges_a_config_as_it_stands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = {"django": {"secret_key": "s"}, "postgres": {"host": "db", "db_name": "x", "password": "p"}, "somebody_elses": {"x": 1}}
    assert cli.main(["check", "--config", str(written(tmp_path, "good.yaml", good))]) == 0

    stale = {**good, "rekuest_hook": {"rekuest_url": "http://r", "secret": "old"}}
    assert cli.main(["check", "--config", str(written(tmp_path, "stale.yaml", stale))]) == cli.REFUSED
    assert "rekuest_hook.secret" in capsys.readouterr().err

    # A key read under its former name is the release's to rename: said elsewhere, not refused.
    renamed = {**good, "rekuest_hook": {"agent_url": "http://r"}}
    assert cli.main(["check", "--config", str(written(tmp_path, "renamed.yaml", renamed))]) == 0


def test_an_image_that_does_not_say_where_its_contract_is_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARKITEKT_SERVICE")
    with pytest.raises(LookupError):
        cli.main(["describe"])


def test_the_shared_blocks_are_written_from_the_same_facts() -> None:
    from arkitekt_service.contract import Facts, Refused, blocks

    facts = Facts.model_validate(
        {
            **FACTS,
            "redis": {"host": "redis"},
            "storage": {"host": "rustfs", "port": 9000, "access_key": "a", "secret_key": "s", "buckets": {"media": "examplemedia"}},
        }
    )
    assert blocks.server(facts).keys() == {"django", "postgres", "redis", "authentikate"}
    assert blocks.datalayer(facts, "media")["media"] == {"bucket": "examplemedia"}
    assert blocks.rekuest_hook(facts) == {"rekuest_url": "http://rekuest-takt:8080/rekuest"}
    with pytest.raises(Refused, match="zarr"):
        blocks.datalayer(facts, "media", "zarr")
    with pytest.raises(Refused, match="instance key"):
        blocks.instance(facts)


def test_migrate_prepares_the_database_in_order_and_stops_at_the_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait, migrate, then the jobs the service named as its setup — and a step that fails is the answer."""
    _with_jobs(monkeypatch)

    ran: list[list[str]] = []
    monkeypatch.setattr(cli, "manage", lambda *step: ran.append(list(step)) or 0)
    assert cli.main(["run", "migrate"]) == 0
    # It waits for a connection and no longer: nothing here is worth a fixed delay. A job
    # that is not part of the setup (`reindex`) is not run.
    assert ran == [["wait_for_database", "-s", "0"], ["migrate", "--noinput"], ["ensureadmin"], ["ensurerepos", "--quiet"]]

    ran.clear()
    monkeypatch.setattr(cli, "manage", lambda *step: ran.append(list(step)) or (3 if "migrate" in step else 0))
    assert cli.main(["run", "migrate"]) == 3
    assert [step[0] for step in ran] == ["wait_for_database", "migrate"], "nothing runs after a failed migration"


def test_the_steps_of_a_preparation_share_one_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each `manage.py` command runs in this interpreter, and its exit code comes back."""
    (tmp_path / "manage.py").write_text(
        "import os, sys\n"
        "with open('ran', 'a') as log:\n"
        "    log.write(f'{os.getpid()} {sys.argv[1:]}\\n')\n"
        "if sys.argv[1] == 'fails':\n"
        "    sys.exit(4)\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.manage("first", "--flag") == 0
    assert cli.manage("fails") == 4
    assert cli.manage("third") == 0

    here = str(os.getpid())
    assert (tmp_path / "ran").read_text().splitlines() == [
        f"{here} ['first', '--flag']",
        f"{here} ['fails']",
        f"{here} ['third']",
    ]


def test_a_sidecars_image_is_named_from_the_services_own() -> None:
    from arkitekt_service.contract import Description, Sidecar

    said = Description(name="pair", identifier="live.arkitekt.pair", sidecars=[Sidecar(name="takt", image="{repository}-takt:{tag}")]).model_dump(mode="json")

    assert said["sidecars"] == [{"name": "takt", "image": "{repository}-takt:{tag}", "summary": "", "optional": False}]
    # Written by hand, a description names no job: they are the contract's to say.
    assert said["jobs"] == {} and said["prepare"] is None


def _with_jobs(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    import dataclasses

    from arkitekt_service.contract import Job
    from arkitekt_service.contract import contract as declared

    from tests.contract import example

    with_jobs = dataclasses.replace(
        example.contract,
        jobs={"ensureadmin": Job(("ensureadmin",), "Create the operator account"), "ensurerepos": Job(("ensurerepos", "--quiet"), "Clone the repositories"), "reindex": Job(("reindex",))},
        setup=("ensureadmin", "ensurerepos"),
    )
    monkeypatch.setattr(declared, "load", lambda: with_jobs)
    monkeypatch.setattr(cli, "load", lambda: with_jobs)
    return with_jobs


def test_describe_lists_the_jobs_and_which_of_them_migrate_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Each job is the command that runs it; ``migrate`` says which it includes, and is the one that prepares."""
    _with_jobs(monkeypatch)
    assert cli.main(["describe"]) == 0

    said = json.loads(capsys.readouterr().out)
    assert said["prepare"] == "migrate"
    assert said["jobs"]["migrate"]["includes"] == ["ensureadmin", "ensurerepos"]
    assert said["jobs"]["ensureadmin"] == {"command": ["arkitekt-service", "run", "ensureadmin"], "summary": "Create the operator account", "includes": []}
    # `plan` is every service's; `upgrade` is only there for a release that ships one.
    assert set(said["jobs"]) == {"migrate", "plan", "superuser", "ensureadmin", "ensurerepos", "reindex"}
    assert said["jobs"]["plan"]["command"] == ["arkitekt-service", "run", "plan"]
    assert said["render"] == ["arkitekt-service", "render"]


def test_whoever_starts_the_image_by_hand_is_told_what_to_run_instead(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`describe` is the image's own command. An installer reads stdout, which stays the
    description and nothing else; a person reads the note beside it."""
    _with_jobs(monkeypatch)
    assert cli.main(["describe"]) == 0

    printed = capsys.readouterr()
    assert json.loads(printed.out)["name"] == "example"
    note = printed.err
    assert "an installer such as konstruktor" in note and "Nothing is being served" in note
    # The one thing most people want comes first; then the steps an installer takes.
    assert note.index("arkitekt-service standalone") < note.index("arkitekt-service run migrate")
    assert "arkitekt-service serve" in note and "arkitekt-service debug" in note
    assert "arkitekt-service run ensureadmin" in note and "Create the operator account" in note


def test_a_job_is_run_by_its_name_with_what_was_passed_after_it(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _with_jobs(monkeypatch)
    ran: list[list[str]] = []
    monkeypatch.setattr(cli, "manage", lambda *step: ran.append(list(step)) or 0)

    assert cli.main(["run", "ensurerepos"]) == 0
    assert cli.main(["run", "reindex", "--since", "2026-01-01"]) == 0
    assert cli.main(["run", "plan"]) == 0
    # The account's name and password come from the environment, never from here.
    assert cli.main(["run", "superuser"]) == 0
    assert ran == [["ensurerepos", "--quiet"], ["reindex", "--since", "2026-01-01"], ["migrate", "--plan"], ["createsuperuser", "--noinput"]]

    assert cli.main(["run", "nope"]) == cli.REFUSED
    assert "migrate, plan, superuser, ensureadmin, ensurerepos, reindex" in capsys.readouterr().err
    # An upgrade is only a job of a release that ships one.
    assert cli.main(["run", "upgrade", "--from", "1", "--to", "2"]) == cli.REFUSED


def test_a_setup_can_only_name_jobs_the_service_declares() -> None:
    import dataclasses

    from arkitekt_service.contract import Job

    from tests.contract import example

    with pytest.raises(ValueError, match="ensureadmin"):
        dataclasses.replace(example.contract, setup=("ensureadmin",))
    for reserved in ("migrate", "plan", "upgrade", "superuser"):
        with pytest.raises(ValueError, match=reserved):
            dataclasses.replace(example.contract, jobs={reserved: Job((reserved,))})


def test_a_release_that_ships_an_upgrade_offers_it_as_a_job() -> None:
    import dataclasses

    from tests.contract import example

    assert "upgrade" not in example.contract.said().jobs
    shipped = dataclasses.replace(example.contract, upgrades=True).said()
    assert shipped.jobs["upgrade"].command == ["arkitekt-service", "run", "upgrade"]


def test_the_command_finds_the_service_in_the_directory_it_is_run_in(tmp_path: Path) -> None:
    """As it is run in an image: the installed command, in the service's own directory, which
    is on nobody's path. `python -m` would find the service there by itself; a command has to
    look."""
    import shutil
    import subprocess
    import sys

    command = shutil.which("arkitekt-service", path=str(Path(sys.executable).parent))
    assert command, "the command is installed beside the interpreter"
    (tmp_path / "somewhere_else.py").write_text(
        "from pydantic_settings import BaseSettings\n"
        "from arkitekt_service.contract import Contract, Description, Start\n"
        "class Settings(BaseSettings): ...\n"
        "contract = Contract(description=Description(name='elsewhere', identifier='live.arkitekt.elsewhere'), settings=Settings, render=lambda facts: {},\n"
        "    serve=Start(('echo', 'serving', 'elsewhere')), debug=Start(('echo', 'debugging')))\n"
    )
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    ran = subprocess.run([command, "describe"], cwd=tmp_path, env={**environment, "ARKITEKT_SERVICE": "somewhere_else"}, capture_output=True, text=True, check=False)

    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["name"] == "elsewhere"
    assert "Nothing is being served" in ran.stderr

    # With no verb at all it says what it is too: that is what an image's own command relies on.
    bare = subprocess.run([command], cwd=tmp_path, env={**environment, "ARKITEKT_SERVICE": "somewhere_else"}, capture_output=True, text=True, check=False)
    assert json.loads(bare.stdout)["name"] == "elsewhere"

    # And `serve` becomes what the service declared: this process is replaced by it.
    served = subprocess.run([command, "serve"], cwd=tmp_path, env={**environment, "ARKITEKT_SERVICE": "somewhere_else"}, capture_output=True, text=True, check=False)
    assert served.stdout.strip() == "serving elsewhere"


def test_serve_and_debug_become_what_the_service_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is prepared on the way: starting is one thing, preparing another."""
    became: list[object] = []
    monkeypatch.setattr(cli, "become", lambda start: became.append(start) or 0)
    monkeypatch.setattr(cli, "manage", lambda *step: pytest.fail(f"`{step}` ran before the service was started"))

    assert cli.main(["serve"]) == 0
    assert cli.main(["debug"]) == 0
    assert [start.command[0] for start in became] == ["daphne", "python"]
    assert became[1].environment == {"EXAMPLE_DEBUG": "1"}


def test_standalone_prepares_and_then_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole of it, in order, for whoever runs one image on its own."""
    _with_jobs(monkeypatch)
    did: list[str] = []
    monkeypatch.setattr(cli, "manage", lambda *step: did.append(step[0]) or 0)
    monkeypatch.setattr(cli, "become", lambda start: did.append(f"became {start.command[0]}") or 0)

    assert cli.main(["standalone"]) == 0
    assert did == ["wait_for_database", "migrate", "ensureadmin", "ensurerepos", "became daphne"]

    did.clear()
    assert cli.main(["standalone", "--debug"]) == 0
    assert did[-1] == "became python"


def test_standalone_serves_nothing_on_a_database_it_could_not_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_jobs(monkeypatch)
    monkeypatch.setattr(cli, "manage", lambda *step: 3 if step[0] == "migrate" else 0)
    monkeypatch.setattr(cli, "become", lambda start: pytest.fail("served on a database that was not prepared"))

    assert cli.main(["standalone"]) == 3


def test_a_service_has_one_database_unless_it_names_others() -> None:
    from arkitekt_service.contract import Needs

    assert Needs().databases == ["main"]
    assert Needs(databases=["main", "events"]).databases == ["main", "events"]
    assert Needs(databases=[]).databases == [], "a service that keeps nothing in Postgres"


@pytest.mark.parametrize("name", ["Main", "my-db", "1st", "with space", 'drop"table', "", "ümlaut", "a" * 64])
def test_a_database_name_postgres_would_have_to_quote_is_refused(name: str) -> None:
    from arkitekt_service.contract import Needs

    with pytest.raises(ValueError, match="not a name Postgres takes unquoted"):
        Needs(databases=[name])


def test_a_database_cannot_be_named_twice() -> None:
    from arkitekt_service.contract import Needs

    with pytest.raises(ValueError, match="named twice: main"):
        Needs(databases=["main", "main"])


def test_a_block_is_written_for_the_database_asked_for_by_name() -> None:
    from arkitekt_service.contract import Facts, Refused, blocks

    facts = Facts.model_validate({**FACTS, "databases": {**FACTS["databases"], "events": {"host": "db", "name": "example_events", "username": "hub", "password": "pw"}}})
    assert blocks.postgres(facts)["db_name"] == "example_main"
    assert blocks.postgres(facts, "events")["db_name"] == "example_events"
    with pytest.raises(Refused, match="called `archive`"):
        blocks.postgres(facts, "archive")
