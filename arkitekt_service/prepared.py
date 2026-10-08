"""Prove what a service's ``migrate`` job does to a database, in the service's own test suite.

What an installer relies on and no schema says: that the migrations are the models', that
every job the contract offers is a command the image has, and that the setup — run again for
every build — changes nothing the second time. Each is one call::

    # tests/test_prepared.py
    import pytest
    from arkitekt_service import prepared
    from mikro_server.contract import contract


    @pytest.mark.django_db
    def test_every_model_change_has_its_migration() -> None:
        prepared.migrations_are_committed()


    def test_every_job_is_a_command_of_this_service() -> None:
        prepared.jobs_are_commands(contract)


    @pytest.mark.django_db
    def test_the_setup_is_safe_to_run_again() -> None:
        prepared.setup_runs_again(contract)

The rules these hold a service to are in ``docs/migrations-and-jobs.md``.
"""

from __future__ import annotations

import io

from django.core.management import call_command, get_commands

from arkitekt_service.contract.contract import Contract


def migrations_are_committed() -> None:
    """No model differs from what the committed migrations make of it."""
    said = io.StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", stdout=said, stderr=said)
    except SystemExit as stopped:
        if stopped.code:
            raise AssertionError(f"a model changed without its migration; run `python manage.py makemigrations` and commit it:\n{said.getvalue()}") from None


def jobs_are_commands(contract: Contract) -> None:
    """Every job the contract offers names a ``manage.py`` command this service has."""
    known = get_commands()
    missing = {name: job.manage[0] for name, job in contract.jobs.items() if job.manage[0] not in known}
    if contract.upgrades and "upgrade" not in known:
        missing["upgrade"] = "upgrade"
    assert not missing, f"jobs that name a command this service does not have: {missing}"


def setup_runs_again(contract: Contract) -> None:
    """The setup, as ``migrate`` runs it after the migrations, twice: an installer runs it for every build."""
    for attempt in ("first", "second"):
        for name in contract.setup:
            try:
                call_command(*contract.jobs[name].manage, stdout=io.StringIO())
            except SystemExit as stopped:
                if stopped.code:
                    raise AssertionError(f"setup job `{name}` failed on its {attempt} run (exit {stopped.code})") from None
