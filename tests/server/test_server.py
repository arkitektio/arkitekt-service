"""The Django app every service installs: its settings blocks, its commands, its check."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.core.management import call_command

CONFIG = {
    "django": {"secret_key": "s3cret", "admin": {"username": "operator", "password": "hunter22", "email": "op@example.org"}},
    "postgres": {"host": "db", "db_name": "served_main", "username": "hub", "password": "pw", "sslmode": "require"},
    "redis": {"host": "redis"},
}


@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a config file and point the service at it; returns the writer."""
    monkeypatch.setenv("ARKITEKT_SERVICE", "tests.server.served")

    def write(document: dict) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(document))
        monkeypatch.setenv("ARKITEKT_CONFIG_FILE", str(path))
        return path

    write(CONFIG)
    return write


def test_the_settings_are_read_from_the_file_with_the_environment_over_it(config, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.server.served import Settings

    settings = Settings()
    assert settings.postgres.db_name == "served_main" and settings.postgres.port == 5432
    assert settings.redis.channel_prefix == "served", "a service's own key on a shared block"
    assert settings.django.debug is False

    monkeypatch.setenv("DJANGO__DEBUG", "true")
    assert Settings().django.debug is True


@pytest.mark.django_db
def test_ensureadmin_creates_the_configured_account_once(config, capsys: pytest.CaptureFixture[str]) -> None:
    call_command("ensureadmin")
    user = get_user_model().objects.get(username="operator")
    assert user.is_superuser and user.check_password("hunter22") and user.email == "op@example.org"

    user.set_password("changed-by-hand")
    user.save()
    call_command("ensureadmin")
    assert "already exists" in capsys.readouterr().out
    assert get_user_model().objects.get(username="operator").check_password("changed-by-hand"), "an account that is there is left alone"


@pytest.mark.django_db
def test_ensureadmin_has_nothing_to_do_without_an_admin(config, capsys: pytest.CaptureFixture[str]) -> None:
    config({**CONFIG, "django": {"secret_key": "s3cret"}})
    call_command("ensureadmin")
    assert "no account to create" in capsys.readouterr().out
    assert not get_user_model().objects.exists()


def test_validate_settings_prints_the_config_with_its_secrets_masked(config, capsys: pytest.CaptureFixture[str]) -> None:
    call_command("validate_settings")
    out = capsys.readouterr().out
    assert "Configuration valid" in out and "db_name: 'served_main'" in out
    assert "hunter22" not in out and "s3cret" not in out and "'pw'" not in out
    assert "password: **** (len=8)" in out


def test_validate_settings_refuses_a_config_the_release_cannot_start_with(config, capsys: pytest.CaptureFixture[str]) -> None:
    config({key: value for key, value in CONFIG.items() if key != "postgres"})
    with pytest.raises(SystemExit) as stopped:
        call_command("validate_settings")
    assert stopped.value.code == 1
    assert "postgres: Field required" in capsys.readouterr().err


def test_a_key_the_release_does_not_read_is_said_and_fails_only_when_strict(config, capsys: pytest.CaptureFixture[str]) -> None:
    config({**CONFIG, "django": {**CONFIG["django"], "debgu": True}})
    call_command("validate_settings")
    assert "not read: django.debgu" in capsys.readouterr().out

    with pytest.raises(SystemExit) as stopped:
        call_command("validate_settings", "--strict")
    assert stopped.value.code == 78


def test_an_open_block_passes_its_extras_on_unjudged(config, capsys: pytest.CaptureFixture[str]) -> None:
    """``postgres.sslmode`` is a driver option, not a misspelling."""
    call_command("validate_settings", "--strict")
    assert "not read" not in capsys.readouterr().out


def test_migrate_warns_about_what_the_release_does_not_read(config) -> None:
    config({**CONFIG, "django": {**CONFIG["django"], "debgu": True}})
    warnings = [message for message in run_checks() if message.id == "arkitekt.W001"]
    assert [message.msg for message in warnings] == ["The configuration sets `django.debgu`, which this release does not read."]


def test_a_process_that_is_no_service_is_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARKITEKT_SERVICE", raising=False)
    assert [message for message in run_checks() if (message.id or "").startswith("arkitekt.")] == []


def _declaring(monkeypatch: pytest.MonkeyPatch, upgrades: dict) -> None:
    import dataclasses

    from tests.server import served

    monkeypatch.setattr(served, "contract", dataclasses.replace(served.contract, upgrades=upgrades))


def test_a_move_runs_the_upgrades_of_every_major_it_crosses_into_in_order(config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """From 5 to 7 is the upgrade into 6, then the one into 7; the one into 8 is not its."""
    ran: list[int] = []
    _declaring(monkeypatch, {7: lambda: ran.append(7), 6: lambda: ran.append(6), 8: lambda: ran.append(8)})

    call_command("upgrade", "--from", "5.2.0", "--to", "7.0.1")

    assert ran == [6, 7]
    assert "6, 7" in capsys.readouterr().out


def test_a_move_within_a_major_or_back_runs_nothing(config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[int] = []
    _declaring(monkeypatch, {6: lambda: ran.append(6)})

    call_command("upgrade", "--from", "6.0.0", "--to", "6.3.1")
    call_command("upgrade", "--from", "6.0.0", "--to", "5.2.0")

    assert ran == []
    assert "Nothing to upgrade" in capsys.readouterr().out


def test_an_upgrade_that_fails_fails_the_command(config, monkeypatch: pytest.MonkeyPatch) -> None:
    """The installer has to hear it: the previous server is started again on a non-zero exit."""

    def broken() -> None:
        raise RuntimeError("row 3 has no organization")

    _declaring(monkeypatch, {6: broken})
    with pytest.raises(RuntimeError, match="row 3"):
        call_command("upgrade", "--from", "5.2.0", "--to", "6.0.0")


def test_something_that_is_no_version_is_refused(config, monkeypatch: pytest.MonkeyPatch) -> None:
    """A label the installer could not read is not guessed at."""
    from django.core.management.base import CommandError

    _declaring(monkeypatch, {6: lambda: None})
    with pytest.raises(CommandError, match="not a version"):
        call_command("upgrade", "--from", "latest", "--to", "6.0.0")
