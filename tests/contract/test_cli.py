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
    "database": {"host": "db", "name": "example", "username": "hub", "password": "pw"},
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
    # What to run to prepare it is the image's to say; its start is the image's own command.
    assert said["prepare"] == "migrate"
    assert said["jobs"]["migrate"]["command"] == ["python", "-m", "arkitekt_service", "migrate"]
    assert said["sidecars"] == []
    assert said["needs"]["storage"] == ["media"] and said["needs"]["instance_key"] is True
    assert said["offers"]["endpoints"] == {"rekuest_hook": "_rekuest/hook"}
    assert said["requires"] == {"rekuest": ">=6"} and said["upgrade_from"] == "1.0.0"


def test_render_writes_the_releases_config_from_the_hubs_facts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    facts = written(tmp_path, "facts.yaml", FACTS)

    assert cli.main(["render", "--facts", str(facts), "--overrides", str(tmp_path / "none.yaml")]) == 0

    config = yaml.safe_load(capsys.readouterr().out)
    assert config["postgres"] == {"host": "db", "db_name": "example", "password": "pw"}
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
    without_database = {key: value for key, value in FACTS.items() if key != "database"}
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


def test_a_release_without_upgrades_has_nothing_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["upgrade", "--from", "1.0.0", "--to", "2.0.0"]) == 0
    assert "Nothing to upgrade" in capsys.readouterr().out


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
    assert cli.main(["migrate"]) == 0
    # It waits for a connection and no longer: nothing here is worth a fixed delay. A job
    # that is not part of the setup (`reindex`) is not run.
    assert ran == [["wait_for_database", "-s", "0"], ["migrate", "--noinput"], ["ensureadmin"], ["ensurerepos", "--quiet"]]

    ran.clear()
    monkeypatch.setattr(cli, "manage", lambda *step: ran.append(list(step)) or (3 if "migrate" in step else 0))
    assert cli.main(["migrate"]) == 3
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

    assert said["sidecars"] == [{"name": "takt", "image": "{repository}-takt:{tag}", "summary": ""}]
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
    assert said["jobs"]["ensureadmin"] == {"command": ["python", "-m", "arkitekt_service", "job", "ensureadmin"], "summary": "Create the operator account", "includes": []}
    assert set(said["jobs"]) == {"migrate", "ensureadmin", "ensurerepos", "reindex"}


def test_a_job_is_run_by_its_name_with_what_was_passed_after_it(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _with_jobs(monkeypatch)
    ran: list[list[str]] = []
    monkeypatch.setattr(cli, "manage", lambda *step: ran.append(list(step)) or 0)

    assert cli.main(["job", "ensurerepos"]) == 0
    assert cli.main(["job", "reindex", "--since", "2026-01-01"]) == 0
    assert ran == [["ensurerepos", "--quiet"], ["reindex", "--since", "2026-01-01"]]

    assert cli.main(["job", "nope"]) == cli.REFUSED
    assert "ensureadmin, ensurerepos, reindex" in capsys.readouterr().err


def test_a_setup_can_only_name_jobs_the_service_declares() -> None:
    import dataclasses

    from arkitekt_service.contract import Job

    from tests.contract import example

    with pytest.raises(ValueError, match="ensureadmin"):
        dataclasses.replace(example.contract, setup=("ensureadmin",))
    with pytest.raises(ValueError, match="migrate"):
        dataclasses.replace(example.contract, jobs={"migrate": Job(("migrate",))})
